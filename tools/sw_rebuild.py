#!/usr/bin/env python3
"""Rebuild Savage Wind's game code library (swgame.dll) without the GUI, for example after patching exefs/main.

    python tools/sw_rebuild.py build [<savage wind folder>] [--exefs DIR] [--out PATH] [--install] [-j N]
                                     [--hooks FILE]
    python tools/sw_rebuild.py install-patched-main <patched main> <savage wind folder>
    python tools/sw_rebuild.py restore <savage wind folder> [--only main|library]
    python tools/sw_rebuild.py status [<savage wind folder>]

`build` repeats what the released Savage Wind 0.4.1-alpha.4 does when you press "Set up game files" (its
runtime/gamebuild.c and runtime/platform/windows.c at commit 76e61af): `swgen layout`, one `swgen <module>` per
module, `zig cc` on every generated file, `zig lld-link`, then swgame.dll and swgame.dll.id. It uses the install's own
swgen\\swgen.exe and zig\\zig.exe and the id in extracted\\swgame.dll.id. The output goes to out\\rebuild\\swgame.dll;
only --install replaces extracted\\swgame.dll, after backing up the vanilla one (swgame.dll.vanilla + .id).
Compiled objects are cached per module, so a build after a main-only change reuses the sdk and subsdk0 objects.
--hooks gives the listed functions of main hook slots for native mods (tools/gen_hooks.py, docs/hooks.md).

`install-patched-main` puts a patched main into extracted\\exefs (it may be a hard link to your own dump, so the old
file is never opened for writing: a new file is written and renamed over it) and keeps the vanilla main in
mods\\.vanilla\\exefs\\main. `restore` puts the vanilla main and library back.

Savage Wind is MIT licensed; the build steps, flags and layout rules below follow its source (credited where used).
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL_ENV = "SWMOD_INSTALL"  # the Savage Wind folder when it isn't the default one (as in tools/build_native.py)
OUT_ROOT = os.path.join(REPO, "out", "rebuild")
LIB_NAME = "swgame.dll"
MODHOST_DLL = os.path.join(REPO, "build", "native", "modhost.dll")  # where tools/build_native.py build puts it
RELEASE = "0.4.1-alpha.4"
CACHE_VERSION = 1

# The release's compile flags (Makefile GENFLAGS minus -Iruntime, GENFLAGS_COMPAT, GENFLAGS_ZIG at 76e61af; the same
# strings are compiled into savagewind.exe as GAMELIB_CFLAGS, GAMELIB_CFLAGS_COMPAT and GAMELIB_ZIGFLAGS).
RELEASE_CFLAGS = {
    "normal": "-O1 -fno-strict-aliasing -Wno-return-type -Wno-unused-label -fvisibility=hidden "
              "-D_FILE_OFFSET_BITS=64 -march=x86-64-v3",
    "compat": "-O1 -fno-strict-aliasing -Wno-return-type -Wno-unused-label -fvisibility=hidden "
              "-D_FILE_OFFSET_BITS=64 -march=x86-64-v2",
}
RELEASE_ZIGFLAGS = "-Xclang -O1 -fno-vectorize -fno-slp-vectorize -g0 -fomit-frame-pointer"
# GAMELIB_ID and GAMELIB_ID_COMPAT of 0.4.1-alpha.4 (recomputed from the 76e61af sources the way its Makefile does).
RELEASE_IDS = {"767a39f088e81e1a": "normal", "b4d249669cc63ebf": "compat"}
ARCH_OF = {"normal": "-march=x86-64-v3", "compat": "-march=x86-64-v2"}

# Module layout rules (Savage Wind tools/layout.py, MIT): modules back to back in this order from MAIN_BASE, each
# page-rounded; a subsdk that the game version doesn't have is left out.
MODULES_LO = 0x7000000000
PAGE = 0x1000
LOAD_ORDER = ["main"] + [f"subsdk{i}" for i in range(10)] + ["sdk"]
GAME_SUBSDKS = {"de0b6ac4efc1dda7": ["subsdk0"], "cd57b23fa4bbad65": ["subsdk0"]}  # 1.8.2, 1.9.0

PROGRESS_RE = re.compile(r": progress (\d+)/(\d+) files (\d+)")
ID_RE = re.compile(r"^[0-9A-Za-z._-]{1,100}$")
CFG_CRASH = 0xC0000409  # what Windows ends a process with when Control Flow Guard stops it


class RebuildError(Exception):
    """Something the user has to fix; the message says what."""


def default_install() -> str:
    """The Savage Wind folder `build` and `status` use when none is given: SWMOD_INSTALL when set, else Documents\\
    SavageWind\\Savage Wind in the user's profile (%USERPROFILE%), where Savage Wind puts itself."""
    env = os.environ.get(INSTALL_ENV, "").strip()
    if env:
        return env
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(home, "Documents", "SavageWind", "Savage Wind")


def _safe_stdout() -> None:
    """Let print() take any path or name. Redirected to a file or a pipe, Windows Python writes stdout in the ANSI
    code page with strict errors, so a letter it lacks (a user folder with an L-stroke, a Chinese file name) ended
    the run in a UnicodeEncodeError that hid the real message; such letters now print as '?'. Left alone when the
    stream already has a lenient handler (as when modman calls main() after setting its own)."""
    try:
        if sys.stdout.errors in ("strict", "surrogateescape"):
            sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


# --- small file helpers -------------------------------------------------------------------------------------------

def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _tmp_name(path: str) -> str:
    return f"{path}.tmp-{os.getpid()}"


def _read_only(path: str) -> bool:
    try:
        return not os.lstat(path).st_mode & stat.S_IWRITE
    except OSError:
        return False


def _delete_name_only(path: str) -> bool:
    """Delete one name of a read-only file without clearing its read-only setting, which belongs to the file and so
    to its other names too (a hard link to the user's own dump). Windows 10 1809 or newer on NTFS
    (FILE_DISPOSITION_IGNORE_READONLY_ATTRIBUTE, as modman._delete_name_only). False when that isn't possible."""
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


def replace_name(src: str, dst: str) -> None:
    """os.replace(src, dst), also when dst is read-only, which Windows refuses ([WinError 5] Access is denied).
    Clearing read-only on dst is no way out: dst may be a hard link to the user's own dump (extracted\\exefs\\main),
    and the setting is shared by both names. So the old name is renamed aside (allowed for a read-only file), src
    is renamed in, and the aside name alone is deleted. When that last step isn't possible, everything is put back
    as it was and RebuildError says why."""
    try:
        os.replace(src, dst)
        return
    except PermissionError:
        if not _read_only(dst):
            raise  # (in use, or no right to the folder: not this case)
    aside = f"{dst}.old-{os.getpid()}"
    os.rename(dst, aside)
    try:
        os.replace(src, dst)
    except BaseException:
        os.rename(aside, dst)
        raise
    if os.lstat(aside).st_nlink > 1:
        gone = _delete_name_only(aside)
    else:
        try:  # (its only name: nothing else shares the read-only setting)
            os.chmod(aside, stat.S_IWRITE)
            os.remove(aside)
            gone = True
        except OSError:
            gone = False
    if not gone:
        os.rename(dst, src)  # back as it was: src again, and the old file under its own name
        os.rename(aside, dst)
        raise RebuildError(f"{dst} is read-only and is also the file in another place (a hard link, for example to "
                           "your own dump), and this Windows can't replace just this name without clearing "
                           "read-only on both. Make that file writable (or set up the game files from a writable "
                           "copy), then run this again.")


def write_atomic(path: str, data: bytes) -> None:
    """Write a new file next to path, then rename it over path. The old file is never opened for writing (and may
    be read-only: replace_name)."""
    tmp = _tmp_name(path)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        replace_name(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def copy_atomic(src: str, dst: str) -> None:
    """Copy src to dst through a temporary file and a rename (dst is replaced as a whole, never written in place)."""
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    tmp = _tmp_name(dst)
    try:
        shutil.copyfile(src, tmp)
        replace_name(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def move_into_place(src: str, dst: str) -> None:
    """Rename src over dst (atomic on one volume); across volumes copy to a temp file next to dst first."""
    try:
        replace_name(src, dst)
    except OSError as e:
        if getattr(e, "winerror", None) != 17 and e.errno != errno.EXDEV:  # ERROR_NOT_SAME_DEVICE
            raise
        copy_atomic(src, dst)
        os.remove(src)


def _canon(path: str) -> str:
    """One spelling per file for comparisons: absolute, \\\\?\\ prefix dropped, short (8.3) names, junctions and
    subst drives resolved by realpath (for the part of the path that exists), case folded."""
    p = os.path.abspath(path)
    if p.startswith("\\\\?\\UNC\\"):
        p = "\\\\" + p[8:]
    elif p.startswith("\\\\?\\"):
        p = p[4:]
    return os.path.normcase(os.path.realpath(p))


def same_file(a: str, b: str) -> bool:
    """Whether two paths name the same file or folder, however they are spelled."""
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    return _canon(a) == _canon(b)


def is_inside(path: str, folder: str) -> bool:
    """Whether path is folder itself or somewhere under it (spellings resolved as in same_file)."""
    p, f = _canon(path), _canon(folder)
    return p == f or p.startswith(f.rstrip("\\/") + os.sep)


WORK_MARK = ".sw_rebuild-work"
CACHE_MARK = ".sw_rebuild-cache"


def claim_dir(path: str, mark: str) -> None:
    """Make sure path is a folder this tool made, so cleaning it can't delete anything else: it holds the marker
    file `mark`, or is new or empty (then the marker is written). A folder with other files is refused (except
    under out\\rebuild, which only this tool uses)."""
    m = os.path.join(path, mark)
    if os.path.isfile(m):
        return
    if os.path.lexists(path):
        if not os.path.isdir(path) or os.path.islink(path) or os.path.isjunction(path):
            raise RebuildError(f"{path} exists and isn't a folder sw_rebuild made; choose another --work-root")
        if os.listdir(path) and not is_inside(path, OUT_ROOT):
            raise RebuildError(f"{path} already holds files that sw_rebuild didn't make; choose another --work-root")
    os.makedirs(path, exist_ok=True)
    with open(m, "w", encoding="utf-8") as f:
        f.write("This folder belongs to tools/sw_rebuild.py, which deletes what is in it.\n")


def clear_dir(path: str, keep: str) -> None:
    """Delete everything in path except the file named keep (links and junctions are removed, not followed)."""
    for n in os.listdir(path):
        if n == keep:
            continue
        p = os.path.join(path, n)
        try:
            if os.path.isdir(p) and not os.path.islink(p) and not os.path.isjunction(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.isdir(p):
                os.rmdir(p)  # (a link or junction: just the link)
            else:
                os.remove(p)
        except OSError:
            pass


def fmt_time(secs: float) -> str:
    secs = int(round(secs))
    return f"{secs // 60}m {secs % 60:02d}s" if secs >= 60 else f"{secs}s"


def fmt_mb(n: int) -> str:
    return f"{n / 2**20:.1f} MB"


# --- NSO headers and the module layout ----------------------------------------------------------------------------

def nso_header(path: str) -> dict:
    """The header fields the layout needs (no decompression)."""
    with open(path, "rb") as f:
        h = f.read(0x100)
    if len(h) < 0x100 or h[:4] != b"NSO0":
        raise RebuildError(f"{path} is not an NSO file")
    seg = {}
    for name, off in (("text", 0x10), ("rodata", 0x20), ("data", 0x30)):
        _file_off, mem_off, size = struct.unpack_from("<III", h, off)
        seg[name] = (mem_off, size)
    return {**seg, "bss": struct.unpack_from("<I", h, 0x3C)[0], "build_id": h[0x40:0x60].hex()}


def image_size(h: dict) -> int:
    end = max(h["text"][0] + h["text"][1], h["rodata"][0] + h["rodata"][1], h["data"][0] + h["data"][1] + h["bss"])
    return (end + PAGE - 1) & ~(PAGE - 1)


def predict_layout(exefs: str) -> list[tuple[str, int, int, str]]:
    """[(name, base, size, build id)] as `swgen layout` will compute it (Savage Wind tools/layout.py)."""
    main = os.path.join(exefs, "main")
    known = GAME_SUBSDKS.get(nso_header(main)["build_id"][:16]) if os.path.isfile(main) else None
    out, base = [], MODULES_LO
    for name in LOAD_ORDER:
        path = os.path.join(exefs, name)
        if not os.path.isfile(path):
            continue
        if known is not None and name.startswith("subsdk") and name not in known:
            continue  # (a mod's subsdk: Savage Wind leaves it out too)
        h = nso_header(path)
        out.append((name, base, image_size(h), h["build_id"]))
        base += out[-1][2]
    return out


def read_layout(path: str) -> list[tuple[str, int, int, str]]:
    """The modules of a layout.txt that `swgen layout` wrote: name, guest base, image size, build id."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) == 4 and not line.startswith("#"):
                out.append((parts[0], int(parts[1], 16), int(parts[2], 16), parts[3]))
    return out


# --- what the release builds with ---------------------------------------------------------------------------------

def exe_flags(exe_data: bytes) -> dict:
    """The game-code build flags compiled into savagewind.exe: {"normal", "compat", "zig"} (None where not found)."""
    found: dict = {"normal": [], "compat": [], "zig": []}
    for m in re.finditer(rb"-O[0-9sz] [ -~]{8,600}?\x00", exe_data):
        s = m.group()[:-1].decode("ascii")
        if "-fvisibility=" not in s:
            continue
        for target, arch in ARCH_OF.items():
            if s.endswith(arch) or f"{arch} " in s:
                found[target].append(s)
    for m in re.finditer(rb"-Xclang [ -~]{2,400}?\x00", exe_data):
        found["zig"].append(m.group()[:-1].decode("ascii"))
    return {k: (v[0] if len(set(v)) == 1 else None) for k, v in found.items()}


def cpu_runs_normal() -> bool:
    """Whether this CPU runs the normal (x86-64-v3) target. Only used for an id this tool doesn't know."""
    env = os.environ.get("SAVAGEWIND_COMPAT_CPU", "")
    if env and env != "0":
        return False
    if sys.platform == "win32":
        import ctypes
        return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(40))  # PF_AVX2_INSTRUCTIONS_AVAILABLE
    return True


def pick_target(gamelib_id: str, override: str | None = None, cpu_normal=cpu_runs_normal) -> str:
    """normal or compat: --target, else the release's id table, else what this CPU runs (as the runtime decides)."""
    if override:
        return override
    if gamelib_id in RELEASE_IDS:
        return RELEASE_IDS[gamelib_id]
    return "normal" if cpu_normal() else "compat"


def compile_flags(cflags: str, zigflags: str, zig: bool) -> list[str]:
    """The compiler flags as gamebuild.c add_cflags builds them: zig cc gets -march=<cpu> as -mcpu=<cpu with _>,
    then the zig-only flags after them (the last -O that reaches clang wins)."""
    out = []
    for f in cflags.split():
        if zig and f.startswith("-march="):
            out.append("-mcpu=" + f[7:].replace("-", "_"))
        else:
            out.append(f)
    if zig:
        out += zigflags.split()
    return out


@dataclass
class Tools:
    """The install's builders. gen / cc are argv prefixes (normally one .exe each)."""
    install: str
    gen: list[str]
    cc: list[str]
    zig: bool
    inc: str
    entry: str
    hle: str
    implib: str
    zig_rt: str | None
    exe: str

    @property
    def gen_no_cfg(self) -> bool:  # windows.c: started without Control Flow Guard when the program is swgen.exe
        return re.split(r"[\\/]", self.gen[0])[-1].lower() == "swgen.exe"


def find_tools(install: str) -> Tools:
    """The tools gamebuild.c find_tools picks in a release install (SAVAGEWIND_GEN / SAVAGEWIND_CC honoured)."""
    p = lambda *a: os.path.join(install, *a)  # noqa: E731
    gen = os.environ.get("SAVAGEWIND_GEN") or p("swgen", "swgen.exe")
    cc_env = os.environ.get("SAVAGEWIND_CC")
    cc = cc_env or p("zig", "zig.exe")
    zig = not cc_env
    t = Tools(install=install, gen=[gen], cc=[cc], zig=zig, inc=p("gamelib-src"),
              entry=p("gamelib-src", "gamelib_entry.c"), hle=p("gamelib-src", "hle_symbols.txt"),
              implib=p("savagewind.dll.a"), zig_rt=None, exe=p("savagewind.exe"))
    if zig and os.path.isfile(p("zig", "rt", "link.rsp")):
        t.zig_rt = p("zig", "rt")
    need = {"the generator": gen, "the compiler": cc, "gamelib-src\\cpu.h": p("gamelib-src", "cpu.h"),
            "gamelib-src\\gamelib_entry.c": t.entry, "gamelib-src\\hle_symbols.txt": t.hle,
            "savagewind.dll.a": t.implib}
    missing = [f"{what} ({path})" for what, path in need.items() if not os.path.isfile(path)]
    if missing:
        raise RebuildError(f"{install} is not a complete Savage Wind folder; missing: " + ", ".join(missing))
    return t


# --- command lines ------------------------------------------------------------------------------------------------

def quote_arg(a: str) -> str:
    """One argument quoted the way CommandLineToArgvW and the C runtime parse it back (windows.c append_arg)."""
    q = not a or any(c in a for c in ' \t"')
    out = ['"'] if q else []
    i, n = 0, len(a)
    while True:
        bs = 0
        while i < n and a[i] == "\\":
            bs += 1
            i += 1
        if i == n:
            out.append("\\" * (2 * bs if q else bs))
            break
        out.append("\\" * (2 * bs + 1 if a[i] == '"' else bs))
        out.append(a[i])
        i += 1
    if q:
        out.append('"')
    return "".join(out)


def command_line(argv: list[str]) -> str:
    return " ".join(quote_arg(a) for a in argv)


def gen_layout_argv(t: Tools, exefs: str, work: str) -> list[str]:
    return t.gen + ["layout", exefs, work]


def gen_module_argv(t: Tools, name: str, exefs: str, layout_txt: str, outdir: str) -> list[str]:
    argv = t.gen + [name, os.path.join(exefs, name), layout_txt, outdir]
    if name != "main":
        argv += ["--hle", t.hle]
    return argv


def compile_argv(t: Tools, flags: list[str], gamelib_id: str, src: str, obj: str) -> list[str]:
    return t.cc + (["cc"] if t.zig else []) + flags + [f"-I{t.inc}", f'-DGAMELIB_ID="{gamelib_id}"',
                                                        "-c", src, "-o", obj]


def link_argv(t: Tools, out_tmp: str, objs_rsp: str) -> list[str]:
    if t.zig_rt:  # the release's zig: lld-link with its runtime built ahead of time (flags and libraries: link.rsp)
        return t.cc + ["lld-link", "@" + os.path.join(t.zig_rt, "link.rsp"), f"-LIBPATH:{t.zig_rt}",
                       f"-OUT:{out_tmp}", "@" + objs_rsp, t.implib]
    return t.cc + (["cc"] if t.zig else []) + ["-shared", "-o", out_tmp, "@" + objs_rsp, t.implib]


def objs_rsp_text(objs: list[str]) -> str:
    """The object list for the linker: quoted, forward slashes (backslashes are escapes in a response file)."""
    return "".join(f'"{o.replace(chr(92), "/")}"\n' for o in objs)


def parse_gen_progress(text: str) -> tuple[int, int, int] | None:
    """(functions written, all functions, finished chunk files) from the last progress line of a generator log."""
    last = None
    for m in PROGRESS_RE.finditer(text):
        last = m
    return (int(last.group(1)), int(last.group(2)), int(last.group(3))) if last else None


def chunk_indexes(outdir: str, name: str, after: int = 0) -> list[int]:
    """1 + the index of every <name>_NNN.c in outdir from `after` on (chunks the log didn't report, if any)."""
    try:
        names = os.listdir(outdir)
    except OSError:
        return []
    out = []
    for n in names:
        m = re.fullmatch(re.escape(name) + r"_(\d{3})\.c", n)
        if m and int(m.group(1)) >= after:
            out.append(int(m.group(1)) + 1)
    return out


def log_tail(path: str, lines: int = 40) -> list[str]:
    """The end of a log without the generator's progress lines (what gamebuild.c copies into savagewind.log)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 16384))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    out = [ln.rstrip("\r") for ln in text.split("\n") if ln.strip() and ": progress " not in ln]
    return out[-lines:]


# --- child processes ----------------------------------------------------------------------------------------------

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes as wt

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _SA(ctypes.Structure):
        _fields_ = [("nLength", wt.DWORD), ("lpSecurityDescriptor", wt.LPVOID), ("bInheritHandle", wt.BOOL)]

    class _SI(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
                    ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
                    ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
                    ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
                    ("lpReserved2", wt.LPVOID), ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE),
                    ("hStdError", wt.HANDLE)]

    class _SIEX(ctypes.Structure):
        _fields_ = [("StartupInfo", _SI), ("lpAttributeList", wt.LPVOID)]

    class _PI(ctypes.Structure):
        _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD),
                    ("dwThreadId", wt.DWORD)]

    class _BasicLimit(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]

    class _ExtLimit(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BasicLimit), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    def _fn(name, res, *args):
        f = getattr(_k32, name)
        f.restype, f.argtypes = res, list(args)
        return f

    _CreateFileW = _fn("CreateFileW", wt.HANDLE, wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.POINTER(_SA), wt.DWORD,
                       wt.DWORD, wt.HANDLE)
    _CreateProcessW = _fn("CreateProcessW", wt.BOOL, wt.LPCWSTR, wt.LPWSTR, wt.LPVOID, wt.LPVOID, wt.BOOL, wt.DWORD,
                          wt.LPVOID, wt.LPCWSTR, wt.LPVOID, ctypes.POINTER(_PI))
    _InitAttrs = _fn("InitializeProcThreadAttributeList", wt.BOOL, wt.LPVOID, wt.DWORD, wt.DWORD,
                     ctypes.POINTER(ctypes.c_size_t))
    _UpdateAttr = _fn("UpdateProcThreadAttribute", wt.BOOL, wt.LPVOID, wt.DWORD, ctypes.c_size_t, wt.LPVOID,
                      ctypes.c_size_t, wt.LPVOID, ctypes.POINTER(ctypes.c_size_t))
    _DeleteAttrs = _fn("DeleteProcThreadAttributeList", None, wt.LPVOID)
    _CreateJobObjectW = _fn("CreateJobObjectW", wt.HANDLE, wt.LPVOID, wt.LPCWSTR)
    _SetInformationJobObject = _fn("SetInformationJobObject", wt.BOOL, wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD)
    _AssignProcessToJobObject = _fn("AssignProcessToJobObject", wt.BOOL, wt.HANDLE, wt.HANDLE)
    _TerminateJobObject = _fn("TerminateJobObject", wt.BOOL, wt.HANDLE, wt.UINT)
    _ResumeThread = _fn("ResumeThread", wt.DWORD, wt.HANDLE)
    _WaitForSingleObject = _fn("WaitForSingleObject", wt.DWORD, wt.HANDLE, wt.DWORD)
    _GetExitCodeProcess = _fn("GetExitCodeProcess", wt.BOOL, wt.HANDLE, ctypes.POINTER(wt.DWORD))
    _TerminateProcess = _fn("TerminateProcess", wt.BOOL, wt.HANDLE, wt.UINT)
    _CloseHandle = _fn("CloseHandle", wt.BOOL, wt.HANDLE)

    _INVALID = ctypes.c_void_p(-1).value
    PROC_THREAD_ATTRIBUTE_MITIGATION_POLICY = 0x00020007
    CFG_ALWAYS_OFF = 0x2 << 40  # PROCESS_CREATION_MITIGATION_POLICY_CONTROL_FLOW_GUARD_ALWAYS_OFF
    _job = None

    def _the_job():
        """One job for every child, ended with this process (a compiler's own children too: no leftovers)."""
        global _job
        if _job is None:
            _job = _CreateJobObjectW(None, None) or 0
            if _job:
                li = _ExtLimit()
                li.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                _SetInformationJobObject(_job, 9, ctypes.addressof(li), ctypes.sizeof(li))
        return _job

    def _env_block(env: dict) -> ctypes.Array:
        """The UTF-16 environment block for CreateProcessW, sized in UTF-16 units (a character outside the BMP is
        two), ending in two NULs."""
        text = "".join(f"{k}={v}\0" for k, v in sorted(env.items(), key=lambda kv: kv[0].upper())) + "\0"
        data = text.encode("utf-16-le", "surrogatepass")
        buf = ctypes.create_unicode_buffer(len(data) // 2 + 1)
        ctypes.memmove(buf, data, len(data))
        return buf

    def _win_spawn(argv, log_path, env, no_cfg, low_priority):
        """CreateProcessW like windows.c plat_spawn: output to log_path, no window, started suspended and put in the
        job, and for swgen.exe the "CFG: off" mitigation policy. Returns (process handle, pid, cfg_off)."""
        sa = _SA(ctypes.sizeof(_SA), None, True)
        log = _CreateFileW(log_path, 0x40000000, 0x3, ctypes.byref(sa), 2, 0x80, None)  # GENERIC_WRITE, CREATE_ALWAYS
        if log in (None, 0, _INVALID):
            raise OSError(ctypes.get_last_error(), f"can't write {log_path}")
        try:
            si = _SI()
            si.cb = ctypes.sizeof(_SI)
            si.dwFlags = 0x100  # STARTF_USESTDHANDLES
            si.hStdInput, si.hStdOutput, si.hStdError = None, log, log
            prog = argv[0].replace("/", "\\")
            app = os.path.abspath(prog) if os.path.isfile(prog) else None
            cmd = ctypes.create_unicode_buffer(command_line(argv))
            envb = _env_block(env) if env is not None else None
            flags = 0x08000000 | 0x4 | (0x400 if envb is not None else 0)  # NO_WINDOW, SUSPENDED, UNICODE_ENVIRONMENT
            if low_priority:
                flags |= 0x4000  # BELOW_NORMAL_PRIORITY_CLASS
            pi = _PI()
            ok, cfg_off = False, False
            if no_cfg:
                size = ctypes.c_size_t(0)
                _InitAttrs(None, 1, 0, ctypes.byref(size))
                attrs = (ctypes.c_byte * max(size.value, 64))()
                if _InitAttrs(attrs, 1, 0, ctypes.byref(size)):
                    policy = ctypes.c_uint64(CFG_ALWAYS_OFF)
                    six = _SIEX()
                    six.StartupInfo = si
                    six.StartupInfo.cb = ctypes.sizeof(_SIEX)
                    six.lpAttributeList = ctypes.addressof(attrs)
                    if _UpdateAttr(attrs, 0, PROC_THREAD_ATTRIBUTE_MITIGATION_POLICY, ctypes.addressof(policy), 8,
                                   None, None):
                        ok = bool(_CreateProcessW(app, cmd, None, None, True, flags | 0x00080000, envb, None,
                                                  ctypes.addressof(six), ctypes.byref(pi)))
                        cfg_off = ok
                    _DeleteAttrs(attrs)
            if not ok:  # (no attribute support: started as before, like windows.c)
                ok = bool(_CreateProcessW(app, cmd, None, None, True, flags, envb, None, ctypes.addressof(si),
                                          ctypes.byref(pi)))
            if not ok:
                raise OSError(ctypes.get_last_error(), f"could not start {argv[0]}")
        finally:
            _CloseHandle(log)
        job = _the_job()
        if job:
            _AssignProcessToJobObject(job, pi.hProcess)
        _ResumeThread(pi.hThread)
        _CloseHandle(pi.hThread)
        return pi.hProcess, pi.dwProcessId, cfg_off


class Proc:
    """A child process writing stdout and stderr to a log file."""

    def __init__(self, argv: list[str], log_path: str, *, env: dict | None = None, no_cfg: bool = False,
                 low_priority: bool = False):
        self.argv, self.log = argv, log_path
        self.code: int | None = None
        self.cfg_off = False
        if sys.platform == "win32":
            self._h, self.pid, self.cfg_off = _win_spawn(argv, log_path, env, no_cfg, low_priority)
            self._p = None
        else:
            with open(log_path, "wb") as f:
                self._p = subprocess.Popen(argv, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                           env=env)
            self.pid = self._p.pid

    def poll(self) -> int | None:
        if self.code is not None:
            return self.code
        if self._p is not None:
            self.code = self._p.poll()
            return self.code
        if _WaitForSingleObject(self._h, 0) == 0x102:  # WAIT_TIMEOUT
            return None
        c = wt.DWORD(255)
        _GetExitCodeProcess(self._h, ctypes.byref(c))
        _CloseHandle(self._h)
        self.code = c.value
        return self.code

    def wait(self) -> int:
        while self.poll() is None:
            time.sleep(0.05)
        return self.code

    def kill(self) -> None:
        if self.poll() is not None:
            return
        if self._p is not None:
            self._p.kill()
            self._p.wait()
        else:
            _TerminateProcess(self._h, 255)
            _WaitForSingleObject(self._h, 0xFFFFFFFF)
        self.poll()


def kill_everything(procs: list[Proc]) -> None:
    for p in procs:
        p.kill()
    if sys.platform == "win32" and _job:
        _TerminateJobObject(_job, 255)  # (zig's own clang children too)


def game_running() -> bool:
    """True when a savagewind.exe process is running (tasklist). The output is compared as bytes, never decoded:
    tasklist writes in the console (OEM) code page, and on a localized Windows its "no tasks" line has letters (the
    German u-umlaut is byte 0x81 in cp850) that the ANSI code page can't decode, which made this raise instead of
    answering. The process name is ASCII in every code page."""
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq savagewind.exe", "/NH", "/FO", "CSV"],
                             capture_output=True, timeout=30).stdout
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return b'"savagewind.exe"' in (out or b"").lower()


# --- the object cache ---------------------------------------------------------------------------------------------

def tool_identity(paths: list[str]) -> str:
    """A hash of the builder programs (and, for a PyInstaller swgen, the names and sizes in its _internal folder)."""
    h = hashlib.sha256()
    for p in paths:
        if os.path.isfile(p):
            h.update(os.path.basename(p).lower().encode() + file_sha256(p).encode())
            internal = os.path.join(os.path.dirname(p), "_internal")
            if os.path.isdir(internal):
                for root, _dirs, files in sorted(os.walk(internal)):
                    for n in sorted(files):
                        fp = os.path.join(root, n)
                        h.update(f"{os.path.relpath(fp, internal)}:{os.path.getsize(fp)};".encode())
        else:
            h.update(p.encode())
    return h.hexdigest()


def headers_identity(inc: str) -> str:
    h = hashlib.sha256()
    for n in sorted(os.listdir(inc)):
        if n.endswith(".h"):
            h.update(n.encode() + file_sha256(os.path.join(inc, n)).encode())
    return h.hexdigest()


def module_cache_key(name: str, base: int, size: int, build_id: str, nso_sha256: str, gamelib_id: str,
                     flags: list[str], *, gen_id: str, cc_id: str, headers_id: str, hle_sha256: str | None,
                     hooks=None) -> str:
    """What a module's objects are made from: the module (build id, every byte, where it loads), the generator and
    compiler, the flags, GAMELIB_ID and the headers. hle_symbols.txt only matters for modules other than main.
    hooks: the hooked addresses (--hooks; none = the key of a build without hooks, unchanged)."""
    parts = {"v": CACHE_VERSION, "module": name, "base": base, "size": size, "build_id": build_id,
             "nso_sha256": nso_sha256, "gamelib_id": gamelib_id, "flags": flags, "gen": gen_id, "cc": cc_id,
             "headers": headers_id, "hle": hle_sha256 if name != "main" else None}
    if hooks:
        parts["hooks"] = {"format": _gen_hooks().FORMAT, "addrs": [f"{a:#x}" for a in sorted(set(hooks))]}
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def _gen_hooks():
    """tools/gen_hooks.py (imported only when hooks are used)."""
    try:
        import gen_hooks
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import gen_hooks
    return gen_hooks


class ObjectCache:
    """<root>/<module>/<key[:32]>/: a module's objects in link order plus manifest.json."""

    def __init__(self, root: str, keep: int = 2):
        self.root, self.keep = root, max(1, keep)

    def entry_dir(self, module: str, key: str) -> str:
        return os.path.join(self.root, module, key[:32])

    def lookup(self, module: str, key: str) -> list[str] | None:
        d = self.entry_dir(module, key)
        try:
            with open(os.path.join(d, "manifest.json"), encoding="utf-8") as f:
                man = json.load(f)
        except (OSError, ValueError):
            return None
        if man.get("key") != key or not man.get("objects"):
            return None
        objs = []
        for n, size in zip(man["objects"], man.get("sizes", [])):
            p = os.path.join(d, n)
            if not os.path.isfile(p) or os.path.getsize(p) != size:
                return None
            objs.append(p)
        if len(objs) != len(man["objects"]):
            return None
        try:
            os.utime(os.path.join(d, "manifest.json"))  # (most recently used: kept longest)
        except OSError:
            pass
        return objs

    def staging_dir(self, module: str, key: str) -> str:
        d = os.path.join(self.root, module, f".staging-{key[:16]}-{os.getpid()}")
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        return d

    def commit(self, module: str, key: str, staging: str, objs: list[str], info: dict,
               tries: int = 8) -> list[str] | None:
        """Turns a staging folder into the entry for key. Returns the objects' new paths, or None when the folder
        couldn't be renamed (a virus scanner holding a file, say): then nothing is cached and objs stay usable."""
        names = [os.path.basename(o) for o in objs]
        man = {"key": key, "module": module, "objects": names, "sizes": [os.path.getsize(o) for o in objs],
               "created": time.strftime("%Y-%m-%d %H:%M:%S"), **info}
        final = self.entry_dir(module, key)
        try:
            with open(os.path.join(staging, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(man, f, indent=1)
            shutil.rmtree(final, ignore_errors=True)
        except OSError:
            return None
        for i in range(tries):
            try:
                os.replace(staging, final)
                return [os.path.join(final, n) for n in names]
            except OSError:
                time.sleep(min(2.0, 0.1 * 2 ** i))
        return None

    def prune(self, module: str, protect: set[str]) -> list[str]:
        """Drops the least recently used entries of a module past `keep` (never one in protect). Returns removed.
        Only folders whose manifest.json is this cache's own entry for that name count."""
        d = os.path.join(self.root, module)
        if not os.path.isdir(d):
            return []
        entries = []
        for n in os.listdir(d):
            man = os.path.join(d, n, "manifest.json")
            try:
                with open(man, encoding="utf-8") as f:
                    info = json.load(f)
                mine = isinstance(info, dict) and info.get("module") == module and \
                    str(info.get("key", ""))[:32] == n
                if mine:
                    entries.append((os.path.getmtime(man), n))
            except (OSError, ValueError):
                pass
        entries.sort(reverse=True)
        removed = []
        for _mtime, n in entries[self.keep:]:
            if n not in protect:
                shutil.rmtree(os.path.join(d, n), ignore_errors=True)
                removed.append(n)
        return removed


# --- vanilla backups and swaps ------------------------------------------------------------------------------------

def paths_for(install: str) -> dict:
    ex = os.path.join(install, "extracted")
    return {
        "extracted": ex,
        "exefs": os.path.join(ex, "exefs"),
        "main": os.path.join(ex, "exefs", "main"),
        "lib": os.path.join(ex, LIB_NAME),
        "lib_id": os.path.join(ex, LIB_NAME + ".id"),
        "lib_vanilla": os.path.join(ex, LIB_NAME + ".vanilla"),
        "lib_vanilla_id": os.path.join(ex, LIB_NAME + ".vanilla.id"),
        "vanilla_dir": os.path.join(install, "mods", ".vanilla"),
        "main_backup": os.path.join(install, "mods", ".vanilla", "exefs", "main"),
        "state": os.path.join(install, "mods", ".vanilla", "state.json"),
    }


def load_state(install: str) -> dict:
    try:
        with open(paths_for(install)["state"], encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(install: str, state: dict) -> None:
    path = paths_for(install)["state"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_atomic(path, json.dumps(state, indent=1, sort_keys=True).encode())


def _refuse_if_running(running=None) -> None:
    """running: a check to use instead of game_running (looked up when called, so a test can replace it)."""
    if (running or game_running)():
        raise RebuildError("Savage Wind is running. Close it first.")


def install_patched_main(patched: str, install: str, *, running=None) -> list[str]:
    """Replace extracted\\exefs\\main with a patched copy. The old file may be a hard link to the user's own dump, so it
    is never opened for writing: a new file is written and renamed over it. The vanilla main is copied to
    mods\\.vanilla\\exefs\\main first (once)."""
    _refuse_if_running(running)
    P = paths_for(install)
    main = P["main"]
    if not os.path.isfile(main):
        raise RebuildError(f"no main in {P['exefs']}: set up the game files first")
    if not os.path.isfile(patched):
        raise RebuildError(f"{patched} not found")
    if os.path.samefile(patched, main):
        raise RebuildError("the patched main is the installed main itself")
    cur_id, new_id = nso_header(main)["build_id"], nso_header(patched)["build_id"]
    if cur_id != new_id:
        raise RebuildError(f"{patched} has build id {new_id[:16]}, the installed main has {cur_id[:16]}; "
                           "Savage Wind would refuse it")
    with open(patched, "rb") as f:
        data = f.read()
    new_sha, cur_sha = hashlib.sha256(data).hexdigest(), file_sha256(main)
    if new_sha == cur_sha:
        return ["main: this patched main is already installed"]
    state = load_state(install)
    msgs = []
    if not os.path.isfile(P["main_backup"]):
        if state.get("main_installed_sha256") == cur_sha:
            raise RebuildError("the installed main is a patched one and the vanilla backup "
                               f"({P['main_backup']}) is missing; set up the game files again first")
        copy_atomic(main, P["main_backup"])
        state["main_vanilla_sha256"] = cur_sha
        msgs.append(f"main: vanilla copy saved to {P['main_backup']}")
    elif nso_header(P["main_backup"])["build_id"] != cur_id:
        # The game files were set up again from another game version: the old copy can never be restored.
        if state.get("main_installed_sha256") == cur_sha:
            raise RebuildError(f"the backup {P['main_backup']} is for another game version")
        copy_atomic(main, P["main_backup"])
        state["main_vanilla_sha256"] = cur_sha
        msgs.append("main: the old vanilla copy was for another game version; saved the current main instead")
    write_atomic(main, data)  # (new file + rename: a hard-linked old main keeps its data)
    state["main_installed_sha256"] = new_sha
    save_state(install, state)
    msgs.insert(0, f"main: patched copy installed ({fmt_mb(len(data))})")
    msgs.append(f'Code changes need a rebuild: python tools/sw_rebuild.py build "{install}" --install')
    return msgs


def exe_knows_id(install: str, gamelib_id: str) -> bool | None:
    """Whether the install's savagewind.exe contains this GAMELIB_ID (None when there is no exe to look in)."""
    exe = os.path.join(install, "savagewind.exe")
    if not os.path.isfile(exe):
        return None
    with open(exe, "rb") as f:
        return (gamelib_id.encode() + b"\0") in f.read()


def _main_build_id(path: str) -> str | None:
    try:
        return nso_header(path)["build_id"] if os.path.isfile(path) else None
    except RebuildError:
        return None


def library_backup_problem(install: str, state: dict | None = None) -> str | None:
    """Why swgame.dll.vanilla can't go back in (None when it can): its id isn't one savagewind.exe uses, it differs
    from the id of a library Savage Wind set up since (not one this tool installed), or it was saved for another
    game version. "Remove game files" and a new setup leave .vanilla files behind, so a backup can be stale."""
    P = paths_for(install)
    state = load_state(install) if state is None else state
    bid = _read_id(P["lib_vanilla_id"])
    if bid and exe_knows_id(install, bid) is False:
        return "for another Savage Wind version"
    if bid and os.path.isfile(P["lib"]) and state.get("library_installed_sha256") != file_sha256(P["lib"]):
        cid = _read_id(P["lib_id"])
        if cid and cid != bid:
            return "for another Savage Wind version"
    was = state.get("library_vanilla_main_build_id")
    now = _main_build_id(P["main"])
    if was and now and was != now:
        return "for another game version"
    return None


def _save_library_backup(install: str, state: dict) -> None:
    P = paths_for(install)
    copy_atomic(P["lib"], P["lib_vanilla"])
    if os.path.isfile(P["lib_id"]):
        copy_atomic(P["lib_id"], P["lib_vanilla_id"])
    elif os.path.isfile(P["lib_vanilla_id"]):
        os.remove(P["lib_vanilla_id"])
    state["library_vanilla_main_build_id"] = _main_build_id(P["main"])
    save_state(install, state)


def backup_vanilla_library(install: str) -> str | None:
    """Copy extracted\\swgame.dll (+ .id) to swgame.dll.vanilla (+ .vanilla.id) unless a good backup exists. A stale
    backup (library_backup_problem) is replaced by the current library when that is one Savage Wind set up from
    the vanilla main; otherwise this refuses rather than overwrite the only vanilla library."""
    P = paths_for(install)
    state = load_state(install)
    have = os.path.isfile(P["lib"])
    ours = have and state.get("library_installed_sha256") == file_sha256(P["lib"])
    if os.path.isfile(P["lib_vanilla"]):
        why = library_backup_problem(install, state)
        if not why:
            return None
        if not have or ours:
            raise RebuildError(f"the vanilla backup {P['lib_vanilla']} is {why}, and extracted\\{LIB_NAME} isn't a "
                               "vanilla one to replace it with; set up the game files in Savage Wind first")
        if os.path.isfile(P["main"]) and state.get("main_installed_sha256") == file_sha256(P["main"]):
            raise RebuildError(f"the vanilla backup {P['lib_vanilla']} is {why}, and the current library may have "
                               "been built from the patched main. Run restore --only main, set up the game files in "
                               "Savage Wind, then build again")
        _save_library_backup(install, state)
        return f"library: the old vanilla copy was {why}; saved the current library instead"
    if not have:
        return "library: no swgame.dll to back up"
    if ours:
        raise RebuildError(f"{P['lib']} is a rebuilt library and the vanilla backup is missing; "
                           "set up the game files again to get a vanilla one")
    _save_library_backup(install, state)
    return f"library: vanilla copy saved as {P['lib_vanilla']}"


def install_library(new_dll: str, install: str, gamelib_id: str, *, running=None) -> list[str]:
    """Put a built library in extracted\\ (after backing up the vanilla one): rename into place, then the .id."""
    _refuse_if_running(running)
    P = paths_for(install)
    msgs = [m for m in [backup_vanilla_library(install)] if m]
    sha = file_sha256(new_dll)
    move_into_place(new_dll, P["lib"])
    write_atomic(P["lib_id"], (gamelib_id + "\n").encode())
    state = load_state(install)
    state["library_installed_sha256"] = sha
    save_state(install, state)
    return msgs


def restore(install: str, only: str | None = None, *, running=None) -> list[str]:
    """Put back the vanilla main (copied from mods\\.vanilla) and/or the vanilla library (swgame.dll.vanilla).
    A backup for another game or Savage Wind version is left alone, and nothing is put back into game files that
    Savage Wind has removed."""
    _refuse_if_running(running)
    P = paths_for(install)
    state = load_state(install)
    msgs = []
    if only in (None, "main"):
        if not os.path.isfile(P["main_backup"]):
            msgs.append("main: no backup, left as it is")
        elif not os.path.isdir(P["exefs"]):
            msgs.append("main: the game files aren't set up, left as it is")
        elif os.path.isfile(P["main"]) and _main_build_id(P["main"]) != _main_build_id(P["main_backup"]):
            msgs.append("main: the backup is for another game version, left as it is")
        elif os.path.isfile(P["main"]) and file_sha256(P["main"]) == file_sha256(P["main_backup"]):
            msgs.append("main: already vanilla")
            state.pop("main_installed_sha256", None)
        else:
            copy_atomic(P["main_backup"], P["main"])
            state.pop("main_installed_sha256", None)
            msgs.append("main: vanilla restored")
    if only in (None, "library"):
        why = library_backup_problem(install, state) if os.path.isfile(P["lib_vanilla"]) else None
        if not os.path.isfile(P["lib_vanilla"]):
            msgs.append("library: no backup, left as it is")
        elif not os.path.isdir(P["exefs"]):  # ("Remove game files" ran: the .vanilla files stay behind)
            msgs.append("library: the game files aren't set up, left as it is")
        elif why:
            msgs.append(f"library: the backup is {why}, left as it is "
                        "(set up the game files in Savage Wind to get a vanilla library)")
        else:
            replace_name(P["lib_vanilla"], P["lib"])
            if os.path.isfile(P["lib_vanilla_id"]):
                replace_name(P["lib_vanilla_id"], P["lib_id"])
            state.pop("library_installed_sha256", None)
            state.pop("library_vanilla_main_build_id", None)
            msgs.append("library: vanilla restored")
    if only == "main" and state.get("library_installed_sha256"):
        msgs.append("Note: the rebuilt library is still installed; it was built for the patched main.")
    if only == "library" and state.get("main_installed_sha256"):
        msgs.append("Note: the patched main is still installed; its code changes need a rebuilt library.")
    if os.path.isdir(P["vanilla_dir"]):
        save_state(install, state)
    return msgs


# --- the build ----------------------------------------------------------------------------------------------------

@dataclass
class Settings:
    install: str
    exefs: str
    out: str                      # the finished library (for --install: <install>\extracted\swgame.dll)
    gamelib_id: str
    target: str
    flags: list[str]              # compiler flags (compile_flags())
    jobs: int = 4
    work: str = os.path.join(OUT_ROOT, "work")
    cache: str | None = os.path.join(OUT_ROOT, "cache")
    cache_keep: int = 2
    install_mode: bool = False
    keep_work: bool = False
    low_priority: bool = False
    hooks: list[int] = field(default_factory=list)  # main's functions to give hook slots (tools/gen_hooks.py)


@dataclass
class _Unit:
    src: str
    obj: str
    log: str
    module: str | None
    generated: bool
    bytes: int = 0


@dataclass
class _Mod:
    name: str
    base: int
    size: int
    build_id: str
    key: str
    objs: list[str] = field(default_factory=list)
    cached: bool = False
    proc: Proc | None = None
    log: str = ""
    outdir: str = ""
    objdir: str = ""
    exit: int | None = None
    ci: int = 0
    n: int = 0
    files: int = 0
    queued: int = 0
    table: bool = False
    done: int = 0
    nso_sha: str = ""

    @property
    def complete(self) -> bool:
        return self.cached or (self.exit == 0 and self.table and self.done == len(self.objs))


@dataclass
class BuildResult:
    dll: str
    size: int
    seconds: float
    generated: list[str]
    cached: list[str]
    notes: list[str]


class StepFailed(RebuildError):
    def __init__(self, what: str, log: str, code: int | None):
        hint = ""
        if code == CFG_CRASH:
            hint = " (Windows stopped it, 0xC0000409: Control Flow Guard?)"
        elif code is not None and code > 255:
            hint = f" (exit status {code:#x})"
        elif code is not None:
            hint = f" (exit status {code})"
        super().__init__(f"{what} failed{hint}")
        self.log, self.code = log, code


def _say(msg: str) -> None:
    print(msg, flush=True)


def _lock(path: str):
    """An exclusive lock on path while a build runs (released when the process ends, even after a crash)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    f = open(path, "a+b")
    try:
        if sys.platform == "win32":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise RebuildError(f"another rebuild is using {os.path.dirname(path)}")
    return f


def _hook_step(fn, m: _Mod):
    """Run a gen_hooks step on a module's generated C; its errors become this tool's."""
    try:
        return fn()
    except _gen_hooks().HookError as e:
        raise RebuildError(f"--hooks: {e}") from None
    except OSError as e:
        raise StepFailed(f"swgen {m.name} (hooks: {e.strerror or e})", m.log, m.exit) from None


def check_out(s: Settings) -> None:
    """The output can't be the game's own library (unless installing) or lie in a folder the build cleans up."""
    if not s.install_mode and same_file(s.out, paths_for(s.install)["lib"]):
        raise RebuildError("that's the game's own library: use --install (it backs up the vanilla one first)")
    for d in (s.work, s.cache):
        if d and is_inside(s.out, d):
            raise RebuildError(f"the output can't be inside {d}: the build cleans that folder up")


def run_build(s: Settings, t: Tools, *, say=_say, running=None, poll_s: float = 0.05) -> BuildResult:
    """Build the library like gamebuild_run, with cached modules skipped. Raises RebuildError on failure."""
    running = running or game_running
    t0 = time.monotonic()
    work = s.work
    check_out(s)
    lock = _lock(work.rstrip("\\/") + ".lock")
    procs: list[Proc] = []
    staging: list[str] = []
    ok = claimed = False
    try:
        claim_dir(work, WORK_MARK)  # (refuses a folder this tool didn't make: nothing else is ever deleted)
        if s.cache:
            claim_dir(s.cache, CACHE_MARK)
        claimed = True
        clear_dir(work, WORK_MARK)
        env = dict(os.environ)
        if t.zig:  # (its caches in the work folder, deleted afterwards, like gamebuild.c)
            env["ZIG_GLOBAL_CACHE_DIR"] = env["ZIG_LOCAL_CACHE_DIR"] = os.path.join(work, "zig-cache")
        cache = ObjectCache(s.cache, s.cache_keep) if s.cache else None

        def start(argv, log, no_cfg=False):
            p = Proc(argv, log, env=env, no_cfg=no_cfg, low_priority=s.low_priority)
            procs.append(p)
            return p

        # 1. the module layout
        layout_txt, layout_c = os.path.join(work, "layout.txt"), os.path.join(work, "layout.c")
        log = os.path.join(work, "gen_layout.log")
        say("Reading the module layout...")
        rc = start(gen_layout_argv(t, s.exefs, work), log, t.gen_no_cfg).wait()
        layout = read_layout(layout_txt) if rc == 0 and os.path.isfile(layout_txt) else []
        if not layout:
            raise StepFailed("swgen layout", log, rc)
        say("Modules: " + ", ".join(f"{n} {b:#x} ({fmt_mb(z)})" for n, b, z, _ in layout))
        notes = []
        try:
            if [(n, b, z) for n, b, z, _ in predict_layout(s.exefs)] != [(n, b, z) for n, b, z, _ in layout]:
                notes.append("Note: swgen's layout differs from the one this tool predicts (still used as is).")
        except (OSError, RebuildError):
            pass

        # 2. cached modules, and the generator for the rest (all at once, as the setup does)
        gen_id, cc_id = tool_identity(t.gen), tool_identity(t.cc)
        headers_id = headers_identity(t.inc)
        hle_sha = file_sha256(t.hle)
        hooks = sorted(set(s.hooks or []))
        hook_pass = None
        if hooks:
            if not any(n == "main" for n, *_ in layout):
                raise RebuildError("--hooks: the layout has no main module")
            hook_pass = _gen_hooks().HookPass(hooks, "main")
        mods: list[_Mod] = []
        for name, base, size, bid in layout:
            nso_sha = file_sha256(os.path.join(s.exefs, name))
            key = module_cache_key(name, base, size, bid, nso_sha, s.gamelib_id, s.flags, gen_id=gen_id,
                                   cc_id=cc_id, headers_id=headers_id, hle_sha256=hle_sha,
                                   hooks=hooks if name == "main" else None)
            m = _Mod(name, base, size, bid, key, nso_sha=nso_sha)
            hit = cache.lookup(name, key) if cache else None
            if hit:
                m.objs, m.cached = hit, True
                say(f"{name}: unchanged, reusing {len(hit)} cached objects")
            else:
                m.outdir, m.log = os.path.join(work, name), os.path.join(work, f"gen_{name}.log")
                if cache:
                    m.objdir = cache.staging_dir(name, key)
                    staging.append(m.objdir)
                else:
                    m.objdir = os.path.join(work, "obj", name)
                    os.makedirs(m.objdir)
                m.proc = start(gen_module_argv(t, name, s.exefs, layout_txt, m.outdir), m.log, t.gen_no_cfg)
                say(f"{name}: generating C" + (" (not in the cache)" if cache else ""))
            mods.append(m)

        units = [_Unit(t.entry, os.path.join(work, "gamelib_entry.o"), os.path.join(work, "gamelib_entry.log"),
                       None, False),
                 _Unit(layout_c, os.path.join(work, "layout.o"), os.path.join(work, "layout.log"), None, True)]
        for u in units:
            u.bytes = os.path.getsize(u.src)
        by_name = {m.name: m for m in mods}

        def queue(m: _Mod, stem: str):
            u = _Unit(os.path.join(m.outdir, stem + ".c"), os.path.join(m.objdir, stem + ".o"),
                      os.path.join(work, stem + ".log"), m.name, True)
            try:
                u.bytes = os.path.getsize(u.src)
            except OSError:
                raise StepFailed(f"swgen {m.name} (no {stem}.c)", m.log, m.exit)
            units.append(u)
            m.objs.append(u.obj)

        gen_mods = [m for m in mods if not m.cached]
        weight_total = sum(m.size for m in gen_mods) or 1
        nxt, running_cc, done_bytes, shown, last_print = 0, [], 0, -1, 0.0
        while True:
            gens_done = True
            gen_frac = 0.0
            for m in gen_mods:
                if m.exit is None:
                    try:
                        with open(m.log, "rb") as f:
                            f.seek(0, 2)
                            f.seek(max(0, f.tell() - 65536))
                            prog = parse_gen_progress(f.read().decode("utf-8", "replace"))
                    except OSError:
                        prog = None
                    if prog:
                        m.ci, m.n, m.files = prog[0], prog[1], max(m.files, prog[2])
                    rc = m.proc.poll()
                    if rc is not None:
                        if rc != 0:
                            raise StepFailed(f"swgen {m.name}", m.log, rc)
                        with open(m.log, "rb") as f:
                            prog = parse_gen_progress(f.read().decode("utf-8", "replace"))
                        if prog:
                            m.ci, m.n, m.files = prog[0], prog[1], max(m.files, prog[2])
                        m.files = max([m.files] + chunk_indexes(m.outdir, m.name, after=m.files))
                        m.exit = 0
                if m.exit != 0:
                    gens_done = False
                hooked = hook_pass is not None and m.name == "main"
                for _ in range(m.queued, m.files):
                    stem = f"{m.name}_{m.queued:03d}"
                    if hooked:  # (before it is compiled: hooked definitions get renamed)
                        _hook_step(lambda: hook_pass.chunk(os.path.join(m.outdir, stem + ".c")), m)
                    queue(m, stem)
                    m.queued += 1
                if m.exit == 0 and not m.table:
                    queue(m, f"{m.name}_table")
                    m.table = True
                    if hooked:  # (every address checked against the function table, then the wrappers)
                        rep = _hook_step(lambda: hook_pass.finish(m.outdir), m)
                        queue(m, os.path.splitext(os.path.basename(rep.hooks_c))[0])
                        say(f"main: hook slots for {len(hooks)} function(s)")
                gen_frac += m.size / weight_total * (1 if m.exit == 0 else (m.ci / m.n if m.n else 0))

            while len(running_cc) < s.jobs and nxt < len(units):
                u = units[nxt]
                running_cc.append((start(compile_argv(t, s.flags, s.gamelib_id, u.src, u.obj), u.log), u))
                nxt += 1
            for item in list(running_cc):
                p, u = item
                rc = p.poll()
                if rc is None:
                    continue
                running_cc.remove(item)
                procs.remove(p)
                if rc != 0:
                    raise StepFailed(f"compiling {os.path.basename(u.src)}", u.log, rc)
                done_bytes += u.bytes
                if u.generated and not s.keep_work:
                    os.remove(u.src)  # (generated C, done with)
                if u.module:
                    m = by_name[u.module]
                    m.done += 1
                    if m.complete and cache:
                        moved = cache.commit(m.name, m.key, m.objdir, m.objs,
                                             {"build_id": m.build_id, "base": hex(m.base), "nso_sha256": m.nso_sha})
                        if moved:
                            m.objs = moved
                            staging.remove(m.objdir)
                            cache.prune(m.name, {m.key[:32]})
                        else:  # (linked from the staging folder, which goes afterwards)
                            notes.append(f"Note: {m.name} couldn't be stored in the cache (folder in use).")
            for m in gen_mods:
                if m.proc in procs and m.exit is not None:
                    procs.remove(m.proc)
            if gens_done and nxt == len(units) and not running_cc:
                break

            # progress: the generator's share and the compiler's (bytes of C compiled of an estimate of all of it)
            written = sum(u.bytes for u in units)
            est = written if gens_done else (written / gen_frac if gen_frac > 0.02 else 0)
            comp = min(1.0, done_bytes / est) if est else 0.0
            frac = (0.2 * gen_frac + 0.75 * comp) if gen_mods else 0.95 * (nxt / len(units))
            pct = int(frac * 100)
            now = time.monotonic()
            if pct >= shown + 5 or now - last_print >= 30:
                gens = ", ".join(f"{m.name} {'done' if m.exit == 0 else f'{int(100 * m.ci / m.n) if m.n else 0}%'}"
                                 for m in gen_mods)
                say(f"  {pct:3d}%  " + (f"generating {gens}; " if gens else "") +
                    f"compiled {nxt - len(running_cc)} of {len(units)}{'' if gens_done else '+'} files, "
                    f"{len(running_cc)} running  [{fmt_time(now - t0)}]")
                shown, last_print = pct, now
            time.sleep(poll_s)

        if hooks:
            notes.append(f"Hook slots: {len(hooks)} function(s) of main can be hooked by native mods "
                         "(docs/hooks.md).")
            if hook_pass is not None and hook_pass.self_jumps:
                notes.append(_gen_hooks().self_jump_note(hook_pass.self_jumps))

        # 3. link
        objs = [units[0].obj, units[1].obj] + [o for m in mods for o in m.objs]
        rsp = os.path.join(work, "link.rsp")
        with open(rsp, "w", encoding="utf-8", newline="\n") as f:
            f.write(objs_rsp_text(objs))
        tmp = os.path.join(work, LIB_NAME + ".tmp")
        log = os.path.join(work, "link.log")
        say(f"Linking {len(objs)} objects...")
        rc = start(link_argv(t, tmp, rsp), log).wait()
        if rc != 0 or not os.path.isfile(tmp):
            raise StepFailed("linking", log, rc)
        size = os.path.getsize(tmp)

        # 4. into place
        if s.install_mode:
            if running():
                raise RebuildError("Savage Wind was started during the build, so the new library wasn't installed. "
                                   "Close it and build again (the cache makes that quick).")
            notes += install_library(tmp, s.install, s.gamelib_id, running=running)
        else:
            check_out(s)
            os.makedirs(os.path.dirname(s.out), exist_ok=True)
            move_into_place(tmp, s.out)
            write_atomic(s.out + ".id", (s.gamelib_id + "\n").encode())
        ok = True
        return BuildResult(s.out, size, time.monotonic() - t0, [m.name for m in gen_mods],
                           [m.name for m in mods if m.cached], notes)
    except BaseException:
        kill_everything(procs)
        raise
    finally:
        for d in staging:
            shutil.rmtree(d, ignore_errors=True)
        if claimed and ok and not s.keep_work:
            shutil.rmtree(work, ignore_errors=True)
        elif claimed and not ok:  # (keep the logs to look at, and the marker; the C and objects go)
            for root, _dirs, files in os.walk(work):
                for n in files:
                    if not n.endswith(".log") and not (n == WORK_MARK and root == work):
                        try:
                            os.remove(os.path.join(root, n))
                        except OSError:
                            pass
        lock.close()


# --- CLI ----------------------------------------------------------------------------------------------------------

def _resolve_out(out: str | None) -> str:
    if not out:
        return os.path.join(OUT_ROOT, LIB_NAME)
    out = os.path.abspath(out)
    return out if out.lower().endswith(".dll") else os.path.join(out, LIB_NAME)


def _read_id(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def differing_modules(a: str, b: str) -> list[str]:
    """The modules (of either folder's layout) whose files differ between two exefs folders, in load order."""
    names = set()
    for exefs in (a, b):
        try:
            names.update(n for n, *_ in predict_layout(exefs))
        except (OSError, RebuildError):
            names.update(n for n in LOAD_ORDER if os.path.isfile(os.path.join(exefs, n)))
    out = []
    for n in LOAD_ORDER:
        if n in names:
            pa, pb = os.path.join(a, n), os.path.join(b, n)
            if not (os.path.isfile(pa) and os.path.isfile(pb)) or file_sha256(pa) != file_sha256(pb):
                out.append(n)
    return out


def install_arg(args) -> str:
    """The Savage Wind folder a command works on: the one given, else default_install(). It must exist."""
    install = os.path.abspath(args.install_dir or default_install())
    if not os.path.isdir(install):
        how = ("" if args.install_dir else f" (from {INSTALL_ENV})" if os.environ.get(INSTALL_ENV, "").strip()
               else f" (the default; give the folder, or set {INSTALL_ENV} to it)")
        raise RebuildError(f"no Savage Wind folder at {install}{how}")
    return install


def prepare(args) -> tuple[Settings, Tools, list[str]]:
    """Settings from the command line, with every check that doesn't need a build. Returns notes to print."""
    install = install_arg(args)
    P = paths_for(install)
    t = find_tools(install)
    notes = []
    exefs = os.path.abspath(args.exefs) if args.exefs else P["exefs"]
    for n in ("main", "sdk"):
        if not os.path.isfile(os.path.join(exefs, n)):
            raise RebuildError(f"no {n} in {exefs}")
    gid, from_backup = args.id or _read_id(P["lib_id"]), False
    if not gid:
        gid, from_backup = _read_id(P["lib_vanilla_id"]), True
    if not gid:
        raise RebuildError(f"no {P['lib_id']}: set up the game files in Savage Wind first, or pass --id")
    if not ID_RE.match(gid):
        raise RebuildError(f"the id {gid!r} doesn't look like a GAMELIB_ID")
    exe_data = b""
    if os.path.isfile(t.exe):
        with open(t.exe, "rb") as f:
            exe_data = f.read()
        if (gid.encode() + b"\0") not in exe_data:
            if from_backup:  # (a backup left from another Savage Wind version: its id is no use)
                raise RebuildError(f"no {P['lib_id']}, and the backup's id {gid} isn't this savagewind.exe's: "
                                   "set up the game files in Savage Wind first, or pass --id")
            notes.append(f"Warning: savagewind.exe doesn't know the id {gid}; it may not load this library.")
    target = pick_target(gid, args.target)
    found = exe_flags(exe_data) if exe_data else {}
    cflags = found.get(target) or RELEASE_CFLAGS[target]
    zigflags = found.get("zig") or RELEASE_ZIGFLAGS
    if exe_data and (cflags != RELEASE_CFLAGS[target] or zigflags != RELEASE_ZIGFLAGS):
        notes.append("Note: savagewind.exe builds with other flags than 0.4.1-alpha.4; using the exe's.")
    if args.install and args.out:
        raise RebuildError("--install and --out can't be used together")
    out = P["lib"] if args.install else _resolve_out(args.out)
    if not same_file(exefs, P["exefs"]) and os.path.isfile(P["main"]):
        diff = differing_modules(exefs, P["exefs"])
        others = [n for n in diff if n != "main"]
        verb = lambda names: "differs" if len(names) == 1 else "differ"  # noqa: E731
        if args.install and others:
            raise RebuildError(f"{', '.join(others)} in {exefs} {verb(others)} from extracted\\exefs, and only a "
                               "patched main can be installed (install-patched-main), so code and data wouldn't match")
        if args.install and diff:
            raise RebuildError("this main isn't the installed one; run install-patched-main first, "
                               "so code and data match")
        if diff:
            notes.append(f"Note: {', '.join(diff)} {verb(diff)} from extracted\\exefs. The game reads data from "
                         "there, so install the same files (install-patched-main for main) before using this library.")
    jobs = max(1, min(64, args.jobs or os.cpu_count() or 4))
    root = os.path.abspath(args.work_root) if args.work_root else OUT_ROOT
    s = Settings(install=install, exefs=exefs, out=out, gamelib_id=gid, target=target,
                 flags=compile_flags(cflags, zigflags, t.zig), jobs=jobs, work=os.path.join(root, "work"),
                 cache=None if args.no_cache else os.path.join(root, "cache"), cache_keep=args.cache_keep,
                 install_mode=args.install, keep_work=args.keep_work, low_priority=args.low_priority,
                 hooks=read_hooks(getattr(args, "hooks", None), exefs))
    check_out(s)
    return s, t, notes


def read_hooks(files, exefs: str) -> list[int]:
    """The addresses the --hooks files list, sorted, after a quick check that each lies in main's code (the
    generated function table checks that they are function starts, during the build)."""
    if not files:
        return []
    gh = _gen_hooks()
    try:
        addrs = gh.read_hooks_files(files)
    except gh.HookError as e:
        raise RebuildError(f"--hooks: {e}") from None
    if not addrs:
        raise RebuildError("--hooks: the file lists no addresses")
    text_off, text_size = nso_header(os.path.join(exefs, "main"))["text"]
    lo = MODULES_LO + text_off  # (main is the first module: tools/layout.py)
    bad = gh.check_in_code(addrs, lo, lo + text_size)
    if bad:
        raise RebuildError("--hooks: " + "; ".join(bad))
    return addrs


def cmd_build(args) -> int:
    s, t, notes = prepare(args)
    _say(f"Rebuilding the game code: {s.out}")
    _say(f"  from {s.exefs}")
    _say(f"  id {s.gamelib_id} ({s.target} target), {s.jobs} jobs, cache "
         f"{s.cache if s.cache else 'off'}")
    if s.hooks:
        _say(f"  hook slots for {len(s.hooks)} function(s) of main: "
             + ", ".join(f"{a:#x}" for a in s.hooks[:8]) + (", ..." if len(s.hooks) > 8 else ""))
    for n in notes:
        _say(n)
    if args.dry_run:
        work = s.work
        lay = os.path.join(work, "layout.txt")
        _say("Commands (nothing is run):")
        _say("  " + command_line(gen_layout_argv(t, s.exefs, work)))
        for name, base, size, _bid in predict_layout(s.exefs):
            _say(f"  {command_line(gen_module_argv(t, name, s.exefs, lay, os.path.join(work, name)))}")
        if s.hooks:
            _say(f"  (tools/gen_hooks.py: renames the {len(s.hooks)} hooked function(s) in main's C and adds "
                 "main_hooks.c)")
        src = os.path.join(work, "main", "main_000.c")
        _say("  " + command_line(compile_argv(t, s.flags, s.gamelib_id, src, os.path.join(work, "main_000.o"))))
        _say("  " + command_line(link_argv(t, os.path.join(work, LIB_NAME + ".tmp"), os.path.join(work, "link.rsp"))))
        _say(f"  swgen runs without Control Flow Guard: {'yes' if t.gen_no_cfg else 'no'}")
        return 0
    if args.install:
        _refuse_if_running(game_running)
        msg = backup_vanilla_library(s.install)  # (before the build: a failed backup costs no build time)
        if msg:
            _say(msg)
    probe = os.path.dirname(s.work)
    while not os.path.isdir(probe) and os.path.dirname(probe) != probe:
        probe = os.path.dirname(probe)
    free = shutil.disk_usage(probe).free if os.path.isdir(probe) else None
    if free is not None and free < 4 * 2**30:
        _say(f"Warning: only {free / 2**30:.1f} GB free for the build; it may need several GB.")
    try:
        r = run_build(s, t)
    except StepFailed as e:
        _say(f"FAILED: {e}")
        for ln in log_tail(e.log):
            _say(f"  | {ln}")
        _say(f"Logs kept in {s.work}")
        return 1
    except KeyboardInterrupt:
        _say("Stopped.")
        return 130
    _say(f"Done in {fmt_time(r.seconds)}: {r.dll} ({fmt_mb(r.size)}, id {s.gamelib_id})")
    if r.generated:
        _say(f"  regenerated: {', '.join(r.generated)}")
    if r.cached:
        _say(f"  reused from the cache: {', '.join(r.cached)}")
    for n in r.notes:
        _say(n)
    for line in try_hint(s, r.dll):
        _say(line)
    return 0


def try_hint(s: Settings, dll: str) -> list[str]:
    """The lines that say how to start the game with a new library. Hook slots only do something when modhost
    loads the library, so a build with hooks names modhost, not the library, as SAVAGEWIND_GAME_LIB."""
    if not s.hooks:
        return [] if s.install_mode else [f"To try it, start savagewind.exe with SAVAGEWIND_GAME_LIB={dll}, "
                                          "or use --install."]
    lines = ["Hooks only run when the game starts through modhost (docs/hooks.md). Start savagewind.exe with:",
             f"  SAVAGEWIND_GAME_LIB={MODHOST_DLL}"]
    if not s.install_mode:  # (installed, it is extracted\swgame.dll: modhost's default)
        lines.append(f"  SWMOD_REAL_GAME_LIB={dll}")
    lines.append("  SWMOD_DIR=<folder with the hook mods>  (optional; default <data root>\\mods\\native)")
    if not os.path.isfile(MODHOST_DLL):
        lines.append("modhost.dll isn't built yet: python tools/build_native.py build")
    return lines


def cmd_install_main(args) -> int:
    for m in install_patched_main(os.path.abspath(args.patched), os.path.abspath(args.install_dir)):
        _say(m)
    return 0


def cmd_restore(args) -> int:
    for m in restore(os.path.abspath(args.install_dir), args.only):
        _say(m)
    return 0


def cmd_status(args) -> int:
    install = install_arg(args)
    _say(f"folder   {install}")
    P = paths_for(install)
    state = load_state(install)
    gid = _read_id(P["lib_id"])
    lib = "missing"
    if os.path.isfile(P["lib"]):
        rebuilt = state.get("library_installed_sha256") == file_sha256(P["lib"])
        lib = f"{'rebuilt by sw_rebuild' if rebuilt else 'from Savage Wind setup'}, id {gid or '?'}"
    main = "missing"
    if os.path.isfile(P["main"]):
        main = "patched (installed by sw_rebuild)" if state.get("main_installed_sha256") == file_sha256(P["main"]) \
            else "as set up"
    _say(f"library  {lib}")
    _say(f"main     {main}")
    lib_b = "no"
    if os.path.isfile(P["lib_vanilla"]):
        why = library_backup_problem(install, state)
        lib_b = f"yes, but {why}" if why else "yes"
    main_b = "no"
    if os.path.isfile(P["main_backup"]):
        stale = os.path.isfile(P["main"]) and _main_build_id(P["main"]) != _main_build_id(P["main_backup"])
        main_b = "yes, but for another game version" if stale else "yes"
    _say(f"backups  library: {lib_b}; main: {main_b}")
    _say(f"game     {'running' if game_running() else 'not running'}")
    return 0


INSTALL_HELP = (f"the Savage Wind folder (default: %%{INSTALL_ENV}%% when set, else "
                r"%%USERPROFILE%%\Documents\SavageWind\Savage Wind)")


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="rebuild swgame.dll from an exefs folder")
    b.add_argument("install_dir", nargs="?", help=INSTALL_HELP)
    b.add_argument("--exefs", help="folder with main, subsdk0 and sdk (default: <install>\\extracted\\exefs)")
    b.add_argument("--out", help=f"output .dll or folder (default: {os.path.join(OUT_ROOT, LIB_NAME)})")
    b.add_argument("--install", action="store_true",
                   help="replace extracted\\swgame.dll (the vanilla one is backed up first)")
    b.add_argument("-j", "--jobs", type=int, default=0, help="compilers at once (default: CPU count)")
    b.add_argument("--id", help="GAMELIB_ID to build with (default: extracted\\swgame.dll.id)")
    b.add_argument("--target", choices=("normal", "compat"), help="CPU target (default: from the id)")
    b.add_argument("--no-cache", action="store_true", help="don't reuse or store compiled modules")
    b.add_argument("--cache-keep", type=int, default=2,
                   help="cached builds kept per module: the most recently used ones (default 2)")
    b.add_argument("--work-root", help="folder to make the work and cache folders in; folders there that "
                                       f"sw_rebuild didn't make are refused (default: {OUT_ROOT})")
    b.add_argument("--hooks", action="append", metavar="FILE",
                   help="give these functions of main hook slots for native mods (a list of addresses, text or "
                        "JSON; may be repeated). See docs/hooks.md")
    b.add_argument("--keep-work", action="store_true", help="keep the generated C and logs")
    b.add_argument("--low-priority", action="store_true", help="run the builders at below-normal priority")
    b.add_argument("--dry-run", action="store_true", help="print the commands and stop")
    m = sub.add_parser("install-patched-main", help="put a patched main into extracted\\exefs (vanilla backed up)")
    m.add_argument("patched")
    m.add_argument("install_dir")
    r = sub.add_parser("restore", help="put the vanilla main and library back")
    r.add_argument("install_dir")
    r.add_argument("--only", choices=("main", "library"))
    st = sub.add_parser("status", help="say what is installed")
    st.add_argument("install_dir", nargs="?", help=INSTALL_HELP)
    return ap


def main(argv: list[str] | None = None) -> int:
    _safe_stdout()
    args = parser().parse_args(argv)
    try:
        return {"build": cmd_build, "install-patched-main": cmd_install_main, "restore": cmd_restore,
                "status": cmd_status}[args.cmd](args)
    except RebuildError as e:
        print(f"ERROR {e}", flush=True)
        return 2
    except OSError as e:
        print(f"ERROR {e}", flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
