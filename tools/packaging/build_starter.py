#!/usr/bin/env python3
"""Build "Start Breathalyzer.exe", the small program players open (tools/packaging/starter/start.c).

    python tools/packaging/build_starter.py            build it into out\\packaging-work\\starter and record its hash
    python tools/packaging/build_starter.py --check    build it again in a temp folder: same bytes as recorded?
    python tools/packaging/build_starter.py --install "D:\\Games\\Savage Wind"    another Savage Wind folder

It is compiled with the zig that ships with Savage Wind (as tools/build_native.py does), without any C runtime, and
linked with only kernel32 and user32. Its resources (the icon from assets/breathalyzer.ico, the version info and a
manifest: run as the user, never as admin) are written by this script, so no resource compiler is needed. It is
built twice and both must be the same bytes; then its SHA-256 goes into tools/packaging/starter.lock.json, which
make_release.py checks. Nothing is downloaded.

Exit code 0: built (or the check passed). 1: the build failed or differs. 2: bad arguments.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import build_native as bn  # noqa: E402 (Savage Wind's zig)
import make_release as mr  # noqa: E402 (the starter's checks)

SOURCE = HERE / "starter" / "start.c"
ICON = REPO / "assets" / "breathalyzer.ico"
LOCK = HERE / "starter.lock.json"
DEFAULT_OUT = REPO / "out" / "packaging-work" / "starter"
EXE = mr.STARTER_NAME  # "Start Breathalyzer.exe"
VERSION = (1, 0, 0, 0)  # the starter's own version: the same file serves every release (its hash stays known)
LANG = 0x0409           # English (US)
CODEPAGE = 0x04B0       # Unicode
BELOW_NORMAL = 0x4000

CFLAGS = ("-target", bn.TARGET, "-std=gnu11", "-O2", "-g0", "-Wall", "-Wextra", "-ffreestanding", "-fno-builtin",
          "-fno-stack-protector", "-mno-stack-arg-probe", "-DUNICODE", "-D_UNICODE")
LDFLAGS = ("-NOLOGO", "-ENTRY:start", "-SUBSYSTEM:WINDOWS", "-NODEFAULTLIB", "-MACHINE:X64", "-Brepro", "-OPT:REF",
           "-OPT:ICF", "-DYNAMICBASE", "-HIGHENTROPYVA", "-NXCOMPAT")
LIBS = ("kernel32.lib", "user32.lib")

STRINGS = {
    "CompanyName": "giruka",
    "FileDescription": "Start Breathalyzer",
    "FileVersion": ".".join(map(str, VERSION)),
    "InternalName": "start",
    "LegalCopyright": "Copyright (c) 2026 giruka and contributors. MIT License.",
    "OriginalFilename": EXE,
    "ProductName": "Breathalyzer",
    "ProductVersion": ".".join(map(str, VERSION)),
}

MANIFEST = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity type="win32" name="giruka.Breathalyzer.Start" version="1.0.0.0" processorArchitecture="amd64"/>
  <description>Start Breathalyzer</description>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
    </application>
  </compatibility>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness>
    </windowsSettings>
  </application>
  <dependency>
    <dependentAssembly>
      <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0"
                        processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*"/>
    </dependentAssembly>
  </dependency>
</assembly>
"""


class StarterError(RuntimeError):
    """The starter couldn't be built; the message says why."""


# ------------------------------------------------------------------ resources (.res, as rc.exe writes it)

RT_ICON, RT_GROUP_ICON, RT_VERSION, RT_MANIFEST = 3, 14, 16, 24


def _pad4(b: bytes) -> bytes:
    return b + b"\0" * (-len(b) % 4)


def res_entry(rtype: int, name: int, data: bytes, flags: int = 0x0030, lang: int = LANG) -> bytes:
    """One resource of a .res file: header (numeric type and name), then the data, padded to 4 bytes."""
    head = struct.pack("<IIHHHHIHHII", len(data), 32, 0xFFFF, rtype, 0xFFFF, name, 0, flags, lang, 0, 0)
    return head + _pad4(data)


def ico_images(data: bytes) -> list[tuple[bytes, bytes]]:
    """The pictures of an .ico file: (its 12-byte directory fields without the offset, the picture's bytes)."""
    if len(data) < 6 or struct.unpack_from("<HHH", data, 0)[:2] != (0, 1):
        raise StarterError("the icon isn't an .ico file")
    count = struct.unpack_from("<H", data, 4)[0]
    out = []
    for i in range(count):
        e = 6 + 16 * i
        if e + 16 > len(data):
            raise StarterError("the .ico file is cut short")
        fields = data[e:e + 12]  # width, height, colours, reserved, planes, bits, size
        size, offset = struct.unpack_from("<II", data, e + 8)
        if offset + size > len(data) or not size:
            raise StarterError("the .ico file is cut short")
        out.append((fields, data[offset:offset + size]))
    if not out:
        raise StarterError("the .ico file holds no pictures")
    return out


def _ver_block(key: str, value: bytes = b"", value_length: int = 0, wtype: int = 1, children=()) -> bytes:
    """One block of a VS_VERSIONINFO tree: length, value length, type, key, then the value and the children, each
    starting on 4 bytes."""
    body = struct.pack("<HH", value_length, wtype) + (key + "\0").encode("utf-16-le")
    body = _pad4(b"\0\0" + body)[2:]  # (padded as if the 2-byte length were already in front)
    body += value
    for child in children:
        body = _pad4(b"\0\0" + body)[2:] + child
    return struct.pack("<H", len(body) + 2) + body


def version_info(strings: dict[str, str], version=VERSION) -> bytes:
    ms, ls = (version[0] << 16) | version[1], (version[2] << 16) | version[3]
    fixed = struct.pack("<13I", 0xFEEF04BD, 0x00010000, ms, ls, ms, ls, 0x3F, 0, 0x00040004, 1, 0, 0, 0)
    table = _ver_block(f"{LANG:04X}{CODEPAGE:04X}", children=[
        _ver_block(k, (v + "\0").encode("utf-16-le"), len(v) + 1) for k, v in strings.items()])
    sfi = _ver_block("StringFileInfo", children=[table])
    vfi = _ver_block("VarFileInfo", children=[
        _ver_block("Translation", struct.pack("<HH", LANG, CODEPAGE), 4, wtype=0)])
    return _ver_block("VS_VERSION_INFO", fixed, len(fixed), wtype=0, children=[sfi, vfi])


def make_res(icon: bytes, strings: dict[str, str] = STRINGS, manifest: str = MANIFEST) -> bytes:
    """A .res file (what rc.exe makes, and what lld-link takes): the icon, the version info and the manifest."""
    out = res_entry(0, 0, b"", flags=0, lang=0)  # (a .res file starts with an empty entry)
    images = ico_images(icon)
    group = struct.pack("<HHH", 0, 1, len(images))
    for i, (fields, img) in enumerate(images, 1):
        out += res_entry(RT_ICON, i, img, flags=0x1010)
        group += fields + struct.pack("<H", i)
    out += res_entry(RT_GROUP_ICON, 1, group, flags=0x1030)
    out += res_entry(RT_VERSION, 1, version_info(strings))
    out += res_entry(RT_MANIFEST, 1, manifest.encode("utf-8"))
    return out


# ------------------------------------------------------------------ build

def _run(cmd: list[str], env: dict, cwd: Path) -> None:
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", env=env, cwd=str(cwd),
                       creationflags=BELOW_NORMAL if os.name == "nt" else 0)
    if r.returncode:
        tail = "\n".join((r.stderr or r.stdout).strip().splitlines()[-20:])
        raise StarterError(f"zig {cmd[1]} failed:\n{tail}")


def build_once(tc: bn.Toolchain, work: Path, out: Path, icon: bytes) -> bytes:
    """Compile and link the starter into out (work holds the object, the .res and zig's cache). Returns its bytes."""
    if tc.rt is None:
        raise StarterError("This needs the zig that ships with Savage Wind (its zig\\rt folder has the Windows "
                           "libraries). Point --install at your Savage Wind folder.")
    work.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    src = work / "start.c"  # (compiled from a copy under a short relative name: no path of this PC can get in)
    shutil.copyfile(SOURCE, src)
    (work / "start.res").write_bytes(make_res(icon))
    env = bn.build_env(work)
    _run([str(tc.zig), "cc", *CFLAGS, "-c", "start.c", "-o", "start.o"], env, work)
    _run([str(tc.zig), "lld-link", *LDFLAGS, f"-LIBPATH:{tc.rt}", *LIBS, "start.o", "start.res", f"-OUT:{out}"],
         env, work)
    return out.read_bytes()


def source_hashes() -> dict[str, str]:
    return {"start.c": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            "breathalyzer.ico": hashlib.sha256(ICON.read_bytes()).hexdigest(),
            "build_starter.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def zig_version(tc: bn.Toolchain) -> str:
    r = subprocess.run([str(tc.zig), "version"], capture_output=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() or "unknown"


def build(install: Path, out_dir: Path) -> tuple[Path, dict]:
    """Build the starter twice (both must match), check it, put it in out_dir and return (its path, the lock)."""
    tc = bn.find_toolchain(install)
    icon = ICON.read_bytes()
    with tempfile.TemporaryDirectory(prefix="starter-", dir=_work_parent(out_dir)) as tmp:
        a = build_once(tc, Path(tmp) / "a", Path(tmp) / "a" / EXE, icon)
        b = build_once(tc, Path(tmp) / "b", Path(tmp) / "b" / EXE, icon)
        if a != b:
            raise StarterError("Two builds of the starter differ, so its hash can't be pinned (zig isn't building "
                               "it the same way twice).")
        problems = mr.starter_problems(a, mr.leak_terms(REPO))
        if problems:
            raise StarterError("The starter fails its checks: " + "; ".join(problems))
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / EXE
        dest.write_bytes(a)
    lock = {"format": 1, "file": EXE, "sha256": hashlib.sha256(a).hexdigest(), "size": len(a),
            "zig": zig_version(tc), "sources": source_hashes(),
            "what": "Start Breathalyzer.exe (tools/packaging/build_starter.py). make_release.py ships only this file."}
    return dest, lock


def _work_parent(out_dir: Path) -> Path:
    parent = out_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    return parent


def write_lock(lock: dict, path: Path = LOCK) -> None:
    path.write_text(json.dumps(lock, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def read_lock(path: Path = LOCK) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def below_normal() -> None:
    """Build at below-normal priority (the laptop runs warm); zig inherits it."""
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL)
        except Exception:  # (no priority change possible: build anyway)
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="build_starter.py", description="Build Start Breathalyzer.exe.")
    ap.add_argument("--install", type=Path, default=None, help="your Savage Wind folder (its zig builds the starter)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="folder for the exe (default out/packaging-work/"
                                                                  "starter)")
    ap.add_argument("--check", action="store_true", help="build it again in a temp folder and compare with the lock")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 2
    below_normal()
    install = a.install or bn.default_install()
    try:
        if a.check:
            with tempfile.TemporaryDirectory(prefix="starter-check-", dir=_work_parent(DEFAULT_OUT)) as tmp:
                path, lock = build(install, Path(tmp))
            old = read_lock()
            if lock["sha256"] != old.get("sha256"):
                print(f"Differs: a new build is {lock['sha256']}, the lock says {old.get('sha256')}.")
                return 1
            print(f"Same bytes as recorded: {lock['sha256']}")
            return 0
        path, lock = build(install, a.out)
    except (StarterError, bn.BuildError, OSError) as e:
        print(f"Couldn't build the starter: {e}")
        return 1
    write_lock(lock)
    print(f"Built {path} ({lock['size']} bytes), the same twice. SHA-256 {lock['sha256']}")
    print(f"Recorded in {LOCK.relative_to(REPO).as_posix()}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
