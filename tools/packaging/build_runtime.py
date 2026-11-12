#!/usr/bin/env python3
"""Build Breathalyzer's own Python (the app\\runtime folder of the player zips), so players need no Python install.

    python tools/packaging/build_runtime.py           build it in out\\packaging-work\\runtime, check it, record it
    python tools/packaging/build_runtime.py --check   only check that folder against tools/packaging/runtime.lock.json

What it is: python.org's Python for Windows (64-bit), laid out like python.org's "embeddable package" (python.exe,
pythonw.exe, the DLLs, the standard library compiled into python3XX.zip and a python3XX._pth file that keeps it
apart from any other Python on the PC), plus Tcl/Tk for the window, from the same python.org build. Nothing is
downloaded: the files are copied from this PC's install of the python.org installer (the Python running this script,
or --from-install), and the script refuses unless every .exe, .dll and .pyd carries a valid signature of the Python
Software Foundation (vcruntime: Microsoft), checked with PowerShell's Get-AuthenticodeSignature.

Left out: pip, site-packages, the tests, IDLE, turtledemo, venv, Tcl's time zone data and Tk's demos. The standard
library is compiled by this Python (it must be the same version), with hash-based .pyc files whose file names are
neutral (no path of this PC).

Then it runs the new Python once, with an empty environment: sys.path must stay inside the runtime and app folders,
and tkinter, ssl, ctypes, sqlite3, asyncio, zipfile and the rest must import (a hidden Tk window is made and closed).
Last, every file's SHA-256 goes into tools/packaging/runtime.lock.json, which make_release.py checks.

Exit code 0: built (or the check passed). 1: refused or failed (the reasons are listed). 2: bad arguments.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import marshal
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import make_release as mr  # noqa: E402 (the lock check, shared with the release)

LOCK = HERE / "runtime.lock.json"
DEFAULT_OUT = REPO / "out" / "packaging-work" / "runtime"
BELOW_NORMAL = 0x4000
FIXED_DATE = (1980, 1, 1, 0, 0, 0)

V = f"{sys.version_info.major}{sys.version_info.minor}"   # "313"
ROOT_FILES = ("python.exe", "pythonw.exe", "python3.dll", f"python{V}.dll", "vcruntime140.dll", "vcruntime140_1.dll",
              "LICENSE.txt")
# From DLLs\: what python.org's embeddable package has, plus Tk (_tkinter, tcl86t, tk86t and the zlib1 Tcl needs).
EMBED_DLLS = ("_asyncio.pyd", "_bz2.pyd", "_ctypes.pyd", "_decimal.pyd", "_elementtree.pyd", "_hashlib.pyd",
              "_lzma.pyd", "_multiprocessing.pyd", "_overlapped.pyd", "_queue.pyd", "_socket.pyd", "_sqlite3.pyd",
              "_ssl.pyd", "_uuid.pyd", "_wmi.pyd", "_zoneinfo.pyd", "pyexpat.pyd", "select.pyd", "unicodedata.pyd",
              "winsound.pyd", "libcrypto-3.dll", "libffi-8.dll", "libssl-3.dll", "sqlite3.dll")
TK_DLLS = ("_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "zlib1.dll")
# Lib\ folders (and top-level modules) never compiled in.
STDLIB_SKIP = {"site-packages", "test", "tests", "idletest", "idlelib", "turtledemo", "ensurepip", "venv",
               "pydoc_data", "__pycache__", "lib2to3", "distutils", "__phello__", "curses"}
# tcl\: the Tcl and Tk script libraries and Tcl's modules; not tzdata (Tk doesn't use it), demos or pictures.
TCL_KEEP = ("tcl8.6", "tk8.6", "tcl8")
TCL_SKIP = {"tzdata", "demos", "images"}
TCL_SKIP_EXT = {".lib", ".sh", ".a"}
SIGNERS = {"default": "Python Software Foundation", "vcruntime": "Microsoft Corporation"}  # (the certificate's O=)
BINARY_EXT = {".exe", ".dll", ".pyd"}
PTH_LINES = (f"python{V}.zip", ".", r"..\tools")  # (relative to the runtime folder: app\tools is the tools)
TCL_LICENCE = "tcl/tcl8.6/license.terms"  # (python.org's install has Tk's license.terms but not Tcl's: --tcl-licence)
LICENCES = {"LICENSE.txt": "Python (PSF License v2), with the licences of the libraries it includes",
            TCL_LICENCE: "Tcl (BSD-style)", "tcl/tk8.6/license.terms": "Tk (BSD-style)"}

SMOKE = r"""
import os, sys
rt = os.path.dirname(os.path.abspath(sys.executable))
app = os.path.dirname(rt)
def inside(p):
    p = os.path.normcase(os.path.abspath(p))
    return p == os.path.normcase(app) or p.startswith(os.path.normcase(app) + os.sep)
bad = [p for p in sys.path if p and not inside(p)]
assert not bad, f"sys.path leaves the runtime: {bad}"
assert "site" not in sys.modules, "site was imported"
assert sys.flags.isolated or sys.flags.ignore_environment, "not isolated"
import asyncio, bz2, ctypes, decimal, hashlib, json, lzma, multiprocessing, queue, select, socket, sqlite3, ssl
import tkinter, unicodedata, uuid, xml.etree.ElementTree, zipfile, zoneinfo, argparse, tomllib, subprocess
from tkinter import filedialog, font, messagebox, ttk
r = tkinter.Tk()
r.withdraw()
tk_version = r.tk.call("info", "patchlevel")
r.destroy()
print("OK", sys.version.split()[0], "Tk", tk_version, ssl.OPENSSL_VERSION)
"""


class RuntimeBuildError(RuntimeError):
    """The runtime can't be built; the message says why."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------ the standard library

def stdlib_modules(lib: Path) -> list[tuple[str, Path]]:
    """(name in the zip, .py file) for every module of Lib that goes in, sorted."""
    out = []
    for dirpath, dirnames, filenames in os.walk(lib):
        dirnames[:] = sorted(d for d in dirnames if d not in STDLIB_SKIP and not d.startswith("."))
        rel = Path(dirpath).relative_to(lib)
        for f in sorted(filenames):
            if not f.endswith(".py") or (not rel.parts and f[:-3] in STDLIB_SKIP):
                continue
            out.append(((rel / f).as_posix(), Path(dirpath) / f))
    return sorted(out)


def pyc(source: bytes, name: str) -> bytes:
    """A hash-based, unchecked .pyc (no file dates in it) whose file name is the module's path in the zip."""
    code = compile(source, name, "exec", dont_inherit=True, optimize=0)
    flags = 0b01  # hash-based; source not checked (there is no source)
    return (importlib.util.MAGIC_NUMBER + flags.to_bytes(4, "little") + importlib.util.source_hash(source)
            + marshal.dumps(code))


def stdlib_zip(lib: Path) -> bytes:
    """The standard library, compiled, as python3XX.zip: sorted names, fixed dates (the same files, the same zip)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, path in stdlib_modules(lib):
            zi = zipfile.ZipInfo(name[:-3] + ".pyc", date_time=FIXED_DATE)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = mr.FILE_MODE << 16
            try:
                zf.writestr(zi, pyc(path.read_bytes(), name), compresslevel=9)
            except SyntaxError as e:  # (a broken file in Lib: say which)
                raise RuntimeBuildError(f"Lib/{name} doesn't compile: {e}") from None
    return buf.getvalue()


# ------------------------------------------------------------------ copying

def tcl_files(tcl: Path) -> list[tuple[str, Path]]:
    out = []
    for top in TCL_KEEP:
        base = tcl / top
        if not base.is_dir():
            raise RuntimeBuildError(f"No tcl\\{top} in the Python install: install Python with Tcl/Tk (tkinter).")
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in TCL_SKIP)
            for f in sorted(filenames):
                if os.path.splitext(f)[1].lower() in TCL_SKIP_EXT:
                    continue
                p = Path(dirpath) / f
                out.append((Path("tcl", p.relative_to(tcl)).as_posix(), p))
    return out


def plan_copy(install: Path) -> list[tuple[str, Path]]:
    """(name in the runtime, file in the install) for every copied file. Raises when one is missing."""
    files = [(n, install / n) for n in ROOT_FILES]
    files += [(n, install / "DLLs" / n) for n in (*EMBED_DLLS, *TK_DLLS)]
    missing = [n for n, p in files if not p.is_file()]
    if missing:
        raise RuntimeBuildError(f"Not a full python.org install (missing {', '.join(missing)}). Install Python "
                                f"{sys.version.split()[0]} from python.org with Tcl/Tk, or name one with --from-install.")
    return files + tcl_files(install / "tcl")


def check_install(install: Path) -> None:
    """The install must be the Python running this script (the .pyc files are made for it)."""
    here = Path(sys.base_prefix).resolve()
    if os.path.normcase(str(install.resolve())) != os.path.normcase(str(here)):
        exe = install / "python.exe"
        if not exe.is_file():
            raise RuntimeBuildError(f"No python.exe in {install}.")
        r = subprocess.run([str(exe), "-I", "-c", "import sys; print(sys.version)"], capture_output=True,
                           encoding="utf-8", errors="replace")
        if r.stdout.strip() != sys.version:
            raise RuntimeBuildError("That Python isn't the one running this script, so its standard library can't "
                                    "be compiled for it. Run this script with that Python instead.")
    if os.name != "nt" or sys.maxsize < 2 ** 32 or "MSC" not in sys.version:
        raise RuntimeBuildError("This needs python.org's 64-bit Python for Windows.")


def signatures(folder: Path, names: list[str]) -> dict[str, tuple[str, str]]:
    """{file name: (signature status, signer's subject)} for files in folder, by PowerShell's
    Get-AuthenticodeSignature (Windows checks the signature and its certificate chain)."""
    if not names:
        return {}
    ps = ("$ErrorActionPreference='Stop'; $names = [Console]::In.ReadToEnd() -split \"`r?`n\" | ? { $_ }; "
          "$out = foreach ($n in $names) { $s = Get-AuthenticodeSignature -LiteralPath (Join-Path $env:RT_DIR $n); "
          "[pscustomobject]@{n=$n; s=[string]$s.Status; c=$(if ($s.SignerCertificate) "
          "{ $s.SignerCertificate.Subject } else { '' })} }; ConvertTo-Json -Compress -InputObject @($out)")
    env = dict(os.environ, RT_DIR=str(folder))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], input="\n".join(names),
                           capture_output=True, encoding="utf-8", errors="replace", env=env, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeBuildError(f"PowerShell is needed to check the signatures, and it failed ({e}).") from None
    if r.returncode:
        raise RuntimeBuildError(f"Checking the signatures failed: {r.stderr.strip()[-400:]}")
    rows = json.loads(r.stdout or "[]")
    return {row["n"]: (row["s"], row["c"]) for row in rows}


def signer_problems(sigs: dict[str, tuple[str, str]], names: list[str]) -> list[str]:
    out = []
    for n in names:
        status, subject = sigs.get(n, ("missing", ""))
        want = SIGNERS["vcruntime" if Path(n).name.lower().startswith("vcruntime") else "default"]
        if status != "Valid":
            out.append(f"{n}: signature {status}")
        elif f"O={want}" not in [x.strip() for x in subject.split(",")]:
            out.append(f"{n}: signed by {subject or 'nobody'}, not {want}")
    return out


def signer_name(subject: str) -> str:
    for part in subject.split(","):
        if part.strip().startswith("CN="):
            return part.strip()[3:]
    return subject


# ------------------------------------------------------------------ build, smoke test, lock

def build(install: Path, out: Path, check_signatures: bool = True, tcl_licence: Path | None = None) -> dict:
    """Make the runtime in out (emptied first) and return its lock. tcl_licence: Tcl's license.terms (from the Tcl
    source release of the same version), which python.org's install doesn't have."""
    check_install(install)
    files = plan_copy(install)
    if tcl_licence is not None:
        text = tcl_licence.read_bytes()
        if b"Regents of the University of" not in text:
            raise RuntimeBuildError(f"{tcl_licence} doesn't look like Tcl's license.terms.")
        files.append((TCL_LICENCE, tcl_licence))
    if out.exists():
        if not mr._inside(os.path.realpath(out), REPO / "out" / "packaging-work") and any(out.iterdir()):
            raise RuntimeBuildError(f"{out} isn't empty and isn't under out\\packaging-work: not emptied.")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name, src in files:
        dest = out / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    (out / f"python{V}.zip").write_bytes(stdlib_zip(install / "Lib"))
    (out / f"python{V}._pth").write_text("\n".join((
        "# Breathalyzer's own Python: only these folders, never site-packages or another Python's (no 'import site').",
        *PTH_LINES)) + "\n", encoding="utf-8")
    binaries = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.suffix.lower() in BINARY_EXT)
    signed: dict[str, str] = {}
    if check_signatures:
        sigs = signatures(out, binaries)
        bad = signer_problems(sigs, binaries)
        if bad:
            raise RuntimeBuildError("Not signed as python.org's files are: " + "; ".join(bad))
        signed = {n: signer_name(sigs[n][1]) for n in binaries}
    smoke = smoke_test(out)
    return make_lock(out, signed, smoke)


def smoke_test(runtime: Path) -> str:
    """Run the new Python once in an empty environment (as on a PC without Python); returns its OK line."""
    keep = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
            "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "HOMEDRIVE", "HOMEPATH")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PATH"] = os.environ.get("SYSTEMROOT", r"C:\Windows") + r"\System32"
    r = subprocess.run([str(runtime / "python.exe"), "-I", "-c", SMOKE], capture_output=True, encoding="utf-8",
                       errors="replace", env=env, cwd=str(runtime), timeout=300)
    line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    if r.returncode or not line.startswith("OK "):
        raise RuntimeBuildError(f"The new Python doesn't work: {(r.stderr or r.stdout).strip()[-800:]}")
    return line


def make_lock(runtime: Path, signed: dict[str, str], smoke: str) -> dict:
    files = {}
    for p in sorted(runtime.rglob("*")):
        if p.is_file():
            data = p.read_bytes()
            files[p.relative_to(runtime).as_posix()] = {"sha256": sha256(data), "size": len(data)}
    ver = sys.version.split()[0]
    return {
        "format": 1,
        "what": "Breathalyzer's own Python (tools/packaging/build_runtime.py): make_release.py ships exactly these "
                "files as app/runtime.",
        "python": ver,
        "python_build": sys.version.split("(", 1)[1].split(",")[0] if "(" in sys.version else "",
        "source": "python.org's Python for Windows (64-bit) and its Tcl/Tk, copied from an install of the python.org "
                  "installer; the standard library compiled by that Python. Nothing downloaded.",
        "source_url": f"https://www.python.org/ftp/python/{ver}/",
        "licences": {k: v for k, v in LICENCES.items() if k in files},
        "smoke_test": smoke,
        "signed": signed,
        "files": files,
        "total_bytes": sum(f["size"] for f in files.values()),
    }


def write_lock(lock: dict, path: Path = LOCK) -> None:
    path.write_text(json.dumps(lock, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def below_normal() -> None:
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL)
        except Exception:  # (no priority change possible: build anyway)
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="build_runtime.py", description="Build Breathalyzer's own Python.")
    ap.add_argument("--from-install", type=Path, default=None,
                    help="a python.org install of the same version as the Python running this (default: that one)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="the runtime folder (default out/packaging-work/"
                                                                  "runtime)")
    ap.add_argument("--check", action="store_true", help="only check the folder against runtime.lock.json")
    ap.add_argument("--tcl-licence", type=Path, default=None,
                    help="Tcl's license.terms (from the Tcl source release of the same version): python.org's install"
                         " has only Tk's")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 2
    below_normal()
    if a.check:
        try:
            lock = json.loads(LOCK.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"Can't read {LOCK.name}: {e}")
            return 1
        problems = mr.runtime_lock_problems(a.out, lock)
        for rel, why in problems:
            print(f"  {rel}: {why}")
        print("Differs from the lock." if problems else f"Matches the lock: {len(lock['files'])} files.")
        return 1 if problems else 0
    try:
        lock = build(a.from_install or Path(sys.base_prefix), a.out, tcl_licence=a.tcl_licence)
    except (RuntimeBuildError, OSError, subprocess.SubprocessError) as e:
        print(f"Couldn't build the runtime: {e}")
        return 1
    write_lock(lock)
    print(f"Built {a.out}: {len(lock['files'])} files, {lock['total_bytes'] / 1048576:.1f} MB "
          f"({len(lock['signed'])} signed binaries checked).")
    print(f"Works: {lock['smoke_test']}")
    if TCL_LICENCE not in lock["files"]:
        print(f"Note: no Tcl licence ({TCL_LICENCE}). Add Tcl's license.terms with --tcl-licence before a public "
              f"release (docs/release.md).")
    print(f"Recorded in {LOCK.relative_to(REPO).as_posix()}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
