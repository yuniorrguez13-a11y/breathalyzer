#!/usr/bin/env python3
"""Build clean Breathalyzer release zips. Never game files, keys, saves, logs, anything made from the game, or mods by
other people.

    python tools/make_release.py --edition players --version 0.2.0-beta.1   both player zips (see below)
    python tools/make_release.py --edition core                         breathalyzer-<VERSION>.zip: launcher, no mods
    python tools/make_release.py --edition plus                         breathalyzer-plus-<VERSION>.zip: + our own mods
    python tools/make_release.py                                        breathalyzer-source-<VERSION>.zip (developers)
    python tools/make_release.py --with-binaries                        source zip + the DLLs build_native.py built
    python tools/make_release.py --dry-run                              only list what would go in; write nothing

The editions (docs/release.md):

- core: what players download when they want no mods. "Start Breathalyzer.exe" (tools/packaging/build_starter.py),
  "READ ME FIRST.txt" and app/: app/runtime (our own Python and Tk, tools/packaging/build_runtime.py: players need no
  Python), app/tools, app/native, app/docs, app/assets, README, LICENSE, NOTICE, and modhost.dll (the loader, not a
  mod). Not the tests or tools/packaging. No DLL but modhost.dll and the runtime's.
- plus: the same, plus our own mods from tools/packaging/bundled-mods.toml (ready = true): each in
  app/bundled-mods/<name>/ with a mod.toml carrying the player title and description, and app/bundled-mods.json, the
  list the mod picker shows (title, description, on or off by default, why, tags). Only ids starting with
  botw-mod-loader. or giruka., only files git tracks or DLLs from build/native; never anything from out/.
- source: the repo as it is, for developers (what this tool made before the editions).

The player zips come from one commit (--commit, default HEAD), never from the working tree: git archive unpacks it
into out/packaging-work/src/<commit>/ and the DLLs are built from that copy (its own build_native.py and modmenu.py),
so other jobs' unfinished files never get in. The commit id is the first line of SHA256SUMS.txt (a '# ' comment) and
"commit" in bundled-mods.json. --overlay <file> lays one uncommitted file over the commit for a test build (each is
listed with its SHA-256; such zips are not for upload); --working-tree takes everything as it is (tests only). The
player zips leave out developer-only files: docs/PLAN.md, docs/release.md, make_release.py, check_clean.py and the
test drivers that start the game (*_ingame_check.py, *_items_check.py).

The runtime must match tools/packaging/runtime.lock.json file for file (SHA-256), and the starter
tools/packaging/starter.lock.json (built from today's start.c); a 64-bit window program that uses only kernel32 and
user32.

What goes in the source zip (an allow-list): files git tracks, and files git would commit under tools/, native/,
docs/, tests/, .github/, plus README.md, LICENSE, NOTICE.md, .gitignore and .cleanallow. git is needed: without it (or
when git fails) the tool refuses, because only git knows what .gitignore keeps out. Never out/, build/, upstream/ or
__pycache__, in any letter case. With --with-binaries: modhost.dll and the example mods from build/native, each
checked to be a 64-bit DLL with the export build_native.py gives it, never swgame.dll and never a file from the
Savage Wind folder (not even through a hard link). The player zips take their app/ files from the same list.

Checks before writing: tools/check_clean.py's rules on every file, plus: no user name, home folder path, repo path,
git e-mail or git name in any file or file name; no private keys, access tokens or key files; zip and gzip files are
opened and every file in them checked, other archives and compressed files are refused; no links or junctions (also
not in a folder above the file); no earlier release; no file over 20 MB. If one file fails, nothing is written and
every failure is listed. Checks after writing: the zip is opened again and every entry checked: names (no absolute
paths, no "..", only what was planned), content (same SHA-256 as planned, check_clean's rules again, the checks above).

It writes out/release/<name>-<version>.zip (everything in one folder of that name, with SHA256SUMS.txt) and
out/release/<name>-<version>.SHA256SUMS.txt next to it (the same list, plus the zip's own hash).
--out may be any folder outside the project, or one under out/ (never one the next release would pack).
With the same Python version, the same files always give a byte-identical zip: sorted names, fixed dates, fixed file
modes. (Python's zlib makes the compressed bytes, and Python 3.14 on Windows uses zlib-ng, so its zips differ.)

Exit code 0: written (or, with --dry-run, it would be). 1: refused (the reasons are listed). 2: bad arguments.
3: the files couldn't be written (a locked old zip, a full disk); the message says what is left.
"""
from __future__ import annotations

import argparse
import getpass
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_native as bn  # noqa: E402 (tools/build_native.py: the DLL targets and the PE reader)
import check_clean as cc  # noqa: E402 (tools/check_clean.py: the game-file rules)

REPO = Path(__file__).resolve().parent.parent
VERSION = "0.1.0-dev"
NAME = "breathalyzer"
# The editions: source (developers), core (the player zip without mods) and plus (with our own mods).
EDITIONS = ("source", "core", "plus")
PLAYER_EDITIONS = ("core", "plus")
EDITION_NAMES = {"source": f"{NAME}-source", "core": NAME, "plus": f"{NAME}-plus"}
OLD_NAMES = ("botw-mod-loader",)  # releases made before the rename: never packed again either
DEFAULT_OUT = REPO / "out" / "release"
DEFAULT_BUILD = bn.DEFAULT_OUT  # build/native
BUILD_PREFIX = "build/native"   # where the DLLs go in the release: the same place as in the repo, so the
                                # mod manager and `build_native.py install` find them after unzipping
SUMS = "SHA256SUMS.txt"
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_UNPACKED = 10 * MAX_FILE_BYTES  # all files in one zip or gzip file together
MAX_MEMBERS = 10000                 # files in one zip file
EXIT_WRITE_FAILED = 3

# The allow-list: these folders and top-level files (plus anything git tracks). Compared in lower case.
ROOT_DIRS = ("tools", "native", "docs", "tests", ".github", "assets")
ROOT_FILES = ("README.md", "LICENSE", "NOTICE.md", ".gitignore", ".cleanallow")
# Never, whatever git says (in any letter case: on Windows Build/ is build/): tool output, build output, the local
# Savage Wind clone, Python caches.
NEVER_TOP = {"out", "build", "upstream", "dist", ".git", ".venv", ".vscode"}
NEVER_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".git"}
NEVER_EXT = {".pyc", ".pyo"}
# This tool's own output, should it ever sit in an allowed folder.
RELEASE_FILE_RE = re.compile("(?:" + "|".join(re.escape(n) for n in (NAME, *OLD_NAMES)) + r")-.+\.(?:zip|"
                             + re.escape(SUMS) + r")(?:\.tmp)?", re.I)

# Archives and compressed files: by their first bytes, else by type. zip and gzip are opened and checked.
ARCHIVE_MAGIC = ((b"PK\x03\x04", "zip"), (b"PK\x05\x06", "zip"), (b"PK\x07\x08", "zip"), (b"\x1f\x8b", "gzip"),
                 (b"7z\xbc\xaf\x27\x1c", "7z"), (b"Rar!\x1a\x07", "rar"), (b"\x28\xb5\x2f\xfd", "zstd"),
                 (b"\xfd7zXZ\x00", "xz"), (b"MSCF\0\0\0\0", "cab"), (b"\x04\x22\x4d\x18", "lz4"),
                 (b"LZIP", "lzip"), (b"\x1f\x9d", "compress"))
BZIP2_RE = re.compile(rb"BZh[1-9](?:1AY&SY|\x17rE8P\x90)")
ARCHIVE_EXT = {".zip": "zip", ".gz": "gzip", ".tgz": "gzip", ".7z": "7z", ".rar": "rar", ".bz2": "bzip2",
               ".tbz": "bzip2", ".tbz2": "bzip2", ".xz": "xz", ".txz": "xz", ".lzma": "lzma", ".zst": "zstd",
               ".tzst": "zstd", ".lz4": "lz4", ".lz": "lzip", ".tar": "tar", ".cab": "cab", ".zipx": "zipx",
               ".whl": "wheel", ".jar": "jar", ".apk": "apk", ".iso": "disk image"}

# Ordinary secrets: key files by name, and private keys and access tokens by their shape. (Written so that this
# source itself doesn't match.)
SECRET_NAMES = {".env", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", "_netrc", ".pypirc",
                ".git-credentials"}
SECRET_EXT = {".pem", ".key", ".pfx", ".p12", ".ppk", ".jks", ".keystore", ".kdbx"}
SECRET_RE = re.compile(
    rb"PRIVATE KEY(?: BLOCK)?-{5}"                                            # PEM, OpenSSH and PGP private keys
    rb"|(?<![0-9A-Za-z_])gh[pousr]_[0-9A-Za-z]{36}"                           # GitHub tokens
    rb"|github_pat_[0-9A-Za-z_]{22,}"
    rb"|(?<![0-9A-Z])(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])"                   # AWS access keys
    rb"|(?<![0-9A-Za-z_-])sk-(?=[0-9A-Za-z_-]{0,200}?[0-9])[0-9A-Za-z_-]{32,}"  # OpenAI / Anthropic style API keys
    rb"|xox[abposr]-[0-9A-Za-z-]{10,}"                                        # Slack
    rb"|AIza[0-9A-Za-z_-]{35}"                                                # Google
    rb"|[rs]k_live_[0-9A-Za-z]{20,}"                                          # Stripe
    rb"|npm_[0-9A-Za-z]{36}"                                                  # npm
    rb"|pypi-AgE[0-9A-Za-z_-]{50,}")                                          # PyPI

VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,63}")
FIXED_DATE = (1980, 1, 1, 0, 0, 0)  # the earliest date a zip can hold
FILE_MODE = 0o100644
HOST_MARK = b"[modhost]"  # modhost.dll's log prefix: the real game library doesn't have it
GEN_EXPORT_RE = re.compile(r"f_[0-9a-f]{8,16}")  # a generated game function (check_clean's GEN_SYM_RE)
# Login names that are too common to mean "you" (CI machines, containers); they would flag ordinary words.
GENERIC_LOGINS = {"root", "runner", "runneradmin", "user", "admin", "administrator", "guest", "dev", "test",
                  "tester", "ubuntu", "vagrant", "docker", "default", "public", "owner"}
WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}

# ---- the player zips (core and plus)
APP = "app"                                   # everything but the starter and READ ME FIRST.txt (modman's REPO)
STARTER_NAME = "Start Breathalyzer.exe"
README_NAME = "READ ME FIRST.txt"
BACKUP_CMD = "Start Breathalyzer (backup).cmd"  # in app/: starts it without the starter (an antivirus holding it)
PACKAGING = "tools/packaging"
RUNTIME_LOCK = f"{PACKAGING}/runtime.lock.json"
STARTER_LOCK = f"{PACKAGING}/starter.lock.json"
STARTER_SOURCES = {"start.c": f"{PACKAGING}/starter/start.c", "breathalyzer.ico": "assets/breathalyzer.ico",
                   "build_starter.py": f"{PACKAGING}/build_starter.py"}
BUNDLED_TOML = f"{PACKAGING}/bundled-mods.toml"
BUNDLED_DIR = "bundled-mods"                  # app/bundled-mods/<name>/: one folder per bundled code mod
BUNDLED_JSON = "bundled-mods.json"            # app/bundled-mods.json: what the mod picker shows
READ_ME = {"core": f"{PACKAGING}/read-me-first-core.txt", "plus": f"{PACKAGING}/read-me-first-plus.txt"}
BACKUP_CMD_SRC = f"{PACKAGING}/start-backup.cmd"
PLAYER_SKIP_TOP = {"tests", ".github"}        # repo folders that stay out of the player zips
# developer-only files (they stay in the source zip): the build PC's notes, the release steps, the release and
# clean-check tools; and (PLAYER_SKIP_NAME) the test drivers that start the game
PLAYER_SKIP_FILES = {".gitignore", ".cleanallow", "docs/plan.md", "docs/release.md", "tools/make_release.py",
                     "tools/check_clean.py"}
PLAYER_SKIP_NAME = re.compile(r"[a-z0-9_]+_(?:ingame|items)_check\.py")
PLAYER_SKIP_DIRS = {"selftest"}              # the native self-tests (made-up "game code" check_clean blocks)
SNAPSHOT_DIR = "src"                          # out/packaging-work/src/<commit>: the player zips' files come from here
BELOW_NORMAL = 0x4000                         # BELOW_NORMAL_PRIORITY_CLASS: the DLL build stays out of the way
# (builds modmenu.dll from the snapshot's own tools/modmenu.py: argv = tools folder, build folder[, Savage Wind])
MODMENU_BUILD = ("import sys; sys.path.insert(0, sys.argv[1]); import modmenu; "
                 "print(modmenu.build_mod(sys.argv[3] if len(sys.argv) > 3 else None, sys.argv[2]))")
STARTER_IMPORTS = {"kernel32.dll", "user32.dll"}
RUNTIME_NEEDS = ("python.exe", "pythonw.exe", "LICENSE.txt")
OUR_IDS = ("botw-mod-loader.", "giruka.")     # bundled mods are ours: their ids start like this
MAX_TITLE, MAX_DESCRIPTION, MAX_TAG = 40, 80, 80  # (the game's Mods page cuts a description at 80 characters)
FONT_MISSING = set("{}|`^~%#")               # characters the game's menu font lacks (tools/modmenu.py)
BUNDLED_KEYS = {"id", "title", "description", "default", "why", "tags", "ready", "kind", "manifest", "dll", "folder",
                "build", "after_add", "developer", "egg"}


class ReleaseError(RuntimeError):
    def __init__(self, message: str, problems: list[tuple[str, str]] | None = None):
        super().__init__(message)
        self.problems = problems or []


class WriteError(ReleaseError):
    """The release was fine but couldn't be written (exit code 3)."""


@dataclass
class Item:
    rel: str            # path inside the release folder, with /
    data: bytes
    export: str = ""    # a DLL: the export it must have
    host: bool = False  # a DLL: modhost.dll
    kind: str = ""      # player zips: "runtime", "starter", "bundled" or "made" (written by this tool)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass
class Plan:
    version: str
    items: list[Item] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)  # (path, why)
    not_built: list[str] = field(default_factory=list)            # DLLs asked for that aren't built
    edition: str = "source"
    bundled: list[dict] = field(default_factory=list)             # plus: the mods in bundled-mods.json
    commit: str = ""    # player zips: the commit the files come from
    stamp: str = ""     # player zips: where the files come from, the first line of SHA256SUMS.txt (a # comment)

    @property
    def folder(self) -> str:
        return f"{EDITION_NAMES[self.edition]}-{self.version}"

    @property
    def zip_name(self) -> str:
        return f"{self.folder}.zip"

    @property
    def sums_name(self) -> str:
        return f"{self.folder}.{SUMS}"

    def binaries(self) -> dict[str, Item]:
        return {i.rel: i for i in self.items if i.export}


# ------------------------------------------------------------------ what goes in

def valid_version(version: str) -> bool:
    return bool(VERSION_RE.fullmatch(version)) and ".." not in version


def _git(root: Path, *args: str) -> list[str]:
    """git's output, split into lines (or -z entries). Raises ReleaseError when git can't start or fails."""
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    except OSError as e:
        raise ReleaseError(f"git is needed to tell which files go in, and it couldn't start ({e.strerror or e})")
    if r.returncode:
        said = next(iter(r.stderr.decode("utf-8", "replace").strip().splitlines()), f"exit code {r.returncode}")
        raise ReleaseError(f"git is needed to tell which files go in (only git knows what .gitignore keeps out)."
                           f" git said: {said}")
    return [p for p in r.stdout.decode("utf-8", "replace").replace("\n", "\0").split("\0") if p]


def _git_lines(root: Path, *args: str) -> list[str]:
    try:
        return _git(root, *args)
    except ReleaseError:
        return []


def never(rel: str) -> bool:
    """Paths that never go in, even if git tracks them (in any letter case: on Windows Build/ is build/)."""
    parts = rel.lower().split("/")
    return (parts[0] in NEVER_TOP or bool(NEVER_DIRS & set(parts[:-1]))
            or os.path.splitext(parts[-1])[1] in NEVER_EXT)


def allowed_root(rel: str) -> bool:
    low = rel.lower()
    return low in {f.lower() for f in ROOT_FILES} or (low.split("/")[0] in ROOT_DIRS and "/" in low)


def source_files(root: Path) -> list[str]:
    """The project files for the release (paths relative to root, with /): files git tracks, and the files git would
    commit under the allow-list; never out/, build/, upstream/ or __pycache__. Raises ReleaseError when git fails:
    without git nothing would keep out what .gitignore lists."""
    listed = _git(root, "ls-files", "-co", "--exclude-standard", "-z")
    tracked = set(_git(root, "ls-files", "-c", "-z"))
    files = set()
    for rel in set(listed) | tracked:
        rel = rel.replace("\\", "/")
        if never(rel) or not (rel in tracked or allowed_root(rel)):
            continue
        if os.path.lexists(root / rel):  # (a tracked file deleted here isn't part of the release)
            files.add(rel)
    return sorted(files)


def _is_link(p: Path) -> bool:
    try:
        return p.is_symlink() or getattr(p, "is_junction", lambda: False)()
    except OSError:
        return False


def link_problems(root: Path, rel: str) -> list[str]:
    """A link or junction at the file or at any folder between root and it. Never followed: one could point at the
    game, and git for Windows lists the files behind a junction as if they were here."""
    parts = rel.split("/")
    p = root
    for i, part in enumerate(parts):
        p = p / part
        if _is_link(p):
            if i == len(parts) - 1:
                return ["a link (not followed)"]
            return [f"inside {'/'.join(parts[:i + 1])}/, a link or junction (not followed: it could point at the game)"]
    real = os.path.normcase(os.path.realpath(root / rel))
    if real != os.path.normcase(os.path.join(os.path.realpath(root), *parts)):
        return ["reached through a link (not followed: it could point at the game)"]
    return []


def _short_path(path: str) -> str:
    """Windows' 8.3 short form of a path (C:\\Users\\ALEXAN~1), or the path itself."""
    if os.name != "nt":
        return path
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf))
        return buf.value if 0 < n < len(buf) else path
    except Exception:  # (no kernel32 call possible: keep the long form only)
        return path


def _path_forms(path: str, drive: bool = False) -> list[str]:
    """A folder path in the spellings text can hold it: with \\, / or \\\\ between the parts. Without the drive
    letter, C:\\Users\\x, D:/Users/x and Git Bash's /c/Users/x all match. A path of fewer than 2 parts is too
    general."""
    d, rest = os.path.splitdrive(path)
    parts = [p for p in re.split(r"[\\/]+", rest) if p]
    if len(parts) < 2:
        return []
    return [(d if drive else "") + sep + sep.join(parts) for sep in ("\\", "/", "\\\\")]


def leak_terms(root: Path = REPO) -> list[str]:
    """What must not be in a release: this computer's login name and home folder name, the home folder's full path
    (also its 8.3 short form, and as TEMP spells it), the repo's path, and git's e-mail and name. The git name is
    left out when LICENSE holds it: the holder chose to publish it."""
    words: list[str] = []
    try:
        words.append(getpass.getuser())
    except Exception:  # (getuser raises when no login name is set at all)
        pass
    home = Path.home()
    words += [home.name, os.environ.get("USERNAME", ""), os.environ.get("USER", "")]
    try:
        license_text = (root / "LICENSE").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        license_text = ""
    words += [n for n in _git_lines(root, "config", "--get-all", "user.name") if n.strip().lower() not in license_text]
    terms = [w.strip() for w in words if len(w.strip()) >= 3 and w.strip().lower() not in GENERIC_LOGINS]
    terms += [e.strip() for e in _git_lines(root, "config", "--get-all", "user.email") if "@" in e]
    # The home folder's path, whatever the login: C:\Users\dev\... holds the login even when "dev" alone is too common.
    homes = {str(home), _short_path(str(home))}
    parent = os.path.normcase(str(home.parent)).rstrip("\\/")
    for var in ("TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA"):  # (TEMP is often in the 8.3 form)
        v = os.environ.get(var, "")
        if parent and os.path.normcase(v).startswith(parent + os.sep):
            homes.add(v[:len(parent) + 1] + re.split(r"[\\/]", v[len(parent) + 1:])[0])
    for h in homes:
        terms += _path_forms(h)
    terms += _path_forms(str(Path(root).resolve()), drive=True)
    seen: dict[str, str] = {}
    for t in terms:
        seen.setdefault(t.lower(), t)
    return sorted(v for v in seen.values() if v)


def savage_wind_dirs() -> list[Path]:
    """The Savage Wind folders (as build_native.py finds them): no DLL may come from there."""
    install = bn.default_install()
    return [install, bn.default_data_root(install)]


def leak_problems(data: bytes, terms) -> list[str]:
    """A login name, local path, e-mail or git name inside a file (UTF-8 or UTF-16 text, in text or binary files), as
    a whole word: a term that starts or ends with a letter or digit must not run on into another one."""
    out = []
    for t in terms:
        for enc, tail in (("utf-8", b""), ("utf-16-le", b"\0")):
            pre = rb"(?<![0-9A-Za-z]" + tail + rb")" if t[:1].isalnum() else b""
            post = rb"(?![0-9A-Za-z]" + tail + rb")" if t[-1:].isalnum() else b""
            if re.search(pre + re.escape(t.encode(enc)) + post, data, re.I):
                out.append(f"holds {t!r} (your user name, a folder of this PC, or your git name or e-mail): a leak")
                break
    return out


def secret_problems(rel: str, data: bytes) -> list[str]:
    """Key files by name, and private keys and access tokens by their shape."""
    name = rel.lower().rsplit("/", 1)[-1]
    out = []
    if (name in SECRET_NAMES or os.path.splitext(name)[1] in SECRET_EXT
            or (name.startswith(".env.") and not name.endswith((".example", ".sample", ".template")))):
        out.append("a file for keys or passwords (by its name)")
    if SECRET_RE.search(data):
        out.append("holds a private key or an access token")
    return out


def size_problems(size: int) -> list[str]:
    return [f"bigger than {MAX_FILE_BYTES // (1024 * 1024)} MB"] if size > MAX_FILE_BYTES else []


def archive_kind(rel: str, data: bytes) -> str:
    """'zip', 'gzip', '7z', ... for an archive or compressed file (by its first bytes, else by its type), else ''."""
    for magic, kind in ARCHIVE_MAGIC:
        if data.startswith(magic):
            return kind
    if BZIP2_RE.match(data):
        return "bzip2"
    if data[257:262] == b"ustar":
        return "tar"
    return ARCHIVE_EXT.get(os.path.splitext(rel.lower())[1], "")


def _gzip_name(data: bytes) -> str:
    """The file name a gzip file stores (its FNAME field), or ''."""
    if len(data) < 10 or not data[3] & 8:
        return ""
    at = 10 + (2 + int.from_bytes(data[10:12], "little") if data[3] & 4 else 0)
    end = data.find(b"\0", at)
    return data[at:end].decode("latin-1") if end > at else ""


def _members(kind: str, rel: str, data: bytes):
    """(name, content) for every file in a zip or gzip file; content is a reason (str) for one that can't be read.
    Each is read up to just over the size limit."""
    if kind == "gzip":
        base = rel.rsplit("/", 1)[-1]
        name = _gzip_name(data) or (base[:-4] + ".tar" if base.lower().endswith(".tgz")
                                    else base[:-3] if base.lower().endswith(".gz") else base + ".unpacked")
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as g:
            yield name, g.read(MAX_FILE_BYTES + 1)
        return
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_MEMBERS:
            raise ValueError(f"{len(infos)} files in it, more than {MAX_MEMBERS}")
        for info in infos:
            if info.flag_bits & 1:
                yield info.filename, "encrypted, so it can't be checked"
                continue
            with zf.open(info) as f:
                yield info.filename, f.read(MAX_FILE_BYTES + 1)


def _safe_parts(name: str) -> list[str]:
    """A file name from inside an archive, made safe to write under a temp folder (no '..', no absolute paths)."""
    parts = []
    for p in name.replace("\\", "/").split("/"):
        p = re.sub(r'[\x00-\x1f<>:"|?*]', "_", p).rstrip(" .")
        if p:
            parts.append("_" + p if p.split(".")[0].lower() in WIN_RESERVED else p)
    return parts or ["unnamed"]


_ARCHIVE_CHECKED: dict[tuple, list[str]] = {}  # (name, SHA-256, cleanallowed, terms) -> problems: the runtime's
                                               # python3XX.zip is checked once per run, not once per zip and pass


def archive_problems(rel: str, data: bytes, terms, cleanallowed: bool = False) -> list[str]:
    """An archive or compressed file can hide game files, keys or leaks from every other check. zip and gzip files
    are opened and each file in them checked like a file of the release (check_clean's rules are skipped only when
    the archive itself is in .cleanallow); an archive inside one, and every other kind, is refused."""
    kind = archive_kind(rel, data)
    if not kind:
        return []
    key = (rel, hashlib.sha256(data).hexdigest(), cleanallowed, tuple(terms))
    if key not in _ARCHIVE_CHECKED:
        _ARCHIVE_CHECKED[key] = _archive_problems(kind, rel, data, terms, cleanallowed)
    return list(_ARCHIVE_CHECKED[key])


def _archive_problems(kind: str, rel: str, data: bytes, terms, cleanallowed: bool) -> list[str]:
    if kind not in ("zip", "gzip"):
        return [f"a {kind} archive or compressed file: what's inside can't be checked"]
    found: dict[str, list[str]] = {}  # member name -> why (in order)
    total = 0
    with tempfile.TemporaryDirectory(prefix="make_release-") as tmp:
        written: list[tuple[str, str]] = []
        taken: set[str] = set()
        try:
            for i, (name, content) in enumerate(_members(kind, rel, data)):
                why = found.setdefault(name, [])
                if isinstance(content, str):
                    why.append(content)
                    continue
                total += len(content)
                if total > MAX_UNPACKED:
                    why.append(f"the {kind} file unpacks to more than {MAX_UNPACKED // (1024 * 1024)} MB")
                    break
                why += [f"its name {w}" for w in leak_problems(name.encode("utf-8", "replace"), terms)]
                why += size_problems(len(content)) + leak_problems(content, terms) + secret_problems(name, content)
                inner = archive_kind(name, content)
                if inner:
                    why.append(f"a {inner} file inside a {kind} file: not opened, so it can't be checked")
                if cleanallowed or len(content) > MAX_FILE_BYTES:
                    continue
                parts = _safe_parts(name)
                if "/".join(parts).lower() in taken:
                    parts = [f"_same_name_{i}", parts[-1]]
                dest = Path(tmp).joinpath(*parts)
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(content)
                except OSError:
                    parts = [f"_same_name_{i}", parts[-1]]
                    dest = Path(tmp).joinpath(*parts)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(content)
                taken.add("/".join(parts).lower())
                written.append((name, "/".join(parts)))
        except Exception as e:  # (zipfile, gzip and zlib raise many kinds on a damaged file)
            found.setdefault("(the file itself)", []).append(f"can't be opened or read to the end ({e})")
        markers: dict[str, str] = {}
        for name, r in written:  # check_clean's rules, on the files as they come out of the archive
            found[name] += cc.problems_for(Path(tmp), r, markers)
    bad = [f"{name}: {'; '.join(why)}" for name, why in found.items() if why]
    if not bad:
        return []
    more = f"; and {len(bad) - 3} more" if len(bad) > 3 else ""
    return [f"a {kind} file whose contents fail the checks ({'; '.join(bad[:3])}{more})"]


def content_problems(rel: str, data: bytes, terms, allowed=frozenset()) -> list[str]:
    """make_release's own checks on one file's bytes (on top of check_clean's), before and after writing."""
    out = []
    if RELEASE_FILE_RE.fullmatch(rel.rsplit("/", 1)[-1]):
        out.append("a release zip or SHA-256 list this tool made: never packed again")
    out += size_problems(len(data)) + leak_problems(data, terms) + secret_problems(rel, data)
    return out + archive_problems(rel, data, terms, rel in allowed)


def _inside(path: str, folder: Path) -> bool:
    f = os.path.normcase(os.path.realpath(folder))
    p = os.path.normcase(path)
    return p == f or p.startswith(f.rstrip("\\/") + os.sep)


def pe_problems(data: bytes, export: str, host: bool) -> list[str]:
    """Is this a 64-bit Windows DLL that build_native.py built (the export its target must have)?"""
    try:
        info = bn.pe_info(data)
    except bn.PeError as e:
        return [f"not a DLL we built ({e})"]
    out = []
    if not info.dll:
        out.append("a program, not a DLL")
    if info.machine != 0x8664:
        out.append(f"not a 64-bit Windows DLL (machine {info.machine:#x})")
    if export not in info.exports:
        out.append(f"doesn't export {export}, so build_native.py didn't build it")
    if any(GEN_EXPORT_RE.fullmatch(e) for e in info.exports):
        out.append("exports game functions (f_<address>): game code")
    if host and HOST_MARK not in data:
        out.append("has no [modhost] log text, so it isn't our modhost.dll (the game library?)")
    return out


def binary_targets(build_dir: Path) -> list[tuple[Path, str, bool]]:
    """modhost.dll and the example mods build_native.py builds: (path, export, is modhost). Not the self-test files."""
    targets = [bn.HOST, *bn.EXAMPLES, *getattr(bn, "HOOK_EXAMPLES", ()), *getattr(bn, "GHOST_EXAMPLES", ())]
    return [(t.output(build_dir), t.export or "swmod_init", t is bn.HOST) for t in targets]


def hardlink_names(path: Path) -> list[str] | None:
    """Every name of this file, one per hard link (Windows), or None where they can't be listed."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        first, nxt, close = k32.FindFirstFileNameW, k32.FindNextFileNameW, k32.FindClose
        first.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR)
        first.restype = wintypes.HANDLE
        nxt.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR)
        nxt.restype = wintypes.BOOL
        close.argtypes = (wintypes.HANDLE,)
        full = os.path.abspath(path)
        drive = os.path.splitdrive(full)[0]  # (the names come without it)
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        h = first(full, 0, ctypes.byref(size), buf)
        if not h or h == wintypes.HANDLE(-1).value:
            return None
        names = []
        try:
            while True:
                names.append(drive + buf.value)
                size = wintypes.DWORD(len(buf))
                if not nxt(h, ctypes.byref(size), buf):
                    break
        finally:
            close(h)
        return names
    except Exception:  # (no kernel32 call possible)
        return None


def hardlink_problems(path: Path, forbidden) -> list[str]:
    """A hard link is the same file under another name, and realpath can't see it: a DLL in build/native may not
    also be a file in the Savage Wind folder. Where the other names can't be listed, any hard link is refused."""
    try:
        count = path.stat().st_nlink
    except OSError:
        return []
    if count <= 1:
        return []
    names = hardlink_names(path)
    if names is None:
        return [f"a hard link ({count} names for one file) whose other names can't be listed: build it again"]
    if any(_inside(n, d) for n in names for d in forbidden):
        return ["a hard link to a file in the Savage Wind folder: never shipped"]
    return []


def binary_problems(path: Path, build_dir: Path, forbidden) -> list[str]:
    """Where a DLL comes from: inside the build folder, no link on the way, not the game library, not from Savage
    Wind (not even through a hard link)."""
    out = []
    if path.name.lower() in cc.BLOCKED_NAMES or path.name.lower() == bn.GAME_LIB:
        out.append("the game's code library: never shipped")
    try:
        out += link_problems(build_dir, path.relative_to(build_dir).as_posix())
    except ValueError:  # (not under build_dir at all)
        if _is_link(path):
            out.append("a link (not followed)")
    real = os.path.realpath(path)
    if not _inside(real, build_dir):
        out.append("not inside the build folder")
    if any(_inside(real, d) for d in forbidden):
        out.append("from the Savage Wind folder: never shipped")
    return out + hardlink_problems(path, forbidden)


def _read(path: Path) -> tuple[bytes, list[str]]:
    """A file's bytes, or the reason it wasn't read (a file over the size limit isn't read at all)."""
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            return b"", size_problems(size)
        return path.read_bytes(), []
    except OSError as e:
        return b"", [f"can't read it: {e.strerror or e}"]


def _add_file(plan: Plan, root: Path, rel: str, dest: str, terms, allowed, markers) -> None:
    """A project file (rel) into the plan at dest, after every check; else its reasons go to plan.refused."""
    why = name_problems(f"{plan.folder}/{dest}", plan.folder, terms) + link_problems(root, rel)
    data = b""
    if not why and rel not in allowed:
        why += cc.problems_for(root, rel, markers)
    if not why:
        data, why = _read(root / rel)
    if not why:
        why += content_problems(rel, data, terms, allowed)
    if why:
        plan.refused.append((dest, "; ".join(why)))
    else:
        plan.items.append(Item(dest, data))


def _add_binary(plan: Plan, path: Path, dest: str, export: str, host: bool, build_dir: Path, forbidden, terms,
                allowed, markers, kind: str = "") -> None:
    """A DLL from the build folder into the plan at dest, after every check."""
    why = name_problems(f"{plan.folder}/{dest}", plan.folder, terms) + binary_problems(path, build_dir, forbidden)
    data = b""
    if not why:
        why += cc.problems_for(build_dir, path.relative_to(build_dir).as_posix(), markers)
    if not why:
        data, why = _read(path)
    if not why:
        why += pe_problems(data, export, host) + content_problems(dest, data, terms, allowed)
    if why:
        plan.refused.append((dest, "; ".join(why)))
    else:
        plan.items.append(Item(dest, data, export, host, kind))


def _add_made(plan: Plan, dest: str, data: bytes, terms, kind: str = "made") -> None:
    """A file this tool writes (READ ME FIRST.txt, a bundled mod.toml, bundled-mods.json), after the same checks."""
    why = name_problems(f"{plan.folder}/{dest}", plan.folder, terms) + content_problems(dest, data, terms)
    if why:
        plan.refused.append((dest, "; ".join(why)))
    else:
        plan.items.append(Item(dest, data, kind=kind))


def plan_release(root: Path = REPO, version: str = VERSION, with_binaries: bool = False,
                 build_dir: Path | None = None, terms=None, forbidden=None, edition: str = "source",
                 runtime_dir: Path | None = None, starter: Path | None = None, experimental: bool = False,
                 files: list[str] | None = None, commit: str = "", stamp: str = "") -> Plan:
    """Everything that would go in, and everything that blocks the release (check_clean's rules and more).
    Raises ReleaseError when git can't list the files."""
    if edition in PLAYER_EDITIONS:
        return plan_player(root, version, edition, build_dir, terms, forbidden, runtime_dir, starter, experimental,
                           files, commit, stamp)
    plan = Plan(version)
    terms = leak_terms(root) if terms is None else terms
    allowed = cc.read_allow(root)
    markers: dict[str, str] = {}
    for rel in source_files(root):
        _add_file(plan, root, rel, rel, terms, allowed, markers)
    if with_binaries:
        build_dir = DEFAULT_BUILD if build_dir is None else build_dir
        forbidden = savage_wind_dirs() if forbidden is None else forbidden
        for path, export, host in binary_targets(build_dir):
            rel = f"{BUILD_PREFIX}/{path.relative_to(build_dir).as_posix()}"
            if not path.is_file():
                plan.not_built.append(rel)
                continue
            _add_binary(plan, path, rel, export, host, build_dir, forbidden, terms, allowed, markers)
    plan.items.sort(key=lambda i: i.rel)
    plan.refused.sort()
    return plan


# ------------------------------------------------------------------ the player zips (core, plus)

def player_file(rel: str) -> bool:
    """A project file that goes into app/ of the player zips: not the tests, the self-tests, .github or
    tools/packaging."""
    low = rel.lower()
    parts = low.split("/")
    return not (parts[0] in PLAYER_SKIP_TOP or low in PLAYER_SKIP_FILES or low.startswith(PACKAGING + "/")
                or PLAYER_SKIP_DIRS & set(parts[:-1])
                or (parts[0] == "tools" and bool(PLAYER_SKIP_NAME.fullmatch(parts[-1]))))


def default_work(root: Path) -> Path:
    return root / "out" / "packaging-work"


# ------------------------------------------------------------------ the player zips come from a commit, not the work tree

@dataclass
class Source:
    """Where a player zip's files come from: a snapshot of one commit (git archive) in out/packaging-work/src/, and
    the DLLs built from that snapshot. Never the working tree: other jobs' unfinished files stay out."""
    root: Path                                          # the snapshot folder (the project as of the commit)
    commit: str                                         # the full commit id
    files: list[str]                                    # the project files in it (relative, with /)
    local: dict[str, str] = field(default_factory=dict)  # --overlay: uncommitted files laid over it -> SHA-256

    def stamp(self, edition: str, version: str) -> str:
        what = f"Breathalyzer{' Plus' if edition == 'plus' else ''} {version}, built from commit {self.commit}"
        if not self.local:
            return what
        return (f"{what} plus {len(self.local)} uncommitted file(s): "
                + ", ".join(f"{rel} ({sha[:12]})" for rel, sha in sorted(self.local.items()))
                + ". A test build: not for upload.")


def _git_bytes(root: Path, *args: str) -> bytes:
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    except OSError as e:
        raise ReleaseError(f"git couldn't start ({e.strerror or e})")
    if r.returncode:
        said = next(iter(r.stderr.decode("utf-8", "replace").strip().splitlines()), f"exit code {r.returncode}")
        raise ReleaseError(f"git {args[0]} failed: {said}")
    return r.stdout


def resolve_commit(root: Path, rev: str) -> str:
    out = _git_bytes(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", out):
        raise ReleaseError(f"{rev} isn't a commit here")
    return out


def take_snapshot(root: Path, rev: str = "HEAD", overlay=(), work: Path | None = None) -> Source:
    """The project as of commit rev, unpacked from git archive into out/packaging-work/src/<commit> (made fresh).
    overlay: working-tree files (tracked, or under the allow-list) laid over it for a test build; each is listed with
    its SHA-256 in the stamp. Raises ReleaseError (nothing is left half made)."""
    commit = resolve_commit(root, rev)
    tracked_now = set(_git(root, "ls-files", "-c", "-z"))
    files = {rel for rel in _git(root, "ls-tree", "-r", "-z", "--name-only", commit) if not never(rel)}
    bad, local = [], {}
    for raw in overlay:
        rel = str(raw).replace("\\", "/").removeprefix("./")
        p = root / rel
        if never(rel) or not (rel in tracked_now or rel in files or allowed_root(rel)):
            bad.append((rel, "--overlay takes project files only (tracked, or under tools/, native/, docs/ ...)"))
        elif not p.is_file() or _is_link(p):
            bad.append((rel, "--overlay: not a file here (or a link)"))
    if bad:
        raise ReleaseError("refused", bad)
    work = default_work(root) if work is None else work
    dest = work / SNAPSHOT_DIR / (commit[:12] + ("-local" if overlay else ""))
    if not _inside(os.path.realpath(dest), work):
        raise ReleaseError(f"the snapshot folder {dest} isn't under {work}")
    try:
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        # (the committed bytes: without core.autocrlf=false, git on Windows would write CRLF into the archive)
        tar = _git_bytes(root, "-c", "core.autocrlf=false", "-c", "core.eol=lf", "archive", "--format=tar", commit)
        with tarfile.open(fileobj=io.BytesIO(tar)) as tf:
            tf.extractall(dest, filter="data")
        for raw in overlay:
            rel = str(raw).replace("\\", "/").removeprefix("./")
            data = (root / rel).read_bytes()
            target = dest.joinpath(*rel.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            local[rel] = hashlib.sha256(data).hexdigest()
    except (OSError, tarfile.TarError) as e:
        raise ReleaseError(f"couldn't unpack commit {commit[:12]} into {dest} ({e})")
    rels = sorted(rel for rel in files | set(local) if (dest / rel).is_file())
    return Source(dest, commit, rels, local)


def build_snapshot_native(src: Source, install: Path | None = None) -> Path:
    """modhost.dll and our mods' DLLs, built from the snapshot's own sources (its tools/build_native.py and
    tools/modmenu.py) into <snapshot>/build/native, at below-normal priority. Returns that folder."""
    out = src.root / "build" / "native"
    tools = src.root / "tools"
    sw = [str(install)] if install else []
    cmds = [[sys.executable, "-I", str(tools / "build_native.py"), "build", "--out", str(out),
             *(["--install", *sw] if sw else [])],
            [sys.executable, "-I", "-c", MODMENU_BUILD, str(tools), str(out), *sw]]
    flags = BELOW_NORMAL if os.name == "nt" else 0
    for cmd in cmds:
        try:
            r = subprocess.run(cmd, capture_output=True, cwd=src.root, creationflags=flags)
        except OSError as e:
            raise ReleaseError(f"couldn't build the DLLs from commit {src.commit[:12]} ({e.strerror or e})")
        if r.returncode:
            tail = " | ".join((r.stdout + r.stderr).decode("utf-8", "replace").strip().splitlines()[-4:])
            raise ReleaseError(f"building the DLLs from commit {src.commit[:12]} failed: {tail}")
    return out


def _read_json(path: Path) -> tuple[dict, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, "is missing"
    except (OSError, ValueError) as e:
        return {}, f"can't be read ({e})"
    return (data, "") if isinstance(data, dict) else ({}, "isn't a JSON object")


def _crlf(data: bytes) -> bytes:
    """Windows line ends (Notepad and cmd.exe)."""
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def runtime_files(runtime: Path, lock: dict) -> tuple[dict[str, bytes], list[tuple[str, str]]]:
    """The runtime folder's files (path -> bytes), and every way it differs from runtime.lock.json: a file more or
    less, another SHA-256, a link."""
    files = lock.get("files") if isinstance(lock, dict) and lock.get("format") == 1 else None
    if not isinstance(files, dict) or not files:
        return {}, [(RUNTIME_LOCK, "isn't a runtime lock this tool knows (format 1, with its files)")]
    if not runtime.is_dir():
        return {}, [("(runtime)", f"no runtime folder at {runtime}: run python tools/packaging/build_runtime.py")]
    found, unreadable = cc.walk_files(runtime, frozenset())
    bad = list(unreadable)
    for rel in sorted(set(found) - set(files)):
        bad.append((rel, "not in runtime.lock.json: the runtime changed after it was built and checked"))
    for rel in sorted(set(files) - set(found)):
        bad.append((rel, "in runtime.lock.json but missing"))
    out: dict[str, bytes] = {}
    for rel in sorted(set(found) & set(files)):
        why = link_problems(runtime, rel)
        data = b""
        if not why:
            data, why = _read(runtime / rel)
        want = files[rel].get("sha256") if isinstance(files[rel], dict) else None
        if not why and hashlib.sha256(data).hexdigest() != want:
            why = ["differs from runtime.lock.json: the runtime changed after it was built and checked"]
        if why:
            bad += [(rel, w) for w in why]
        else:
            out[rel] = data
    return out, bad


def runtime_shape_problems(files: dict[str, bytes]) -> list[tuple[str, str]]:
    """What any runtime must be: python.exe and pythonw.exe, the standard library zip, a ._pth file that keeps it
    apart from other Pythons (relative paths only, no 'import site'), no site-packages or .pth files, and only
    64-bit Windows binaries."""
    out = [(n, "missing: the runtime needs it") for n in RUNTIME_NEEDS if n not in files]
    if not [n for n in files if re.fullmatch(r"python3\d+\.zip", n)]:
        out.append(("python3XX.zip", "missing: the standard library"))
    pths = [n for n in files if re.fullmatch(r"python3\d+\._pth", n)]
    if len(pths) != 1:
        out.append(("python3XX._pth", "missing (or more than one): without it the runtime could use the PC's own "
                                      "Python and its packages"))
    for n in pths:
        for line in files[n].decode("utf-8", "replace").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("import"):
                out.append((n, f"'{s}': site-packages could come in"))
            elif s.startswith(("\\", "/")) or re.match(r"[A-Za-z]:", s):
                out.append((n, f"an absolute path ({s}): only folders of the runtime"))
    for n, data in files.items():
        low = n.lower()
        if "site-packages" in low.split("/"):
            out.append((n, "site-packages never goes in"))
        elif low.endswith(".pth"):
            out.append((n, ".pth files never go in"))
        if low.endswith((".exe", ".dll", ".pyd")):
            try:
                if bn.pe_info(data).machine != 0x8664:
                    out.append((n, "not a 64-bit Windows file"))
            except bn.PeError as e:
                out.append((n, f"not a Windows program or DLL ({e})"))
    return out


def runtime_lock_problems(runtime: Path, lock: dict) -> list[tuple[str, str]]:
    """tools/packaging/build_runtime.py --check: the folder against the lock, and the runtime rules."""
    files, bad = runtime_files(runtime, lock)
    return bad + (runtime_shape_problems(files) if not bad else [])


def pe_subsystem(data: bytes) -> int:
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    return struct.unpack_from("<H", data, pe + 24 + 68)[0]


def starter_problems(data: bytes, terms) -> list[str]:
    """Start Breathalyzer.exe: a 64-bit window program (not a DLL, no console) that uses only kernel32 and user32 and
    exports nothing, with no leak or secret in it."""
    try:
        info = bn.pe_info(data)
        sub = pe_subsystem(data)
    except (bn.PeError, struct.error) as e:
        return [f"not a Windows program ({e})"]
    out = []
    if info.dll:
        out.append("a DLL, not a program")
    if info.machine != 0x8664:
        out.append(f"not a 64-bit program (machine {info.machine:#x})")
    if sub != 2:
        out.append(f"not a window program (subsystem {sub}, not 2)")
    extra = sorted(d for d in info.imports if d.lower() not in STARTER_IMPORTS)
    if extra:
        out.append(f"uses {', '.join(extra)}: the starter may only use kernel32 and user32")
    if info.exports:
        out.append("exports functions, so it isn't our starter")
    return out + leak_problems(data, terms) + secret_problems(STARTER_NAME, data)


def _add_runtime(plan: Plan, root: Path, runtime_dir: Path, terms, markers) -> None:
    lock, why = _read_json(root / RUNTIME_LOCK)
    if why:
        plan.refused.append((RUNTIME_LOCK, f"{why}: run python tools/packaging/build_runtime.py"))
        return
    files, bad = runtime_files(runtime_dir, lock)
    plan.refused += [(f"{APP}/runtime/{rel}", w) for rel, w in bad + runtime_shape_problems(files)]
    for rel, data in files.items():
        dest = f"{APP}/runtime/{rel}"
        why = (name_problems(f"{plan.folder}/{dest}", plan.folder, terms) + cc.problems_for(runtime_dir, rel, markers)
               + content_problems(dest, data, terms))
        if why:
            plan.refused.append((dest, "; ".join(why)))
        else:
            plan.items.append(Item(dest, data, kind="runtime"))


def _add_starter(plan: Plan, root: Path, starter: Path, terms) -> None:
    lock, why_lock = _read_json(root / STARTER_LOCK)
    why = name_problems(f"{plan.folder}/{STARTER_NAME}", plan.folder, terms)
    data = b""
    if why_lock:
        why.append(f"{STARTER_LOCK} {why_lock}: run python tools/packaging/build_starter.py")
    elif _is_link(starter):
        why.append("a link (not followed)")
    elif not starter.is_file():
        why.append(f"not built yet ({starter}): run python tools/packaging/build_starter.py")
    else:
        data, read_why = _read(starter)
        why += read_why
    if data:
        if hashlib.sha256(data).hexdigest() != lock.get("sha256"):
            why.append(f"doesn't match {STARTER_LOCK}: build it again with python tools/packaging/build_starter.py")
        sources = lock.get("sources") if isinstance(lock.get("sources"), dict) else {}
        for name, rel in STARTER_SOURCES.items():
            try:
                have = hashlib.sha256((root / rel).read_bytes()).hexdigest()
            except OSError:
                have = None
            if sources.get(name) != have:
                why.append(f"{rel} changed since the starter was built: run python tools/packaging/build_starter.py")
        why += starter_problems(data, terms)
    if why:
        plan.refused.append((STARTER_NAME, "; ".join(why)))
    else:
        plan.items.append(Item(STARTER_NAME, data, kind="starter"))


def _plain_text_problems(what: str, text, limit: int) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        return [f"{what} is missing"]
    out = []
    if len(text) > limit:
        out.append(f"{what} is {len(text)} characters, more than {limit}")
    if any(not (" " <= c <= "~") for c in text) or FONT_MISSING & set(text):
        out.append(f"{what} must be plain ASCII without {' '.join(sorted(FONT_MISSING))} (the game's font lacks them)")
    return out


def _bundled_path_problems(what: str, path, tracked: set[str], build_ok: bool) -> list[str]:
    """A path in bundled-mods.toml: a file git tracks, or (build_ok) one under build/native. Never out/ or a path
    that leaves the project."""
    if not isinstance(path, str) or not path:
        return [f"{what} is missing"]
    parts = path.split("/")
    if "\\" in path or path.startswith("/") or re.match(r"[A-Za-z]:", path) or any(p in ("", ".", "..") for p in parts):
        return [f"{what} {path!r} must be a plain path inside the project, with /"]
    if path.lower().startswith(BUILD_PREFIX + "/"):
        return [] if build_ok else [f"{what} {path!r} must be a file git tracks, not one in build/"]
    if parts[0].lower() in NEVER_TOP:
        return [f"{what} {path!r} is in {parts[0]}/: never shipped (only our own tracked files and build/native)"]
    if path not in tracked:
        return [f"{what} {path!r} isn't a file git tracks"]
    return []


def _commands_problems(what: str, cmds, tracked: set[str]) -> list[str]:
    if not isinstance(cmds, list) or not cmds or not all(
            isinstance(c, list) and c and all(isinstance(x, str) and x for x in c) for c in cmds):
        return [f"{what} must be a list of commands, each a list like [\"quality.py\", \"set\", \"low\"]"]
    out = []
    for c in cmds:
        if "/" in c[0] or "\\" in c[0] or f"tools/{c[0]}" not in tracked:
            out.append(f"{what}: {c[0]!r} isn't one of our tools (a file in tools/)")
    return out


def _manifest_id_problems(path: Path, mod_id: str) -> list[str]:
    try:
        man = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        return [f"its mod.toml can't be read ({e})"]
    got = (man.get("mod") or {}).get("id")
    return [] if got == mod_id else [f"its mod.toml says id {got!r}, not {mod_id!r}"]


def read_bundled(root: Path, tracked: set[str]) -> tuple[list[dict], list[tuple[str, str]]]:
    """bundled-mods.toml, checked: (the mods, the problems). Any problem refuses the whole list."""
    try:
        data = tomllib.loads((root / BUNDLED_TOML).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], [(BUNDLED_TOML, "is missing: the plus zip needs its list of mods")]
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        return [], [(BUNDLED_TOML, f"can't be read ({e})")]
    mods = data.get("mod")
    if not isinstance(mods, list) or not mods or not all(isinstance(m, dict) for m in mods):
        return [], [(BUNDLED_TOML, "has no [[mod]] entries")]
    bad: list[tuple[str, str]] = []
    seen: set[str] = set()
    for n, m in enumerate(mods, 1):
        mid = m.get("id")
        where = f"{BUNDLED_TOML} [{mid if isinstance(mid, str) else f'mod {n}'}]"
        why = []
        if not isinstance(mid, str) or not mid.startswith(OUR_IDS) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", mid):
            why.append(f"id {mid!r} isn't one of ours (it must start with {' or '.join(OUR_IDS)})")
        elif mid in seen:
            why.append("listed twice")
        seen.add(mid if isinstance(mid, str) else "")
        unknown = sorted(set(m) - BUNDLED_KEYS)
        if unknown:
            why.append(f"unknown keys {', '.join(unknown)}")
        why += _plain_text_problems("title", m.get("title"), MAX_TITLE)
        why += _plain_text_problems("description", m.get("description"), MAX_DESCRIPTION)
        if m.get("default") not in ("on", "off"):
            why.append("default must be \"on\" or \"off\"")
        if not isinstance(m.get("why"), str) or not m["why"].strip():
            why.append("why is missing (why the default)")
        tags = m.get("tags", [])
        if not isinstance(tags, list):
            why.append("tags must be a list")
        else:
            for t in tags:
                why += _plain_text_problems("a tag", t, MAX_TAG)
        if not isinstance(m.get("ready"), bool):
            why.append("ready must be true or false")
        if "developer" in m and not isinstance(m["developer"], bool):
            why.append("developer must be true or false (true: Play turns on developer commands while it is on)")
        if "egg" in m and not isinstance(m["egg"], bool):
            why.append("egg must be true or false (true: an easter egg, behind the picker's one easter-egg switch)")
        kind = m.get("kind")
        if kind == "code":
            why += _bundled_path_problems("manifest", m.get("manifest"), tracked, True)
            why += _bundled_path_problems("dll", m.get("dll"), set(), True)
            dll, man = m.get("dll"), m.get("manifest")
            if isinstance(dll, str) and not dll.lower().startswith(BUILD_PREFIX + "/"):
                why.append("dll must be a DLL under build/native (built here by build_native.py or modmenu.py)")
            if isinstance(dll, str) and not dll.lower().endswith(".dll"):
                why.append("dll must name a .dll file")
            if isinstance(man, str) and not man.endswith("/mod.toml"):
                why.append("manifest must name a mod.toml")
            if "folder" in m or "build" in m:
                why.append("a code mod has manifest and dll, not folder or build")
        elif kind == "recipe":
            folder = m.get("folder")
            why += _bundled_path_problems("folder's mod.toml", f"{folder}/mod.toml" if isinstance(folder, str)
                                          else None, tracked, False)
            why += _commands_problems("build", m.get("build"), tracked)
            if "manifest" in m or "dll" in m:
                why.append("a recipe has folder and build, not manifest or dll")
        else:
            why.append("kind must be \"code\" or \"recipe\"")
        if "after_add" in m:
            why += _commands_problems("after_add", m["after_add"], tracked)
        if not why and kind == "recipe":
            why += _manifest_id_problems(root / m["folder"] / "mod.toml", mid)
        bad += [(where, w) for w in why]
    return (mods if not bad else []), bad


def player_manifest(text: str, mod: dict) -> tuple[str, list[str]]:
    """The bundled copy of a mod.toml: the developer comments at the top replaced by one line, and [mod]'s name and
    description set to the player text. Checked by reading it back. Returns (text, problems)."""
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith("#")):
        i += 1
    body = lines[i:]
    table, done = "", set()
    for j, line in enumerate(body):
        s = line.strip()
        if s.startswith("["):
            table = s.split("#", 1)[0].strip()
        elif table == "[mod]":
            m = re.match(r"(name|description)\s*=", s)
            if m and m.group(1) not in done:
                key = m.group(1)
                body[j] = f"{key} = {json.dumps(mod['title'] if key == 'name' else mod['description'])}"
                done.add(key)
    head = ["# Comes with Breathalyzer Plus: its name and description are the player text from bundled-mods.toml.", ""]
    out = "\n".join(head + body).rstrip("\n") + "\n"
    try:
        man = tomllib.loads(out)
    except tomllib.TOMLDecodeError as e:
        return out, [f"the bundled mod.toml doesn't read back ({e})"]
    info, code = man.get("mod") or {}, man.get("code") or {}
    why = []
    if info.get("id") != mod["id"]:
        why.append(f"its mod.toml says id {info.get('id')!r}, not {mod['id']!r}")
    if (info.get("name"), info.get("description")) != (mod["title"], mod["description"]):
        why.append("its mod.toml has no name and description lines under [mod] to set")
    if mod.get("kind") == "recipe":   # (a recipe's mod.toml: tools/quality.py copies it into the mod it builds)
        return out, why
    if code.get("windows") != mod["dll"].rsplit("/", 1)[-1]:
        why.append(f"its mod.toml's [code] windows is {code.get('windows')!r}, not the DLL listed "
                   f"({mod['dll'].rsplit('/', 1)[-1]})")
    return out, why


def _add_bundled(plan: Plan, root: Path, tracked: set[str], build_dir: Path, forbidden, terms, allowed, markers,
                 experimental: bool) -> None:
    """plus: our own mods (bundled-mods.toml) into app/bundled-mods/, and the picker's list app/bundled-mods.json."""
    mods, bad = read_bundled(root, tracked)
    plan.refused += bad
    if bad:
        return
    listed = []
    for m in mods:
        if not m["ready"] and not experimental:
            continue
        tags = list(m.get("tags", []))
        if not m["ready"] and "Experimental" not in tags:
            tags.insert(0, "Experimental")
        entry = {"id": m["id"], "title": m["title"], "description": m["description"],
                 "default": "on" if m["ready"] and m["default"] == "on" and not m.get("egg") else "off",
                 "why": m["why"], "tags": tags,
                 "experimental": not m["ready"], "kind": m["kind"]}
        if m["kind"] == "code":
            folder = f"{BUNDLED_DIR}/{m['id'].split('.', 1)[1]}"
            dll = build_dir.joinpath(*m["dll"].split("/")[2:])
            dest_dll = f"{APP}/{folder}/{dll.name}"
            if not dll.is_file():
                if m["ready"]:
                    plan.refused.append((dest_dll, f"not built ({m['dll']}): build it first (docs/release.md)"))
                else:
                    plan.not_built.append(m["dll"])
                continue
            man_path = (build_dir.joinpath(*m["manifest"].split("/")[2:])
                        if m["manifest"].lower().startswith(BUILD_PREFIX + "/") else root / m["manifest"])
            dest_toml = f"{APP}/{folder}/mod.toml"
            if not man_path.is_file() or _is_link(man_path):
                plan.refused.append((dest_toml, f"{m['manifest']} is missing (or a link)"))
                continue
            text, why = player_manifest(man_path.read_text(encoding="utf-8", errors="replace"), m)
            if why:
                plan.refused.append((dest_toml, "; ".join(why)))
                continue
            _add_made(plan, dest_toml, text.encode("utf-8"), terms, kind="bundled")
            _add_binary(plan, dll, dest_dll, "swmod_init", False, build_dir, forbidden, terms, allowed, markers,
                        kind="bundled")
            entry["folder"] = folder
        else:
            # (the recipe's mod.toml in app/ gets the player text: tools/quality.py copies it into the mod it builds,
            # so Home and the game's Mods page show the title and description from bundled-mods.toml)
            dest_toml = f"{APP}/{m['folder']}/mod.toml"
            item = next((i for i in plan.items if i.rel == dest_toml), None)
            if item is None:
                plan.refused.append((dest_toml, f"{m['folder']}/mod.toml isn't in the zip"))
                continue
            text, why = player_manifest(item.data.decode("utf-8", "replace"), m)
            if why:
                plan.refused.append((dest_toml, "; ".join(why)))
                continue
            plan.items.remove(item)
            _add_made(plan, dest_toml, text.encode("utf-8"), terms)
            entry["recipe"] = m["folder"]
            entry["build"] = m["build"]
        if m.get("after_add"):
            entry["after_add"] = m["after_add"]
        if m.get("developer"):
            entry["developer"] = True
        if m.get("egg"):   # (secret: the picker and Home show one easter-egg switch for all of them, OFF at first)
            entry["egg"] = True
        listed.append(entry)
    plan.bundled = listed
    doc = {"format": 1, "edition": "plus", "version": plan.version, "from": BUNDLED_TOML,
           "about": "The mods that come with Breathalyzer Plus, for the mod picker. Paths are inside app/.",
           **({"commit": plan.commit} if plan.commit else {}), **({"built_from": plan.stamp} if plan.stamp else {}),
           "mods": listed}
    _add_made(plan, f"{APP}/{BUNDLED_JSON}", (json.dumps(doc, indent=1) + "\n").encode("utf-8"), terms)


def edition_problems(plan: Plan) -> list[tuple[str, str]]:
    """What each player zip must and mustn't hold: the starter, READ ME FIRST.txt, the runtime, modhost.dll and the
    window; no program or DLL but those, the runtime's and (plus) the bundled mods'; never tests or packaging files."""
    rels = {i.rel: i for i in plan.items}
    refused = {r for r, _ in plan.refused}
    host = f"{APP}/{BUILD_PREFIX}/{bn.HOST_DLL}"
    out = []
    for need in (STARTER_NAME, README_NAME, f"{APP}/{BACKUP_CMD}", f"{APP}/runtime/python.exe",
                 f"{APP}/runtime/pythonw.exe", host, f"{APP}/tools/modman_gui.py", f"{APP}/tools/modman.py",
                 f"{APP}/LICENSE"):
        if need not in rels and need not in refused and not plan.refused:
            out.append((need, "missing: every player zip needs it"))
    if plan.edition == "plus" and f"{APP}/{BUNDLED_JSON}" not in rels and not plan.refused:
        out.append((f"{APP}/{BUNDLED_JSON}", "missing: the plus zip's mod list"))
    for rel, item in rels.items():
        low = rel.lower()
        inner = low[len(APP) + 1:] if low.startswith(APP + "/") else ""
        if low.endswith((".exe", ".dll", ".pyd", ".sys", ".scr", ".com", ".msi")) and item.kind != "runtime":
            if not (rel in (STARTER_NAME, host) or (plan.edition == "plus" and item.kind == "bundled")):
                extra = ", the runtime's and the bundled mods'" if plan.edition == "plus" else " and the runtime's"
                out.append((rel, f"no program or DLL but the starter, modhost.dll{extra} goes in the "
                                 f"{plan.edition} zip"))
        if inner and (inner.split("/")[0] in PLAYER_SKIP_TOP or inner.startswith(PACKAGING + "/")):
            out.append((rel, "tests and packaging files stay out of the player zips"))
        if plan.edition == "core" and (inner.startswith(BUNDLED_DIR + "/") or inner == BUNDLED_JSON):
            out.append((rel, "the core zip holds no mods"))
        if not inner and rel not in (STARTER_NAME, README_NAME):
            out.append((rel, f"only {STARTER_NAME} and {README_NAME} sit next to app/"))
    return out


def plan_player(root: Path, version: str, edition: str, build_dir: Path | None = None, terms=None, forbidden=None,
                runtime_dir: Path | None = None, starter: Path | None = None, experimental: bool = False,
                files: list[str] | None = None, commit: str = "", stamp: str = "") -> Plan:
    """A player zip (core or plus): the starter, READ ME FIRST.txt and app/ (see the top of this file). files: the
    project files (a snapshot's; default source_files(root)); commit and stamp: where they come from."""
    plan = Plan(version, edition=edition, commit=commit, stamp=stamp)
    terms = leak_terms(root) if terms is None else terms
    build_dir = DEFAULT_BUILD if build_dir is None else build_dir
    forbidden = savage_wind_dirs() if forbidden is None else forbidden
    runtime_dir = default_work(root) / "runtime" if runtime_dir is None else runtime_dir
    starter = default_work(root) / "starter" / STARTER_NAME if starter is None else starter
    allowed = cc.read_allow(root)
    markers: dict[str, str] = {}
    rels = source_files(root) if files is None else files
    for rel in rels:
        if not player_file(rel):
            continue
        if rel in allowed:  # (.cleanallow isn't in the player zips: check_clean on the unpacked zip would block it)
            plan.refused.append((f"{APP}/{rel}", "listed in .cleanallow, so check_clean blocks it in the unpacked "
                                                 "zip: leave it out of the player zips (player_file)"))
            continue
        _add_file(plan, root, rel, f"{APP}/{rel}", terms, allowed, markers)
    host = bn.HOST.output(build_dir)
    host_rel = f"{APP}/{BUILD_PREFIX}/{host.relative_to(build_dir).as_posix()}"
    if host.is_file():
        _add_binary(plan, host, host_rel, bn.HOST.export, True, build_dir, forbidden, terms, allowed, markers)
    else:
        plan.refused.append((host_rel, "not built: run python tools/build_native.py build"))
    _add_runtime(plan, root, runtime_dir, terms, markers)
    _add_starter(plan, root, starter, terms)
    for dest, src in ((README_NAME, READ_ME[edition]), (f"{APP}/{BACKUP_CMD}", BACKUP_CMD_SRC)):
        try:
            data = (root / src).read_bytes()
        except OSError:
            plan.refused.append((dest, f"{src} is missing"))
            continue
        _add_made(plan, dest, _crlf(data.replace(b"{version}", version.encode())), terms)
    if edition == "plus":
        _add_bundled(plan, root, set(rels), build_dir, forbidden, terms, allowed, markers, experimental)
    plan.refused += edition_problems(plan)
    plan.items.sort(key=lambda i: i.rel)
    merged: dict[str, list[str]] = {}  # (one line per path, every reason in it)
    for rel, why in plan.refused:
        if why not in merged.setdefault(rel, []):
            merged[rel].append(why)
    plan.refused = sorted((rel, "; ".join(why)) for rel, why in merged.items())
    return plan


# ------------------------------------------------------------------ the zip

def sums_text(items, prefix: str = "", stamp: str = "") -> str:
    """sha256sum's format: '<hash>  <path>' per line, sorted by path. Written as UTF-8. The player zips' lists start
    with one '# ' line saying which commit the files come from (sha256sum -c skips it)."""
    head = f"# {stamp}\n" if stamp else ""
    return head + "".join(f"{i.sha256}  {prefix}{i.rel}\n" for i in sorted(items, key=lambda i: i.rel))


def entries(plan: Plan) -> dict[str, bytes]:
    """Zip entry name -> content: every file in one folder, with SHA256SUMS.txt."""
    out = {f"{plan.folder}/{i.rel}": i.data for i in plan.items}
    out[f"{plan.folder}/{SUMS}"] = sums_text(plan.items, stamp=plan.stamp).encode("utf-8")
    return out


def zip_bytes(files: dict[str, bytes]) -> bytes:
    """A zip that only depends on the names, the contents and Python's zlib: sorted, fixed date and mode, fixed
    compression."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in sorted(files):
            zi = zipfile.ZipInfo(name, date_time=FIXED_DATE)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 3  # (so the mode below is read as a Unix mode everywhere)
            zi.external_attr = FILE_MODE << 16
            zf.writestr(zi, files[name], compresslevel=9)
    return buf.getvalue()


def name_problems(name: str, folder: str, terms=()) -> list[str]:
    """A zip entry name (folder/rel), before and after writing."""
    out = []
    try:
        raw = name.encode("utf-8")
    except UnicodeEncodeError:
        return ["a file name a zip can't hold (not valid Unicode)"]
    if "\\" in name:
        out.append("a backslash in the name")
    if name.startswith("/") or re.match(r"[A-Za-z]:", name):
        out.append("an absolute path")
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        out.append("a '..', '.' or empty part in the name")
    if any(re.search(r'[\x00-\x1f<>:"|?*]', p) for p in parts):
        out.append("a character Windows can't use in a file name")
    if parts[0] != folder or len(parts) < 2:
        out.append(f"not inside the {folder} folder")
    rel = "/".join(parts[1:])
    if never(rel) and not rel.startswith(BUILD_PREFIX + "/"):
        out.append("out/, build/, upstream/ and __pycache__ never go in")
    out += [f"its name {w}" for w in leak_problems(raw, terms)]
    return out


def _source_rel(plan: Plan, rel: str) -> str:
    """A release path as the project names it: app/tools/x.py in a player zip is tools/x.py."""
    if plan.edition in PLAYER_EDITIONS and rel.startswith(APP + "/"):
        return rel[len(APP) + 1:]
    return rel


def verify_zip(zip_path: Path, plan: Plan, allowed: set[str], terms) -> list[tuple[str, str]]:
    """Open the written zip again and check every entry: names, contents (check_clean's rules, the planned hashes,
    leaks, secrets, archives, size) and the SHA256SUMS.txt inside. Returns the problems."""
    expected = {n: hashlib.sha256(d).hexdigest() for n, d in entries(plan).items()}
    binaries = plan.binaries()
    bad: list[tuple[str, str]] = []
    try:
        zf = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as e:
        return [(str(zip_path), f"can't open the zip: {e}")]
    with zf, tempfile.TemporaryDirectory(prefix="make_release-") as tmp:
        names = [i.orig_filename for i in zf.infolist()]  # (as stored: zipfile turns \ into / on Windows)
        if len(set(names)) != len(names):
            bad.append(("(zip)", "a name is in it twice"))
        try:
            broken = zf.testzip()
        except Exception as e:  # (a damaged deflate stream raises zlib.error, not BadZipFile)
            return bad + [("(zip)", f"damaged ({e})")]
        if broken:
            return bad + [(broken, "damaged (CRC doesn't match)")]
        for name in sorted(set(expected) - set(names)):
            bad.append((name, "planned but missing from the zip"))
        out_root = Path(tmp)
        on_disk: list[str] = []
        for info in zf.infolist():
            name = info.orig_filename
            why = name_problems(name, plan.folder, terms)
            if name not in expected:
                why.append("not planned")
            why += size_problems(info.file_size)
            if why:
                bad.append((name, "; ".join(why)))
                continue
            data = zf.read(info)
            rel = name.split("/", 1)[1]
            src = _source_rel(plan, rel)
            why = content_problems(src, data, terms, allowed)
            if hashlib.sha256(data).hexdigest() != expected[name]:
                why.append("content differs from the file that was checked")
            if rel in binaries:
                why += pe_problems(data, binaries[rel].export, binaries[rel].host)
            elif src.lower().startswith("build/") or src.split("/")[0].lower() in NEVER_TOP:
                why.append("build/ only holds the chosen DLLs")
            if plan.edition in PLAYER_EDITIONS and rel == STARTER_NAME:
                why += starter_problems(data, terms)
            if why:
                bad.append((name, "; ".join(why)))
                continue
            dest = out_root.joinpath(*rel.split("/"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            on_disk.append(rel)
        # check_clean's rules on the files as they came out of the zip
        markers: dict[str, str] = {}
        for rel in on_disk:
            if _source_rel(plan, rel) in allowed:
                continue
            why = cc.problems_for(out_root, rel, markers)
            if why:
                bad.append((f"{plan.folder}/{rel}", "; ".join(why)))
        # the list inside matches the files
        sums_name = f"{plan.folder}/{SUMS}"
        if sums_name in names:
            inside = zf.read(sums_name).decode("utf-8", "replace")
            actual = (f"# {plan.stamp}\n" if plan.stamp else "") + "".join(
                f"{hashlib.sha256(zf.read(n)).hexdigest()}  {n.split('/', 1)[1]}\n"
                for n in sorted(names) if n != sums_name and n in expected)
            if inside != actual:
                bad.append((sums_name, "doesn't match the files in the zip"))
    return sorted(set(bad))


def _remove(*paths: Path) -> None:
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def write_release(plan: Plan, out_dir: Path, allowed: set[str], terms) -> tuple[Path, Path]:
    """Write the zip and the SHA-256 list next to it, then check the zip again. On a failed check the zip is
    removed and ReleaseError lists why. When a file can't be written (a locked old zip, a full disk), WriteError
    says what is left; no .tmp file is left behind, and the list next to the zip never describes another zip."""
    if plan.refused:
        raise ReleaseError("refused", plan.refused)
    zip_path = out_dir / plan.zip_name
    sums_path = out_dir / plan.sums_name
    tmp_zip = out_dir / (plan.zip_name + ".tmp")
    tmp_sums = out_dir / (plan.sums_name + ".tmp")
    data = zip_bytes(entries(plan))
    sums = (sums_text(plan.items, plan.folder + "/", plan.stamp)
            + f"{hashlib.sha256(data).hexdigest()}  {plan.zip_name}\n").encode("utf-8")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp_zip.write_bytes(data)
    except OSError as e:
        _remove(tmp_zip)
        raise WriteError(f"can't write in {out_dir} ({e.strerror or e}). Nothing was changed.")
    try:
        problems = verify_zip(tmp_zip, plan, allowed, terms)
    except BaseException:
        _remove(tmp_zip)
        raise
    if problems:
        _remove(tmp_zip)
        raise ReleaseError("the written zip failed its check", problems)
    try:
        tmp_sums.write_bytes(sums)
        os.replace(tmp_zip, zip_path)
    except OSError as e:
        _remove(tmp_zip, tmp_sums)
        raise WriteError(f"can't replace {zip_path} ({e.strerror or e}). Is it open in another program? Close it and"
                         f" run this again. Nothing was changed.")
    try:
        os.replace(tmp_sums, sums_path)
    except OSError as e:
        _remove(tmp_sums)
        try:
            sums_path.unlink(missing_ok=True)
            left = "The old list was deleted."
        except OSError:
            left = f"The old list there is out of date: delete {sums_path}."
        raise WriteError(f"wrote {zip_path}, but not the SHA-256 list next to it ({e.strerror or e}). {left}"
                         f" The list inside the zip is right.")
    return zip_path, sums_path


# ------------------------------------------------------------------ command line

def human(n: int) -> str:
    return f"{n} bytes" if n < 1024 else f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / 1048576:.1f} MB"


def show_refused(refused) -> None:
    for rel, why in refused:
        print(f"  REFUSED {rel}: {why}")


def out_problem(out_dir: Path, root: Path) -> str:
    """--out may not be a folder the next release would pack (inside the project, other than under out/)."""
    real = os.path.realpath(out_dir)
    if _inside(real, root) and not _inside(real, root / "out"):
        return (f"--out {out_dir} is inside the project, so the next release would pack this one. Use a folder"
                f" under out\\ (the default is out\\release) or one outside the project.")
    return ""


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="make_release.py", description="Build clean Breathalyzer release zips.")
    ap.add_argument("--edition", choices=(*EDITIONS, "players"), default="source",
                    help="core: launcher, no mods; plus: with our own mods; players: both; source: the repo for "
                         "developers (default)")
    ap.add_argument("--version", default=VERSION, help=f"version in the zip's name (default {VERSION})")
    ap.add_argument("--with-binaries", action="store_true",
                    help="source zip: also modhost.dll and the example mods from build/native (only those that are "
                         "built)")
    ap.add_argument("--with-experimental", action="store_true",
                    help="plus zip: also the mods bundled-mods.toml marks ready = false that are built (off, marked "
                         "Experimental)")
    ap.add_argument("--runtime", type=Path, default=None,
                    help="player zips: the runtime folder (default out/packaging-work/runtime)")
    ap.add_argument("--starter", type=Path, default=None,
                    help="player zips: Start Breathalyzer.exe (default out/packaging-work/starter/)")
    ap.add_argument("--commit", default="HEAD",
                    help="player zips: the commit their files come from (default HEAD); the DLLs are built from it too")
    ap.add_argument("--overlay", action="append", default=[], metavar="PATH",
                    help="player zips, test builds only: also take this uncommitted file from the working tree (listed "
                         "with its SHA-256 in SHA256SUMS.txt; such zips are not for upload)")
    ap.add_argument("--working-tree", action="store_true",
                    help="player zips, developers' tests only: take the working tree as it is and build/native as "
                         "it is (stamped as a test build, not for upload)")
    ap.add_argument("--install", type=Path, default=None,
                    help="player zips: the Savage Wind folder whose zig builds the DLLs (default: build_native's)")
    ap.add_argument("--dry-run", action="store_true", help="only list what would go in; write nothing")
    ap.add_argument("--out", type=Path, default=None,
                    help="folder for the zip (default out/release; any folder outside the project, or under out/)")
    return ap


def groups(items) -> list[tuple[str, int, int]]:
    """(name, files, bytes) for a player zip's dry run: the files next to app/, and app/'s folders and files."""
    out: dict[str, list[int]] = {}
    for i in items:
        parts = i.rel.split("/")
        key = (f"{parts[0]}/{parts[1]}/" if parts[0] == APP and len(parts) > 2 else
               f"{parts[0]}/" if len(parts) > 2 else i.rel)
        n = out.setdefault(key, [0, 0])
        n[0] += 1
        n[1] += len(i.data)
    return [(k, v[0], v[1]) for k, v in sorted(out.items())]


def show_dry_run(plan: Plan) -> None:
    total = sum(len(i.data) for i in plan.items)
    if plan.edition in PLAYER_EDITIONS:
        print(f"{plan.zip_name} ({plan.edition}):")
        for name, n, size in groups(plan.items):
            print(f"  {name}  ({human(size)}" + (f" in {n} files)" if n > 1 else ")"))
        for m in plan.bundled:
            print(f"  mod: {m['title']} ({m['default']}{', experimental' if m['experimental'] else ''})")
    else:
        for i in plan.items:
            print(f"  {i.rel}  ({human(len(i.data))})")
    for rel in plan.not_built:
        print(f"  not built, left out: {rel}")
    show_refused(plan.refused)
    if plan.refused:
        print(f"Would refuse {plan.zip_name}: {len(plan.refused)} file(s) blocked. Nothing written (dry run).")
    else:
        print(f"Would write {plan.zip_name}: {len(plan.items)} file(s) + {SUMS}, {human(total)} before"
              f" compression. Nothing written (dry run).")


def main(argv: list[str] | None = None, *, root: Path = REPO, build_dir: Path | None = None, terms=None,
         forbidden=None) -> int:
    try:
        a = parser().parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 2
    if not valid_version(a.version):
        print(f"Bad version {a.version!r}: use letters, digits, '.', '-', '+' or '_' (like 0.1.0-dev).",
              file=sys.stderr)
        return 2
    editions = PLAYER_EDITIONS if a.edition == "players" else (a.edition,)
    if a.with_binaries and a.edition != "source":
        print("--with-binaries is for --edition source: the player zips always hold modhost.dll (and plus its mods).",
              file=sys.stderr)
        return 2
    if a.with_experimental and "plus" not in editions:
        print("--with-experimental is for the plus zip (--edition plus or players).", file=sys.stderr)
        return 2
    out_dir = a.out if a.out is not None else (DEFAULT_OUT if root == REPO else root / "out" / "release")
    bad_out = out_problem(out_dir, root)
    if bad_out:
        print(bad_out, file=sys.stderr)
        return 2
    players = a.edition != "source"
    if not players and (a.overlay or a.working_tree or a.commit != "HEAD"):
        print("--commit, --overlay and --working-tree are for the player zips (--edition core, plus or players).",
              file=sys.stderr)
        return 2
    if a.working_tree and a.overlay:
        print("--overlay lays files over a commit; --working-tree takes them all from the working tree: pick one.",
              file=sys.stderr)
        return 2
    terms = leak_terms(root) if terms is None else terms
    src_root, files, commit, stamp = root, None, "", {}
    runtime_dir = a.runtime if a.runtime is not None else default_work(root) / "runtime"
    starter = a.starter if a.starter is not None else default_work(root) / "starter" / STARTER_NAME
    try:
        if players and a.working_tree:
            head = next(iter(_git_lines(root, "rev-parse", "--verify", "--quiet", "HEAD")), "").strip()
            dirty = [x for x in _git(root, "status", "--porcelain", "-z", "--untracked-files=all") if len(x) > 3]
            for e in editions:
                stamp[e] = (f"Breathalyzer{' Plus' if e == 'plus' else ''} {a.version}, built from the working tree "
                            f"at commit {head or '(none yet)'} ({len(dirty)} changed file(s)). A test build: not for "
                            f"upload.")
        elif players:
            src = take_snapshot(root, a.commit, a.overlay)
            print(f"Taking the files from commit {src.commit[:12]}"
                  + (f" plus {len(src.local)} uncommitted file(s) (a test build)" if src.local else "")
                  + f": {src.root}")
            if build_dir is None:
                print("Building the DLLs from that commit (low priority)...")
                build_dir = build_snapshot_native(src, a.install)
            src_root, files, commit = src.root, src.files, src.commit
            stamp = {e: src.stamp(e, a.version) for e in editions}
        plans = [plan_release(src_root, a.version, a.with_binaries, build_dir, terms, forbidden, edition=e,
                              runtime_dir=runtime_dir, starter=starter, experimental=a.with_experimental,
                              files=files, commit=commit, stamp=stamp.get(e, ""))
                 for e in editions]
    except ReleaseError as e:
        print(f"Refused: {e}")
        show_refused(e.problems)
        print("Nothing written.")
        return 1

    if a.dry_run:
        for plan in plans:
            show_dry_run(plan)
        return 1 if any(p.refused for p in plans) else 0

    if any(p.refused for p in plans):
        for plan in plans:
            if plan.refused:
                print(f"Refused {plan.zip_name}: {len(plan.refused)} file(s) blocked.")
                show_refused(plan.refused)
        print("Nothing written.")
        return 1
    for plan in plans:
        try:
            zip_path, sums_path = write_release(plan, out_dir, cc.read_allow(src_root), terms)
        except WriteError as e:
            print(f"Couldn't write the release: {e}")
            return EXIT_WRITE_FAILED
        except ReleaseError as e:
            print(f"Refused {plan.zip_name}: {e}. Nothing written for it.")
            show_refused(e.problems)
            return 1
        size = zip_path.stat().st_size
        dlls = len(plan.binaries())
        print(f"Wrote {zip_path}: {len(plan.items)} file(s) + {SUMS}, {human(size)}."
              + (f" {dlls} DLL(s) in it." if a.with_binaries or plan.edition in PLAYER_EDITIONS else ""))
        for m in plan.bundled:
            print(f"  mod: {m['title']} ({m['default']}{', experimental' if m['experimental'] else ''})")
        for rel in plan.not_built:
            print(f"Not built, left out: {rel}")
        print(f"SHA-256 list: {sums_path}")
        if plan.stamp:
            print(f"  {plan.stamp}")
    print("Checked again after writing: clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
