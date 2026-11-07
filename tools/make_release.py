#!/usr/bin/env python3
"""Build a clean release zip of Breathalyzer: the mod loader's tools, native sources, docs, examples and tests.
Never game files, keys, saves, logs or anything made from the game.

    python tools/make_release.py                          out/release/breathalyzer-<VERSION>.zip
    python tools/make_release.py --version 0.2.0         name another version
    python tools/make_release.py --with-binaries         also the DLLs build_native.py built (only those that exist)
    python tools/make_release.py --dry-run               only list what would go in; write nothing

What goes in (an allow-list): files git tracks, and files git would commit under tools/, native/, docs/, tests/,
.github/, plus README.md, LICENSE, NOTICE.md, .gitignore and .cleanallow. git is needed: without it (or when git
fails) the tool refuses, because only git knows what .gitignore keeps out. Never out/, build/, upstream/ or
__pycache__, in any letter case. With --with-binaries: modhost.dll and the example mods from build/native, each
checked to be a 64-bit DLL with the export build_native.py gives it, never swgame.dll and never a file from the
Savage Wind folder (not even through a hard link).

Checks before writing: tools/check_clean.py's rules on every file, plus: no user name, home folder path, repo path,
git e-mail or git name in any file or file name; no private keys, access tokens or key files; zip and gzip files are
opened and every file in them checked, other archives and compressed files are refused; no links or junctions (also
not in a folder above the file); no earlier release; no file over 20 MB. If one file fails, nothing is written and
every failure is listed. Checks after writing: the zip is opened again and every entry checked: names (no absolute
paths, no "..", only what was planned), content (same SHA-256 as planned, check_clean's rules again, the checks above).

It writes out/release/breathalyzer-<version>.zip (everything in one folder of that name, with SHA256SUMS.txt)
and out/release/breathalyzer-<version>.SHA256SUMS.txt next to it (the same list, plus the zip's own hash).
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
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_native as bn  # noqa: E402 (tools/build_native.py: the DLL targets and the PE reader)
import check_clean as cc  # noqa: E402 (tools/check_clean.py: the game-file rules)

REPO = Path(__file__).resolve().parent.parent
VERSION = "0.1.0-dev"
NAME = "breathalyzer"
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

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass
class Plan:
    version: str
    items: list[Item] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)  # (path, why)
    not_built: list[str] = field(default_factory=list)            # DLLs asked for that aren't built

    @property
    def folder(self) -> str:
        return f"{NAME}-{self.version}"

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


def archive_problems(rel: str, data: bytes, terms, cleanallowed: bool = False) -> list[str]:
    """An archive or compressed file can hide game files, keys or leaks from every other check. zip and gzip files
    are opened and each file in them checked like a file of the release (check_clean's rules are skipped only when
    the archive itself is in .cleanallow); an archive inside one, and every other kind, is refused."""
    kind = archive_kind(rel, data)
    if not kind:
        return []
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


def plan_release(root: Path = REPO, version: str = VERSION, with_binaries: bool = False,
                 build_dir: Path | None = None, terms=None, forbidden=None) -> Plan:
    """Everything that would go in, and everything that blocks the release (check_clean's rules and more).
    Raises ReleaseError when git can't list the files."""
    plan = Plan(version)
    terms = leak_terms(root) if terms is None else terms
    allowed = cc.read_allow(root)
    markers: dict[str, str] = {}
    for rel in source_files(root):
        why = name_problems(f"{plan.folder}/{rel}", plan.folder, terms) + link_problems(root, rel)
        data = b""
        if not why and rel not in allowed:
            why += cc.problems_for(root, rel, markers)
        if not why:
            data, why = _read(root / rel)
        if not why:
            why += content_problems(rel, data, terms, allowed)
        if why:
            plan.refused.append((rel, "; ".join(why)))
        else:
            plan.items.append(Item(rel, data))
    if with_binaries:
        build_dir = DEFAULT_BUILD if build_dir is None else build_dir
        forbidden = savage_wind_dirs() if forbidden is None else forbidden
        for path, export, host in binary_targets(build_dir):
            rel = f"{BUILD_PREFIX}/{path.relative_to(build_dir).as_posix()}"
            if not path.is_file():
                plan.not_built.append(rel)
                continue
            why = (name_problems(f"{plan.folder}/{rel}", plan.folder, terms)
                   + binary_problems(path, build_dir, forbidden))
            data = b""
            if not why:
                why += cc.problems_for(build_dir, path.relative_to(build_dir).as_posix(), markers)
            if not why:
                data, why = _read(path)
            if not why:
                why += pe_problems(data, export, host) + content_problems(rel, data, terms, allowed)
            if why:
                plan.refused.append((rel, "; ".join(why)))
            else:
                plan.items.append(Item(rel, data, export, host))
    plan.items.sort(key=lambda i: i.rel)
    plan.refused.sort()
    return plan


# ------------------------------------------------------------------ the zip

def sums_text(items, prefix: str = "") -> str:
    """sha256sum's format: '<hash>  <path>' per line, sorted by path. Written as UTF-8."""
    return "".join(f"{i.sha256}  {prefix}{i.rel}\n" for i in sorted(items, key=lambda i: i.rel))


def entries(plan: Plan) -> dict[str, bytes]:
    """Zip entry name -> content: every file in one folder, with SHA256SUMS.txt."""
    out = {f"{plan.folder}/{i.rel}": i.data for i in plan.items}
    out[f"{plan.folder}/{SUMS}"] = sums_text(plan.items).encode("utf-8")
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
            why = content_problems(rel, data, terms, allowed)
            if hashlib.sha256(data).hexdigest() != expected[name]:
                why.append("content differs from the file that was checked")
            if rel in binaries:
                why += pe_problems(data, binaries[rel].export, binaries[rel].host)
            elif rel.lower().startswith("build/") or rel.split("/")[0].lower() in NEVER_TOP:
                why.append("build/ only holds the chosen DLLs")
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
            if rel in allowed:
                continue
            why = cc.problems_for(out_root, rel, markers)
            if why:
                bad.append((f"{plan.folder}/{rel}", "; ".join(why)))
        # the list inside matches the files
        sums_name = f"{plan.folder}/{SUMS}"
        if sums_name in names:
            inside = zf.read(sums_name).decode("utf-8", "replace")
            actual = "".join(f"{hashlib.sha256(zf.read(n)).hexdigest()}  {n.split('/', 1)[1]}\n"
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
    sums = (sums_text(plan.items, plan.folder + "/")
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
    ap = argparse.ArgumentParser(prog="make_release.py", description="Build a clean Breathalyzer release zip.")
    ap.add_argument("--version", default=VERSION, help=f"version in the zip's name (default {VERSION})")
    ap.add_argument("--with-binaries", action="store_true",
                    help="also modhost.dll and the example mods from build/native (only those that are built)")
    ap.add_argument("--dry-run", action="store_true", help="only list what would go in; write nothing")
    ap.add_argument("--out", type=Path, default=None,
                    help="folder for the zip (default out/release; any folder outside the project, or under out/)")
    return ap


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
    out_dir = a.out if a.out is not None else (DEFAULT_OUT if root == REPO else root / "out" / "release")
    bad_out = out_problem(out_dir, root)
    if bad_out:
        print(bad_out, file=sys.stderr)
        return 2
    terms = leak_terms(root) if terms is None else terms
    try:
        plan = plan_release(root, a.version, a.with_binaries, build_dir, terms, forbidden)
    except ReleaseError as e:
        print(f"Refused: {e}")
        print("Nothing written.")
        return 1
    total = sum(len(i.data) for i in plan.items)

    if a.dry_run:
        for i in plan.items:
            print(f"  {i.rel}  ({human(len(i.data))})")
        for rel in plan.not_built:
            print(f"  not built, left out: {rel}")
        show_refused(plan.refused)
        if plan.refused:
            print(f"Would refuse: {len(plan.refused)} file(s) blocked. Nothing written (dry run).")
            return 1
        print(f"Would write {plan.zip_name}: {len(plan.items)} file(s) + {SUMS}, {human(total)} before"
              f" compression. Nothing written (dry run).")
        return 0

    if plan.refused:
        print(f"Refused: {len(plan.refused)} file(s) blocked. Nothing written.")
        show_refused(plan.refused)
        return 1
    try:
        zip_path, sums_path = write_release(plan, out_dir, cc.read_allow(root), terms)
    except WriteError as e:
        print(f"Couldn't write the release: {e}")
        return EXIT_WRITE_FAILED
    except ReleaseError as e:
        print(f"Refused: {e}. Nothing written.")
        show_refused(e.problems)
        return 1
    size = zip_path.stat().st_size
    dlls = len(plan.binaries())
    print(f"Wrote {zip_path}: {len(plan.items)} file(s) + {SUMS}, {human(size)}."
          + (f" {dlls} DLL(s) in it." if a.with_binaries else ""))
    for rel in plan.not_built:
        print(f"Not built, left out: {rel}")
    print(f"SHA-256 list: {sums_path}")
    print("Checked again after writing: clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
