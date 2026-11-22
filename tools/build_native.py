#!/usr/bin/env python3
"""Build and install modhost, the native code-mod host for Savage Wind, and the example mods.

    python tools/build_native.py build       # compile modhost.dll and the examples into build/native/
    python tools/build_native.py install     # copy them into your Savage Wind folder (--dry-run to only show)
    python tools/build_native.py uninstall   # remove what install copied (only files it wrote, unchanged)
    python tools/build_native.py check       # with the game running: ask it which mods loaded
    python tools/build_native.py imports <file.dll>...   # what a DLL imports and exports
    python tools/build_native.py selftest    # build a fake game and run modhost against it (no game needed)

modhost.dll stands in for the game code library: start Savage Wind with SAVAGEWIND_GAME_LIB=<path to
modhost.dll> and it loads the real extracted/swgame.dll, then every DLL in <data root>/mods/native/.
Savage Wind itself is not changed. Compiling uses the zig and savagewind.dll.a that ship with Savage Wind.

Options: --install <Savage Wind folder> (default: SWMOD_INSTALL, else Documents/SavageWind/Savage Wind),
--data-root <folder> (default: SAVAGEWIND_DATA_ROOT, else the Savage Wind folder), --out <build folder>.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NATIVE = REPO / "native"
HOST_SRC = NATIVE / "modhost"
DEFAULT_OUT = REPO / "build" / "native"
TARGET = "x86_64-windows-gnu"
CFLAGS = ("-target", TARGET, "-std=gnu11", "-O2", "-g0", "-Wall", "-Wextra")
HOST_DLL = "modhost.dll"
GAME_LIB = "swgame.dll"
RUNTIME_DLL = "savagewind.exe"  # the DLL name savagewind.dll.a imports from
MOD_SUBDIR = Path("mods") / "native"


class BuildError(RuntimeError):
    """Something is missing or a compile failed; the message says what to do."""


# ------------------------------------------------------------------ what gets built

@dataclass(frozen=True)
class Target:
    name: str                        # short name, used for object files
    sources: tuple[Path, ...]
    runtime: bool                    # links against savagewind.dll.a (imports from savagewind.exe)
    out_name: str = ""               # output file name (default <name>.dll)
    subdir: str = ""                 # under the build folder
    defines: tuple[str, ...] = ()
    export: str = ""                 # an export the result must have

    @property
    def filename(self) -> str:
        return self.out_name or f"{self.name}.dll"

    def output(self, out: Path) -> Path:
        return out / self.subdir / self.filename if self.subdir else out / self.filename


# modhost's sources. settings.c (mod settings, API 3: docs/mod-settings.md) is built in once it is there, so this
# list never names a file that is missing.
SETTINGS_SRC = HOST_SRC / "settings.c"
HOST_SOURCES = (HOST_SRC / "modhost.c", *((SETTINGS_SRC,) if SETTINGS_SRC.is_file() else ()))
HOST = Target("modhost", HOST_SOURCES, runtime=True, export="swgame_tables")
EXAMPLES = (
    Target("hello", (NATIVE / "examples" / "hello" / "hello.c",), runtime=False, subdir="mods", export="swmod_init"),
)
# Examples that need game code built with hook slots (docs/hooks.md): built, but not copied by `install`.
HOOK_EXAMPLES = (
    Target("stamina_hook", (NATIVE / "examples" / "stamina_hook" / "stamina_hook.c",), runtime=False,
           subdir="hook-mods", export="swmod_init"),
)
# The ghost example (docs/ghost.md): needs no hook slots, but calls game code by address (1.9.0 only) and is
# experimental, so it is built into its own folder and not copied by `install` either.
GHOST_EXAMPLES = (
    Target("ghost", (NATIVE / "examples" / "ghost" / "ghost.c",), runtime=False, subdir="ghost-mods",
           export="swmod_init"),
    # More Room (docs/room.md): part of multiplayer (the costumes), so it sits with the ghost mod.
    Target("room", (NATIVE / "examples" / "room" / "room.c",), runtime=False, subdir="ghost-mods",
           export="swmod_init"),
)
# The basics examples (docs/game-basics.md): small mods on native/modhost/botw.h (1.9.0 only), built into their own
# folder and not copied by `install`.
BASICS_EXAMPLES = tuple(
    Target(n, (NATIVE / "examples" / n / f"{n}.c",), runtime=False, subdir="basics-mods", export="swmod_init")
    for n in ("basics_show_pos", "basics_inf_stamina", "basics_teleport_up"))
# The settings example (docs/mod-settings.md): a mod with every kind of setting, built into its own folder and not
# copied by `install` (modman add native/examples/settings_demo takes it from there). Built once its source is there.
_SETTINGS_DEMO = NATIVE / "examples" / "settings_demo" / "settings_demo.c"
SETTINGS_EXAMPLES = tuple(
    Target("settings_demo", (_SETTINGS_DEMO,), runtime=False, subdir="settings-mods", export="swmod_init")
    for _ in range(1 if _SETTINGS_DEMO.is_file() else 0))
# The emotes mod (docs/emotes.md): calls game code by address (1.9.0 only), experimental: its own folder, not copied
# by `install` (modman add native/examples/emotes takes it from there).
EMOTES_EXAMPLES = (
    Target("emotes", (NATIVE / "examples" / "emotes" / "emotes.c",), runtime=False, subdir="emotes-mods",
           export="swmod_init"),
)
_ST = HOST_SRC / "selftest"
SELFTEST = (
    Target("fake_runtime", (_ST / "fake_runtime.c",), runtime=False, out_name=RUNTIME_DLL, subdir="selftest"),
    Target("fake_game", (_ST / "fake_game.c",), runtime=False, out_name="fake_swgame.dll", subdir="selftest",
           export="swgame_tables"),
    Target("fake_game_other", (_ST / "fake_game.c",), runtime=False, out_name="fake_swgame_other.dll",
           subdir="selftest", defines=("SELFTEST_OTHER_ID",), export="swgame_tables"),
    Target("probe", (_ST / "probe.c",), runtime=False, subdir="selftest/mods", export="swmod_init"),
    Target("bad", (_ST / "probe.c",), runtime=False, subdir="selftest/mods", defines=("PROBE_FAIL",),
           export="swmod_init"),
    Target("noinit", (_ST / "probe.c",), runtime=False, subdir="selftest/mods", defines=("PROBE_NOINIT",)),
)
# The hook self-test: made-up "generated" functions (selftest/hooked/) get hook slots from tools/gen_hooks.py in
# <out>/selftest/hooked-gen/, then build a fake game library; hook_probe and the stamina_hook example hook it.
_HK = _ST / "hooked"
HOOKED_GEN_FILES = ("cpu.h", "main_000.c", "main_001.c", "main_table.c")
SELFTEST_HOOKS = (
    Target("hook_probe", (_ST / "hook_probe.c",), runtime=False, subdir="selftest/hook-mods", export="swmod_init"),
)
# The hot-load self-test (modload / modreload / modunload): one test mod built three ways (selftest/hot_probe.c).
SELFTEST_HOT = (
    Target("hot_v1", (_ST / "hot_probe.c",), runtime=False, subdir="selftest/hot", export="swmod_init"),
    Target("hot_v2", (_ST / "hot_probe.c",), runtime=False, subdir="selftest/hot", defines=("HOT_V2",),
           export="swmod_init"),
    Target("hot_fail", (_ST / "hot_probe.c",), runtime=False, subdir="selftest/hot", defines=("HOT_FAIL",),
           export="swmod_init"),
)
# The race run: the self-test's own build of modhost (it can pause a game thread between hook_entry's has_hot check
# and the gate), a mod loaded at game start that plays a game thread (race_probe), and two mods for modload that
# break the rules (hot_misuse.c: an hle_override from a hook, as a tail call and while modunload waits).
HOST_TEST = Target("modhost_test", HOST_SOURCES, runtime=True, out_name="modhost_test.dll",
                   subdir="selftest", defines=("MODHOST_SELFTEST_PAUSE",), export="swgame_tables")
SELFTEST_RACE = (
    Target("race_probe", (_ST / "race_probe.c",), runtime=False, subdir="selftest/race-start", export="swmod_init"),
    Target("misuse_tail", (_ST / "hot_misuse.c",), runtime=False, subdir="selftest/misuse", defines=("MISUSE_TAIL",),
           export="swmod_init"),
    Target("misuse_rearm", (_ST / "hot_misuse.c",), runtime=False, subdir="selftest/misuse",
           defines=("MISUSE_REARM",), export="swmod_init"),
)
# The file log's overlap run (run D): a mod loaded at game start that overrides one of the four file opens, and one for
# modload that overrides another (selftest/fslog_probe.c).
SELFTEST_FSLOG = (
    Target("fslog_probe", (_ST / "fslog_probe.c",), runtime=False, subdir="selftest/fslog-start", export="swmod_init"),
    Target("fslog_probe_hot", (_ST / "fslog_probe.c",), runtime=False, subdir="selftest/fslog-hot",
           defines=("FSLOG_PROBE_HOT",), export="swmod_init"),
)
# The file redirect runs (fslog.c, API 4): a mod loaded at game start that names redirects (selftest/fsredir_probe.c).
SELFTEST_FSREDIR = Target("fsredir_probe", (_ST / "fsredir_probe.c",), runtime=False, subdir="selftest/fsredir-start",
                          export="swmod_init")
# The override chain run (run E): three mods loaded at game start wrap one function with hle_override; the second one's
# swmod_init fails (selftest/chain_probe.c).
SELFTEST_CHAIN = tuple(
    Target(f"chain{n}", (_ST / "chain_probe.c",), runtime=False, subdir="selftest/chain-start",
           defines=(f"CHAIN_TAG={n}", *(("CHAIN_FAIL",) if n == 2 else ())), export="swmod_init")
    for n in (1, 2, 3))


def hooked_game_target(out: Path) -> Target:
    gen = out / "selftest" / "hooked-gen"
    return Target("fake_game_hooked", (gen / "main_000.c", gen / "main_001.c", gen / "main_table.c",
                                       gen / "main_hooks.c", _HK / "entry.c"),
                  runtime=False, out_name="fake_swgame_hooked.dll", subdir="selftest", export="swgame_tables")


def prepare_hooked_game(out: Path) -> Path:
    """Copy the made-up generated files to <out>/selftest/hooked-gen and add hook slots with tools/gen_hooks.py."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gen_hooks  # noqa: E402 (tools/gen_hooks.py)
    gen = out / "selftest" / "hooked-gen"
    gen.mkdir(parents=True, exist_ok=True)
    for n in HOOKED_GEN_FILES:
        shutil.copyfile(_HK / n, gen / n)
    try:
        gen_hooks.apply(str(gen), gen_hooks.read_hooks_files(str(_HK / "hooks.txt")))
    except gen_hooks.HookError as e:
        raise BuildError(f"gen_hooks failed on the self-test files: {e}") from None
    return gen


# ------------------------------------------------------------------ the toolchain (Savage Wind's own zig)

@dataclass(frozen=True)
class Toolchain:
    zig: Path
    implib: Path
    rt: Path | None  # zig/rt: the release's prebuilt C runtime and link.rsp (None: a full zig, linked with zig cc)


def default_install() -> Path:
    env = os.environ.get("SWMOD_INSTALL")
    return Path(env) if env else Path.home() / "Documents" / "SavageWind" / "Savage Wind"


def default_data_root(install: Path) -> Path:
    env = os.environ.get("SAVAGEWIND_DATA_ROOT")
    return Path(env) if env else install


def find_toolchain(install: Path) -> Toolchain:
    zig = install / "zig" / "zig.exe"
    implib = install / "savagewind.dll.a"
    if not zig.is_file():
        raise BuildError(f"No zig at {zig}. Point --install at your Savage Wind folder.")
    if not implib.is_file():
        raise BuildError(f"No savagewind.dll.a in {install}. Point --install at your Savage Wind folder.")
    rt = install / "zig" / "rt"
    return Toolchain(zig, implib, rt if (rt / "link.rsp").is_file() else None)


def compile_cmd(tc: Toolchain, src: Path, obj: Path, defines=(), includes=()) -> list[str]:
    return [str(tc.zig), "cc", *CFLAGS, *(f"-I{i}" for i in includes), *(f"-D{d}" for d in defines),
            "-c", str(src), "-o", str(obj)]


def link_cmd(tc: Toolchain, objs, out: Path, runtime: bool) -> list[str]:
    """The release links the game code with `zig lld-link @zig/rt/link.rsp` (runtime/gamebuild.c); do the same."""
    if tc.rt:
        cmd = [str(tc.zig), "lld-link", f"@{tc.rt / 'link.rsp'}", f"-LIBPATH:{tc.rt}", f"-OUT:{out}",
               *(str(o) for o in objs)]
    else:
        cmd = [str(tc.zig), "cc", "-shared", "-target", TARGET, "-o", str(out), *(str(o) for o in objs)]
    if runtime:
        cmd.append(str(tc.implib))
    return cmd


def build_env(out: Path) -> dict:
    env = dict(os.environ)
    cache = str(out / "zig-cache")  # (not the user profile: zig's caches can get big)
    env["ZIG_GLOBAL_CACHE_DIR"] = cache
    env["ZIG_LOCAL_CACHE_DIR"] = cache
    return env


def plan_build(tc: Toolchain, out: Path, targets) -> list[tuple[Target, list[list[str]]]]:
    """The commands for each target, in order: compile each source, then link."""
    plan = []
    for t in targets:
        cmds, objs = [], []
        for src in t.sources:
            obj = out / "obj" / f"{t.name}-{src.stem}.o"
            cmds.append(compile_cmd(tc, src, obj, t.defines, (HOST_SRC,)))
            objs.append(obj)
        cmds.append(link_cmd(tc, objs, t.output(out), t.runtime))
        plan.append((t, cmds))
    return plan


def run_captured(cmd: list[str], env: dict, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run cmd and capture its output as text. zig and modhost write UTF-8 (paths can have any characters), so decode
    it as UTF-8 and never fail on it."""
    return subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", env=env,
                          cwd=str(cwd) if cwd else None)


def run_cmd(cmd: list[str], env: dict) -> None:
    r = run_captured(cmd, env)
    if r.returncode:
        tail = "\n".join((r.stderr or r.stdout).strip().splitlines()[-20:])
        raise BuildError(f"{Path(cmd[0]).name} {cmd[1]} failed:\n{tail}")


def build(tc: Toolchain, out: Path, targets, runner=run_cmd) -> list[Path]:
    env = build_env(out)
    built = []
    for t, cmds in plan_build(tc, out, targets):
        t.output(out).parent.mkdir(parents=True, exist_ok=True)
        (out / "obj").mkdir(parents=True, exist_ok=True)
        for cmd in cmds:
            runner(cmd, env)
        built.append(t.output(out))
    return built


# ------------------------------------------------------------------ PE files: imports and exports

class PeError(ValueError):
    pass


@dataclass
class PeInfo:
    machine: int
    dll: bool
    imports: dict[str, list[str]] = field(default_factory=dict)  # DLL name -> function names ("#12" = ordinal)
    exports: list[str] = field(default_factory=list)


def _cstr(data: bytes, off: int) -> str:
    end = data.find(b"\0", off)
    if off < 0 or off >= len(data) or end < 0:
        raise PeError("bad string offset")
    return data[off:end].decode("latin-1")


def pe_info(data: bytes) -> PeInfo:
    """Read the import and export tables of a PE file (PE32 or PE32+)."""
    try:
        if data[:2] != b"MZ":
            raise PeError("not a Windows executable (no MZ header)")
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            raise PeError("not a PE file")
        machine, nsec, _, _, _, opt_size, chars = struct.unpack_from("<HHIIIHH", data, pe + 4)
        opt = pe + 24
        magic = struct.unpack_from("<H", data, opt)[0]
        if magic == 0x20B:
            ndirs_off, dirs = opt + 108, opt + 112
        elif magic == 0x10B:
            ndirs_off, dirs = opt + 92, opt + 96
        else:
            raise PeError("unknown optional header")
        ndirs = struct.unpack_from("<I", data, ndirs_off)[0]
        sections = []
        for i in range(nsec):
            s = opt + opt_size + 40 * i
            vsize, va, rsize, roff = struct.unpack_from("<IIII", data, s + 8)
            sections.append((va, max(vsize, rsize), roff, rsize))

        def off(rva: int) -> int:
            for va, size, roff, rsize in sections:
                if va <= rva < va + size and rva - va < rsize:
                    return roff + rva - va
            raise PeError(f"address {rva:#x} is in no section")

        def directory(i: int) -> tuple[int, int]:
            return struct.unpack_from("<II", data, dirs + 8 * i) if i < ndirs else (0, 0)

        info = PeInfo(machine, bool(chars & 0x2000))
        exp_rva, _ = directory(0)
        if exp_rva:
            e = off(exp_rva)
            nnames, names_rva = struct.unpack_from("<I", data, e + 24)[0], struct.unpack_from("<I", data, e + 32)[0]
            for i in range(nnames):
                info.exports.append(_cstr(data, off(struct.unpack_from("<I", data, off(names_rva) + 4 * i)[0])))
        imp_rva, _ = directory(1)
        if imp_rva:
            d = off(imp_rva)
            wide = magic == 0x20B
            while True:
                lookup, _, _, name_rva, thunk = struct.unpack_from("<IIIII", data, d)
                if not name_rva:
                    break
                dll = _cstr(data, off(name_rva))
                funcs = info.imports.setdefault(dll, [])
                t = off(lookup or thunk)
                while True:
                    v = struct.unpack_from("<Q" if wide else "<I", data, t)[0]
                    if not v:
                        break
                    if v >> (63 if wide else 31):
                        funcs.append(f"#{v & 0xFFFF}")
                    else:
                        funcs.append(_cstr(data, off(v & 0x7FFFFFFF) + 2))
                    t += 8 if wide else 4
                d += 20
        return info
    except struct.error as e:
        raise PeError(f"truncated PE file ({e})") from None


def imports_from(info: PeInfo, dll: str) -> list[str]:
    for name, funcs in info.imports.items():
        if name.lower() == dll.lower():
            return funcs
    return []


def describe(path: Path, rel_to: Path | None = None) -> str:
    info = pe_info(path.read_bytes())
    sw = imports_from(info, RUNTIME_DLL)
    name = str(path.relative_to(rel_to)) if rel_to else str(path)
    exp = ", ".join(info.exports) if info.exports else "nothing"
    return (f"{name}: exports {exp}; imports {len(sw)} function(s) from {RUNTIME_DLL}"
            + (f" ({', '.join(sorted(sw))})" if sw else ""))


def check_exports(paths_targets) -> None:
    for path, t in paths_targets:
        if t.export and t.export not in pe_info(path.read_bytes()).exports:
            raise BuildError(f"{path.name} was built but doesn't export {t.export}")


# ------------------------------------------------------------------ install / uninstall

class InstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class Step:
    src: Path | None  # None: remove dst
    dst: Path


RECORD = "modhost.install.json"  # in <data root>/extracted: what install wrote, with each file's SHA-256


def install_paths(data_root: Path, examples=EXAMPLES) -> dict:
    game = data_root / "extracted"
    return {
        "game": game,
        "real": game / GAME_LIB,
        "real_id": game / f"{GAME_LIB}.id",
        "host": game / HOST_DLL,
        "host_id": game / f"{HOST_DLL}.id",
        "mods": data_root / MOD_SUBDIR,
        "examples": [data_root / MOD_SUBDIR / t.filename for t in examples],
        "record": game / RECORD,
    }


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(data_root: Path, path: Path) -> str:
    return path.relative_to(data_root).as_posix()


def _ours(rel: str) -> bool:
    """A path install may write and uninstall may remove: modhost.dll(.id) in extracted/, or a DLL in mods/native/.
    (The record is a plain file in the user's folder: nothing it lists outside these is ever removed.)"""
    if rel in (f"extracted/{HOST_DLL}", f"extracted/{HOST_DLL}.id"):
        return True
    parts = rel.split("/")
    return (len(parts) == 3 and tuple(parts[:2]) == MOD_SUBDIR.parts and parts[2].lower().endswith(".dll")
            and "\\" not in parts[2] and ":" not in parts[2])


def read_record(data_root: Path) -> dict[str, str]:
    """{path relative to the data root: SHA-256} of the files install wrote; {} when there is no readable record."""
    try:
        files = json.loads(install_paths(data_root)["record"].read_text(encoding="utf-8")).get("files", {})
    except (OSError, ValueError, AttributeError):
        return {}
    if not isinstance(files, dict):
        return {}
    return {k: v for k, v in files.items() if isinstance(k, str) and isinstance(v, str) and _ours(k)}


def plan_install(out: Path, data_root: Path, examples=EXAMPLES, force: bool = False) -> list[Step]:
    """Copies: modhost.dll next to (not over) extracted/swgame.dll, swgame.dll.id as modhost.dll.id (Savage Wind's
    start check reads <library>.id: runtime/gamelib.c gamelib_present), and the example mods into mods/native/.
    Refuses to replace a file that install didn't write (unless force)."""
    p = install_paths(data_root, examples)
    if not p["real"].is_file():
        raise InstallError(f"No game code at {p['real']}. Set up the game files in Savage Wind first.")
    if not p["real_id"].is_file():
        raise InstallError(f"No {p['real_id'].name} next to it. Set up the game files in Savage Wind first.")
    host = out / HOST_DLL
    if not host.is_file():
        raise InstallError(f"{HOST_DLL} isn't built yet. Run: python tools/build_native.py build")
    steps = [Step(host, p["host"]), Step(p["real_id"], p["host_id"])]
    for t, dst in zip(examples, p["examples"]):
        src = t.output(out)
        if src.is_file():
            steps.append(Step(src, dst))
    for s in steps:  # never over the real game code
        if s.dst.name.lower() in (GAME_LIB, f"{GAME_LIB}.id") or not _ours(_rel(data_root, s.dst)):
            raise InstallError(f"refusing to write {s.dst}")
    if not force:
        record = read_record(data_root)
        foreign = [s.dst for s in steps if s.dst.is_file()
                   and sha256_of(s.dst) not in (sha256_of(s.src), record.get(_rel(data_root, s.dst)))]
        if foreign:
            raise InstallError("These files are already there and install didn't write them:\n"
                               + "\n".join(f"  {f}" for f in foreign)
                               + "\nMove them away, or run install --force to replace them.")
    return steps


def record_install(data_root: Path, steps: list[Step]) -> Path:
    """Add the files just copied, with their SHA-256, to the install record (uninstall removes only those)."""
    files = read_record(data_root)
    for s in steps:
        if s.src is not None:
            files[_rel(data_root, s.dst)] = sha256_of(s.dst)
    path = install_paths(data_root)["record"]
    path.write_text(json.dumps({"tool": "build_native.py install", "files": files}, indent=1) + "\n",
                    encoding="utf-8")
    return path


def plan_uninstall(data_root: Path) -> tuple[list[Step], list[tuple[Path, str]]]:
    """(the files to remove: install wrote them and they are unchanged; the files left, each with why)."""
    record = read_record(data_root)
    p = install_paths(data_root)
    known = {_rel(data_root, d) for d in (p["host"], p["host_id"], *p["examples"])}
    steps, left = [], []
    for rel in sorted(known | set(record)):
        path = data_root / rel
        if not path.is_file():
            continue
        if rel not in record:
            left.append((path, "install didn't write it"))
        elif sha256_of(path) != record[rel]:
            left.append((path, "changed since install"))
        else:
            steps.append(Step(None, path))
    return steps, left


def forget_install(data_root: Path) -> None:
    install_paths(data_root)["record"].unlink(missing_ok=True)


def run_steps(steps: list[Step], dry_run: bool = False) -> list[str]:
    lines = []
    for s in steps:
        if s.src is None:
            lines.append(f"remove {s.dst}")
            if not dry_run:
                s.dst.unlink()
        else:
            lines.append(f"copy {s.src.name} -> {s.dst}")
            if not dry_run:
                s.dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s.src, s.dst)
    return lines


PS_SINGLE_QUOTES = "'\u2018\u2019\u201a\u201b"  # PowerShell takes the curly ones as single quotes too


def ps_quote(value) -> str:
    """A PowerShell single-quoted string. Each single quote inside (' and the curly ones PowerShell also reads as
    quotes) is written twice, so a path like C:\\Users\\O\u2019Neil stays one string."""
    return "'" + "".join(ch * 2 if ch in PS_SINGLE_QUOTES else ch for ch in str(value)) + "'"


def launch_lines(install: Path, data_root: Path, port: int = 7777) -> list[str]:
    host = install_paths(data_root)["host"]
    lines = [f"$env:SAVAGEWIND_GAME_LIB = {ps_quote(host)}",
             f"$env:SAVAGEWIND_CONTROL = {ps_quote(port)}   # optional: lets `build_native.py check` ask the game"]
    if data_root != install:
        lines.append(f"$env:SAVAGEWIND_DATA_ROOT = {ps_quote(data_root)}")
    lines.append(f"& {ps_quote(install / 'savagewind.exe')} --play")
    return lines


# ------------------------------------------------------------------ check (the game is running)

def check_game(port: int, connect=None) -> int:
    """Ask the running game (SAVAGEWIND_CONTROL=<port>) for `mods` and `hello`."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from control import Control, ControlError  # noqa: E402 (tools/control.py)
    connect = connect or (lambda: Control(port, timeout=10.0))
    try:
        c = connect()
    except (OSError, ControlError) as e:
        print(f"No answer from the game on port {port}: {e}")
        print("Start it with SAVAGEWIND_CONTROL set, and SAVAGEWIND_GAME_LIB for modhost (see `install`).")
        return 1
    try:
        try:
            mods = c.cmd("mods")
        except ControlError as e:
            if not str(e).startswith("Unknown command"):
                print(f"The game didn't answer `mods`: {e}")
                print("Commands only run while the game shows frames (not while it loads or is minimised). Try again.")
                return 1
            print(f"The game runs, but modhost isn't loaded ({e}).")
            print("Start it with SAVAGEWIND_GAME_LIB pointing at modhost.dll; savagewind.log says which game code"
                  " it loaded ([gamelib] and [modhost] lines).")
            return 1
        try:
            hello = {"ok": True, "data": c.cmd("hello")}
        except ControlError as e:
            hello = {"ok": False, "error": str(e)}
    except (OSError, ControlError) as e:
        print(f"Lost the connection to the game: {e}")
        return 1
    finally:
        c.close()
    print(f"modhost is running: {mods.get('loaded')} of {mods.get('found')} mod(s) loaded"
          f" (game {mods.get('game_version')}, API {mods.get('api')})")
    i = 0
    while f"mods.{i}.file" in mods:
        skipped = mods.get(f"mods.{i}.skipped")
        print(f"  {mods[f'mods.{i}.file']}: {mods.get(f'mods.{i}.status')}"
              + (f" (commands not added, name taken or table full: {skipped})" if skipped else ""))
        i += 1
    print(f"hello -> {json.dumps(hello)}")
    return 0


# ------------------------------------------------------------------ selftest (a fake game, no Savage Wind running)

_CHILD = r"""
import ctypes, json, sys, time
rt = ctypes.CDLL(sys.argv[1])        # the fake savagewind.exe: loaded first, so modhost's imports bind to it
host = ctypes.CDLL(sys.argv[2])
host.swgame_tables.restype = ctypes.c_void_p
tables = host.swgame_tables()
again = host.swgame_tables()
run = rt.fake_run
run.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
out = {"tables": bool(tables), "same_twice": tables == again, "steps": [], "ms": []}
buf = ctypes.create_string_buffer(65536)
for line in sys.argv[3:]:
    t = time.perf_counter()
    if line.startswith("!pause "):    # (modhost_test.dll only: game threads pause before the gate)
        host.modhost_test_pause(ctypes.c_uint32(int(line.split()[1])))
        reply = {"ok": True, "data": {}}
    elif line.startswith("!sleep "):
        time.sleep(int(line.split()[1]) / 1000)
        reply = {"ok": True, "data": {}}
    else:
        run(line.encode(), buf, len(buf))
        reply = json.loads(buf.value.decode())
    out[line] = reply
    out["steps"].append(reply)  # (in order: a line can come more than once)
    out["ms"].append(round((time.perf_counter() - t) * 1000, 1))
print(json.dumps(out))
"""

def _api_version() -> int:
    """swmod.h's SWMOD_API_VERSION: the mod API of the modhost this builds (and modman's highest loader_api)."""
    try:
        m = re.search(r"^#define SWMOD_API_VERSION (\d+)", (HOST_SRC / "swmod.h").read_text(encoding="utf-8"), re.M)
    except OSError:
        m = None
    return int(m.group(1)) if m else 2


API_VERSION = _api_version()  # swmod.h SWMOD_API_VERSION (the host the selftest builds)
SELFTEST_LINES = ("mods", "hello", "probe", "probe fail", "#overrides", "#guest_calls", "#commands")
FAKE_BUILTINS = ["help", "ping"]  # the fake savagewind.exe's own commands (fake_runtime.c)
# the commands modhost adds (modhost.c swgame_tables; modsettings and modset: mod settings, API 3, settings.c)
HOST_COMMANDS = ["mods", "modload", "modunload", "modreload", "modsettings", "modset"]
PROBE_SKIPPED = "help,ping,mods,hello,probe,probe_full"  # taken names, then one that no longer fits the fake table


def selftest_expect(result: dict) -> list[str]:
    """What should be true of the fake run; returns the failures (empty = passed)."""
    bad = []

    def want(cond, what):
        if not cond:
            bad.append(what)

    want(result.get("tables"), "swgame_tables returned the game's tables")
    want(result.get("same_twice"), "a second swgame_tables call returns the same tables")
    mods = result.get("mods", {})
    m = mods.get("data", {})
    files = [m.get(f"mods.{i}.file") for i in range(m.get("found", 0))]
    want(files == ["bad.dll", "hello.dll", "junk.dll", "noinit.dll", "probe.dll"], f"mods in name order ({files})")
    want(m.get("loaded") == 2, "2 mods loaded (hello, probe)")
    want(m.get("game_version") == "0.0.0-selftest", "game version from the real tables")
    want(str(mods.get("text", "")).startswith("2 of 5 native mods loaded"), "`mods` prints a summary line")
    status = {m.get(f"mods.{i}.file"): m.get(f"mods.{i}.status", "") for i in range(m.get("found", 0))}
    want(status.get("bad.dll") == "init failed (code 7)", "a failing swmod_init is reported")
    want(status.get("junk.dll", "").startswith("load failed (error"), "a file that isn't a DLL is reported")
    want(status.get("noinit.dll") == "loaded, no swmod_init", "a DLL without swmod_init is reported")
    want(m.get("mods.1.commands") == "hello" and m.get("mods.4.commands") == "probe,probe_more",
         f"each mod lists only the commands it got ({m.get('mods.1.commands')}; {m.get('mods.4.commands')})")
    want(m.get("mods.4.skipped") == PROBE_SKIPPED,
         f"taken names and a full table are listed as skipped ({m.get('mods.4.skipped')})")
    want(result.get("hello") == {"ok": True, "data": {"hello": "world", "mods_api": API_VERSION}, "text": ""},
         f"hello answers {{hello: world, mods_api: {API_VERSION}}} ({result.get('hello')})")
    probe = result.get("probe", {})
    p = probe.get("data", {})
    want(p.get("registered") == 2, f"control_register returns only what was added ({p.get('registered')})")
    want(p.get("site") == 0x7000123450 and p.get("no_site") == 0, "find_site")
    want(p.get("found_fn") and p.get("fn_x0") == 42, "dispatch_lookup finds and calls a function")
    want(p.get("between_fns") and p.get("outside"), "dispatch_lookup returns NULL off a function entry")
    want(p.get("mod") == "probe" and p.get("game") == "0.0.0-selftest", "mod_name and game_version")
    want(bool(p.get("host")) and p.get("host") == m.get("host") and p.get("size_ok") is True,
         "host_version and size")
    want(p.get("half") == 0.5 and p.get("dev") is True and p.get("cpu_null") is True, "reply_num, dev_mode, current_cpu")
    want(p.get("ok_smaller_host") is True and p.get("ok_older_version") is False,
         "SWMOD_API_OK checks the version only")
    want(p.get("has_field") is True and p.get("has_missing_field") is False, "SWMOD_HAS checks the host's size")
    want(probe.get("text") == "probe ok: 42 lines\nsecond line", f"reply_text formats its lines ({probe.get('text')!r})")
    want(result.get("probe fail") == {"ok": False, "error": "failing on purpose: fail"}, "reply_fail")
    o = result.get("#overrides", {}).get("data", {})
    want(o.get("count") == 1 and o.get("0.addr") == 0x7000001000 and o.get("0.name") == "probe_override",
         "hle_override is passed on")
    g = result.get("#guest_calls", {}).get("data", {})
    want(g.get("count") == 1 and g.get("0.addr") == 0x7000003000 and g.get("0.cpu") == p.get("cpu_ptr"),
         f"guest_call is passed on with the mod's Cpu, and dropped without one ({g})")
    cmds = sorted(result.get("#commands", {}).get("data", {}).values())
    want(cmds == sorted([*FAKE_BUILTINS, *HOST_COMMANDS, "hello", "probe", "probe_more"]),
         f"registered commands ({cmds}; the broken entry, taken names and the overflow skipped)")
    return bad


SELFTEST_LOG = (
    f"[mod] hello: ready (game 0.0.0-selftest, mod API {API_VERSION})",
    "[mod] probe: failing on purpose",
    "[modhost] mod probe.dll: a command without a name or function was skipped",
    "[modhost] mod probe.dll: command help skipped: the name is taken",
    "[modhost] mod probe.dll: command mods skipped: the name is taken",
    "[modhost] mod probe.dll: command probe_full skipped: Savage Wind didn't add it (its command table may be full)",
    "[mod] probe: ready, 2 command(s)",
    f"[modhost] mod probe.dll: ok, commands: probe,probe_more, skipped: {PROBE_SKIPPED}",
)


def selftest_expect_log(log: str) -> list[str]:
    """The log lines the main run must have (api->log goes through, skipped commands are named)."""
    lines = set(log.splitlines())
    return [f"log line: {want}" for want in SELFTEST_LOG if want not in lines]


def selftest_expect_fallback(missing: dict, missing_log: str, other: dict, other_log: str) -> list[str]:
    """No real library: NULL and no mods (Savage Wind then uses extracted/ itself). Another version's library:
    its tables unchanged (Savage Wind refuses them as usual) and no mods."""
    bad = []

    def only_builtins(r):
        return sorted(r.get("#commands", {}).get("data", {"x": "x"}).values()) == FAKE_BUILTINS

    if missing.get("tables") is not False or not only_builtins(missing):
        bad.append("with no real game code: returns NULL and loads no mods")
    if "can't load the game code" not in missing_log:
        bad.append("with no real game code: says so in the log")
    if other.get("tables") is not True or not only_builtins(other):
        bad.append("with game code for another version: returns its tables and loads no mods")
    if "another Savage Wind version" not in other_log:
        bad.append("with game code for another version: says so in the log")
    return bad


HOOK_LINES = ("mods", "hookprobe", "stamina_hook")
HOOK_LOG = (
    "[modhost] game code has 4 hook slots",
    "[modhost] mod hook_probe.dll: hook 0x7000002020: mod hook_probe.dll already replaces it",
    "[modhost] mod hook_probe.dll: no hook slot for 0x7000001020 in this game code; add it to the hooks list and "
    "rebuild",
    "[modhost] mod hook_probe.dll: hook 0x7000001000: no before, after or replace given",
    "[modhost] mod hook_probe.dll: ok, commands: hookprobe, hooks: 0x7000001000,0x7000002020",
    "[mod] stamina_hook: ready",
    "[modhost] mod stamina_hook.dll: ok, commands: stamina_hook, hooks: 0x7000cc7410",
    "[modhost] mod ?: hook 0x7000002030 refused: hooks can only be added from swmod_init",
)
NO_SLOTS_LINE = "[modhost] mod hook_probe.dll: this game code has no hook slots; rebuild with hooks (docs/hooks.md)"


def selftest_expect_hooks(hooked: dict, hooked_log: str, plain: dict, plain_log: str) -> list[str]:
    """The hook run (fake game with slots from gen_hooks: hook_probe and stamina_hook hook it) and the same mods on the
    plain fake game (no slots). Returns the failures."""
    bad = []

    def want(cond, what):
        if not cond:
            bad.append(what)

    m = hooked.get("mods", {}).get("data", {})
    want(m.get("hook_slots") == 4 and m.get("hooked") == 3, f"4 hook slots, 3 of them hooked ({m.get('hook_slots')}, "
                                                            f"{m.get('hooked')})")
    want([m.get("mods.0.file"), m.get("mods.1.file")] == ["hook_probe.dll", "stamina_hook.dll"]
         and m.get("loaded") == 2, "hook_probe and stamina_hook load")
    want(m.get("mods.0.hooks") == "0x7000001000,0x7000002020" and m.get("mods.1.hooks") == "0x7000cc7410",
         f"`mods` lists each mod's hooks ({m.get('mods.0.hooks')}; {m.get('mods.1.hooks')})")
    p = hooked.get("hookprobe", {}).get("data", {})
    codes = {k: p.get(k) for k in ("rc_add", "rc_times3", "rc_taken", "rc_no_slot", "rc_bad_args", "rc_late")}
    want(codes == {"rc_add": 0, "rc_times3": 0, "rc_taken": 3, "rc_no_slot": 2, "rc_bad_args": 5, "rc_late": 4},
         f"hook's return codes: ok, ok, taken, no slot, bad args, late ({codes})")
    want(p.get("api") == API_VERSION and p.get("can_hook_slot") is True and p.get("can_hook_no_slot") is False,
         "api_version and can_hook")
    want(p.get("add") == 103, f"a before hook changes an argument (1 + 2 -> 103: {p.get('add')})")
    want(p.get("add_skipped") == 7, f"a before hook skips the function with its own result ({p.get('add_skipped')})")
    want(p.get("direct_call") == 206, f"a direct call from the same file reaches the hook ({p.get('direct_call')})")
    want(p.get("tail_jump") == 104, f"a tail jump reaches the hook ({p.get('tail_jump')})")
    want(p.get("times3") == 19, f"replace runs the function twice, then after adds 1 (2 -> 19: {p.get('times3')})")
    want(p.get("five") == 5 and p.get("no_slot_fn") == 77, "unhooked functions run as before")
    want(p.get("setter_down") == 100.0 and p.get("setter_up") == 150.0,
         f"stamina_hook keeps the value from going down ({p.get('setter_down')}, {p.get('setter_up')})")
    want((p.get("before_runs"), p.get("after_runs"), p.get("replace_runs")) == (4, 1, 1),
         "each hook ran as often as its function")
    want(p.get("regs_layout") is True, "SWMOD_REGS matches the Cpu layout")
    s = hooked.get("stamina_hook", {})
    want(s.get("data") == {"on": True, "calls": 2, "kept": 1}, f"`stamina_hook` counts ({s.get('data')})")
    lines = set(hooked_log.splitlines())
    bad += [f"hook log line: {w}" for w in HOOK_LOG if w not in lines]

    q = plain.get("hookprobe", {}).get("data", {})
    pc = {k: q.get(k) for k in ("rc_add", "rc_times3", "rc_no_slot", "rc_bad_args", "rc_late")}
    want(pc == {"rc_add": 1, "rc_times3": 1, "rc_no_slot": 1, "rc_bad_args": 5, "rc_late": 4},
         f"without hook slots, hook says so ({pc})")
    want(q.get("add") == 41 and q.get("can_hook_slot") is False, "without hook slots, functions run unhooked")
    want(plain_log.splitlines().count(NO_SLOTS_LINE) == 1, "without hook slots: one clear log line per mod")
    pm = plain.get("mods", {}).get("data", {})
    want(pm.get("hook_slots") == 0 and pm.get("mods.1.status") == "init failed (code 2)",
         f"without hook slots: no slots listed; stamina_hook refuses a game that isn't 1.9.0 ({pm.get('mods.1.status')})")
    return bad


# The hot-load run (modload / modreload / modunload) on the fake hooked game, with hook_probe loaded at game start:
# hot_probe.c's v1 is loaded and used, held busy (a thread of its own waits inside its hook, so modunload must refuse),
# replaced by the changed build v2, joined by a build whose swmod_init fails, and unloaded. Then v1 again on the fake
# game without hook slots. The copies are named like the editor's (<name>-<number>.dll).
HOT_ADDR = "0x7000001020"  # the made-up function hot_probe overrides (v2 also asks to hook it: it has no slot)
HOT_ENV = {"FAKE_MAX_CMDS": "16", "SWMOD_UNLOAD_WAIT_MS": "300"}
HOT_FILES = ("hotmod-1.dll", "hotmod-2.dll", "hotfail-1.dll")  # SELFTEST_HOT's v1, v2 and fail, as the editor names them
HOT_LOG = (
    "[mod] hot: ready (v1)",
    "[modhost] modunload hotmod (hotmod-1.dll): refused: a game thread still runs one of its hooks or overrides",
    "[mod] hot: exit (v1)",
    "[modhost] modunload hotmod (hotmod-1.dll): unloaded",
    "[mod] hot: ready (v2)",
    "[mod] hot: ready (v9)",
    "[modhost] modload hotfail: swmod_init failed (code 3); unloaded again",
    "[mod] hot: exit (v2)",
    "[modhost] modunload hotmod (hotmod-2.dll): unloaded",
)


def control_word(text) -> str:
    """One word of a control line, quoted (a path may have spaces): \\ and \" escaped, as tools/control.py does."""
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def hot_steps(run_dir: Path) -> list[tuple[str, str]]:
    """The hot-load run's steps: (name, control line), in order."""
    v1, v2, fail, junk = (control_word(run_dir / n) for n in (*HOT_FILES, "junk-1.dll"))
    return [
        ("mods_start", "mods"), ("load", f"modload {v1}"), ("hot_v1", "hot"), ("call_v1", f"#call {HOT_ADDR}"),
        ("load_again", f"modload {v1}"), ("block", "hot block"), ("unload_busy", "modunload hotmod"),
        ("hot_after_busy", "hot"), ("call_after_busy", f"#call {HOT_ADDR}"), ("release", "hot release"),
        ("reload", f"modreload hotmod {v2}"), ("hot_v2", "hot"), ("hot2", "hot2"), ("call_v2", f"#call {HOT_ADDR}"),
        ("reload_missing", f"modreload hotmod {control_word(run_dir / 'nosuch-9.dll')}"),
        ("reload_junk", f"modreload hotmod {junk}"), ("hot_after_bad_reload", "hot"),
        ("load_unquoted", "modload C:\\no such folder\\nosuch-1.dll"),
        ("load_fail", f"modload {fail}"), ("hotfail", "hotfail"), ("hot_after_fail", "hot"),
        ("unload_start_mod", "modunload hook_probe"), ("load_junk", f"modload {junk}"), ("mods_hot", "mods"),
        ("unload", "modunload hotmod"), ("hot_gone", "hot"), ("call_gone", f"#call {HOT_ADDR}"),
        ("hookprobe", "hookprobe"), ("overrides", "#overrides"), ("mods_end", "mods"),
        ("unload_none", "modunload nosuch"),
    ]


def hot_plain_steps(run_dir: Path) -> list[tuple[str, str]]:
    """modload on the fake game without hook slots: the reply must say that the hooks need a rebuild."""
    return [("load", f"modload {control_word(run_dir / HOT_FILES[0])}"), ("hot", "hot"), ("unload", "modunload hotmod")]


def selftest_expect_hot(steps: dict, log: str, plain: dict) -> list[str]:
    """The hot-load runs: steps and plain map each step's name (hot_steps, hot_plain_steps) to its reply. Returns
    the failures."""
    bad = []

    def want(cond, what):
        if not cond:
            bad.append(what)

    def data(k, where=steps):
        return where.get(k, {}).get("data", {})

    def ok(k, where=steps):
        return where.get(k, {}).get("ok") is True

    def error(k):
        return str(steps.get(k, {}).get("error", "")) if steps.get(k, {}).get("ok") is False else ""

    m = data("mods_start")
    want(m.get("found") == 1 and m.get("hot") == 0 and m.get("mods.0.name") == "hook_probe"
         and m.get("mods.0.hot") is False, f"at the start only hook_probe is loaded ({m})")
    ld = data("load")
    want(ok("load") and (ld.get("name"), ld.get("id"), ld.get("replaced")) == ("hotmod", 2, False),
         f"modload loads hotmod-1.dll as hotmod, id 2 ({steps.get('load')})")
    want((ld.get("commands"), ld.get("hooks"), ld.get("overrides"), ld.get("no_slot"))
         == ("hot", "0x7000002030,0x7000001000,0x7000cc7410", HOT_ADDR, ""), f"modload lists what the mod added ({ld})")
    h1 = data("hot_v1")
    want((h1.get("version"), h1.get("mod"), h1.get("add"), h1.get("five")) == (1, "hotmod", 1103, 105),
         f"its command runs, and its hooks run after hook_probe's ({h1})")
    want(data("call_v1").get("x0") == 1001, f"its hle_override runs ({steps.get('call_v1')})")
    want("already loaded" in error("load_again") and "modreload" in error("load_again"),
         f"loading it a second time is refused ({steps.get('load_again')})")
    want(data("block").get("blocked") is True, "a thread of the mod waits inside its hook")
    want("still in use" in error("unload_busy") and "restart the game to replace this mod" in error("unload_busy"),
         f"modunload refuses while a thread is inside the mod's hook ({steps.get('unload_busy')})")
    hb = data("hot_after_busy")
    want((hb.get("version"), hb.get("add"), hb.get("five")) == (1, 1103, 105)
         and data("call_after_busy").get("x0") == 1001, f"after the refusal the mod works as before ({hb})")
    want(data("release").get("released") is True, "the waiting thread finishes")
    rl = data("reload")
    want(ok("reload") and (rl.get("name"), rl.get("id"), rl.get("replaced"), rl.get("commands"))
         == ("hotmod", 3, True, "hot,hot2"), f"modreload puts the changed build in its place ({steps.get('reload')})")
    want(rl.get("no_slot") == HOT_ADDR and "needs a rebuild" in str(rl.get("notes")),
         f"modreload says which function has no hook slot ({rl.get('notes')!r})")
    h2 = data("hot_v2")
    want((h2.get("version"), h2.get("mod"), h2.get("add"), h2.get("five")) == (2, "hotmod", 103, 205),
         f"the new build runs, and the old build's hooks are gone ({h2})")
    want(data("hot2").get("version") == 2, "a command only the new build has works")
    want(data("call_v2").get("x0") == 2002, "the override calls the new build")
    for k, why in (("reload_missing", "there is no file"), ("reload_junk", "error 193")):
        want("was not replaced, the old build still runs" in error(k) and why in error(k),
             f"modreload checks the new file before it unloads the old build ({steps.get(k)})")
    want(data("hot_after_bad_reload").get("version") == 2, "after a refused modreload the old build still works")
    want("double quotes" in error("load_unquoted"), f"a path that wasn't quoted gets a hint ({steps.get('load_unquoted')})")
    ef = error("load_fail")
    want("(code 3)" in ef and "unloaded again" in ef and "overrides it already" in ef,
         f"a mod whose swmod_init fails is unloaded again, and the reply says why ({ef!r})")
    want("not loaded any more" in error("hotfail"), "the failed mod's command says it is gone")
    want(data("hot_after_fail").get("five") == 205, "the failed mod's hook is gone")
    want("restart the game to replace this mod" in error("unload_start_mod"), "a mod loaded at game start stays")
    want("error 193" in error("load_junk"), f"a file that isn't a DLL is refused ({error('load_junk')!r})")
    mh = data("mods_hot")
    want((mh.get("found"), mh.get("hot"), mh.get("mods.1.name"), mh.get("mods.1.hot"), mh.get("mods.1.id"),
          mh.get("mods.1.overrides")) == (2, 1, "hotmod", True, 3, HOT_ADDR),
         f"`mods` lists the mod loaded while the game runs ({mh})")
    un = data("unload")
    want(ok("unload") and un.get("unloaded") is True and un.get("name") == "hotmod",
         f"modunload unloads it ({steps.get('unload')})")
    want("not loaded any more" in error("hot_gone"), "its command says it is gone")
    want(data("call_gone").get("x0") == 77, "the override runs the game's function again")
    hp = data("hookprobe")
    want((hp.get("add"), hp.get("five"), hp.get("times3")) == (103, 5, 19),
         f"hook_probe's hooks still work, and the unloaded mod's don't run ({hp})")
    want(data("overrides").get("count") == 1, "Savage Wind got one override for the address, not one per load")
    me = data("mods_end")
    want((me.get("found"), me.get("hot"), me.get("hooked")) == (1, 0, 2),
         f"in the end only hook_probe is loaded, with its 2 hooked functions ({me})")
    want("no mod named nosuch" in error("unload_none"), "unloading a name that isn't loaded says so")
    lines = log.splitlines()
    bad += [f"hot log line: {w}" for w in HOT_LOG if w not in lines]
    want("[mod] hot: exit (v9)" not in lines, "swmod_exit isn't called when swmod_init failed")

    pl = data("load", plain)
    want(ok("load", plain) and pl.get("no_slot") == "0x7000002030,0x7000001000,0x7000cc7410"
         and "no hook slots" in str(pl.get("notes")) and "rebuild" in str(pl.get("notes")),
         f"without hook slots, modload says the hooks need a rebuild ({plain.get('load')})")
    want(data("hot", plain).get("add") == 41 and ok("unload", plain), "without hook slots the mod runs unhooked")
    return bad


# The race run (modhost_test.dll on the fake hooked game, race_probe loaded at game start): game threads in the
# moments where an unload could free code they are about to run, and mods that try to add things from a hook. A run
# that goes wrong ends in an access violation (the run crashed), or in the checks below.
RACE_ENV = {"FAKE_MAX_CMDS": "16", "SWMOD_UNLOAD_WAIT_MS": "1500"}
RACE_FILES = ("hotmod-1.dll", "hotother-1.dll", "misusetail-1.dll", "misuserearm-1.dll")  # v1, v1, tail, rearm
RACE_HOOK_MS = 750  # modload of a mod that hooks a function a thread is inside: well under the 1500 ms wait
FN_FIVE, FN_ADD, FN_SETTER = "0x7000002030", "0x7000001000", "0x7000cc7410"  # made up (selftest/hooked)
MISUSE_REFUSED = ("hle_override 0x7000001000 refused: a mod loaded while the game runs can only add it from "
                  "swmod_init or its own command (not from a hook, a thread of its own or swmod_exit)")
RACE_LOG = (
    "[mod] race: ready",
    f"[modhost] mod misusetail-1.dll: {MISUSE_REFUSED}",
    f"[modhost] mod misuserearm-1.dll: {MISUSE_REFUSED}",
    "[modhost] modunload misuserearm (misuserearm-1.dll): unloaded",
)


def race_steps(run_dir: Path) -> list[tuple[str, str]]:
    """The race run's steps: (name, control line or !pause / !sleep), in order."""
    v1, other, tail, rearm = (control_word(run_dir / n) for n in RACE_FILES)
    return [
        # a thread waits inside hotmod's hook while another mod hooks the same function: modload doesn't wait for it
        ("load", f"modload {v1}"), ("block", "hot block"), ("load_other", f"modload {other}"),
        ("release", "hot release"), ("unload_other", "modunload hotother"),
        # a thread saw hotmod's hooks, then stops before the gate; hotmod is unloaded meanwhile
        ("pause", "!pause 400"), ("race", f"race {FN_FIVE}"), ("sleep", "!sleep 100"), ("unpause", "!pause 0"),
        ("unload", "modunload hotmod"), ("race_wait", "race wait"),
        # a hook that adds an override as its last call (a tail call), from a game thread
        ("load_tail", f"modload {tail}"), ("race_tail", f"race {FN_FIVE}"), ("race_tail_wait", "race wait"),
        ("mods_tail", "mods"), ("unload_tail", "modunload misusetail"), ("call_after_tail", f"#call {FN_ADD}"),
        # a hook that puts its override back while modunload waits for it
        ("load_rearm", f"modload {rearm}"), ("call_rearm", f"#call {FN_ADD}"), ("race_rearm", f"race {FN_SETTER}"),
        ("sleep_rearm", "!sleep 40"), ("unload_rearm", "modunload misuserearm"), ("race_rearm_wait", "race wait"),
        ("call_after_rearm", f"#call {FN_ADD} 2 3"), ("overrides", "#overrides"),
    ]


def selftest_expect_race(steps: dict, ms: dict, log: str) -> list[str]:
    """The race run: steps and ms map each step's name (race_steps) to its reply and how long it took. Returns the
    failures."""
    bad = []

    def want(cond, what):
        if not cond:
            bad.append(what)

    def data(k):
        return steps.get(k, {}).get("data", {})

    def ok(k):
        return steps.get(k, {}).get("ok") is True

    def error(k):
        return str(steps.get(k, {}).get("error", "")) if steps.get(k, {}).get("ok") is False else ""

    want(ok("load") and data("block").get("blocked") is True, f"race run: hotmod loads and holds a thread in its hook "
                                                              f"({steps.get('load')}, {steps.get('block')})")
    want(ok("load_other") and data("load_other").get("hooks") == f"{FN_FIVE},{FN_ADD},{FN_SETTER}",
         f"a second mod hooks the same functions ({steps.get('load_other')})")
    want(ms.get("load_other", 1e9) < RACE_HOOK_MS, f"modload doesn't wait for a game thread inside a function it hooks "
                                                   f"({ms.get('load_other')} ms; the wait is 1500 ms)")
    want(data("release").get("released") is True and ok("unload_other"), "the other mod unloads once the thread left")
    rw = data("race_wait")
    want(ok("unload") and rw.get("done") is True and rw.get("x0") == 5,
         f"a thread that saw the mod's hooks just before modunload runs the game's function, not the freed mod ({rw})")
    want(data("race_tail_wait").get("x0") == 6, f"the misusing mod's hook ran ({steps.get('race_tail_wait')})")
    mt = data("mods_tail")
    i = next((k.split(".")[1] for k, v in mt.items() if k.endswith(".name") and v == "misusetail"), None)
    want(i is not None and mt.get(f"mods.{i}.overrides") == "", f"its hle_override from a hook isn't listed ({mt})")
    want(ok("unload_tail") and "no override" in error("call_after_tail"),
         f"an hle_override from a hook (a tail call) was refused, so nothing of the freed mod is left "
         f"({steps.get('call_after_tail')})")
    want(data("call_rearm").get("x0") == 4242, f"the override from swmod_init runs ({steps.get('call_rearm')})")
    want(ok("unload_rearm") and data("race_rearm_wait").get("done") is True,
         f"modunload waits for the hook, then unloads ({steps.get('unload_rearm')})")
    want(data("call_after_rearm").get("x0") == 5,
         f"after modunload the address runs the game's function, though the hook tried to put the override back "
         f"({steps.get('call_after_rearm')})")
    o = data("overrides")
    names = sorted(v for k, v in o.items() if k.endswith(".name"))
    want(names == ["hot_override", "misuse_rearm"], f"Savage Wind got only modhost's stand-ins ({names})")
    lines = log.splitlines()
    bad += [f"race log line: {w}" for w in RACE_LOG if w not in lines]
    return bad


# The file log runs (fslog.c): modhost with SWMOD_FILE_LOG=1 on the fake game, which has made-up functions at BotW
# 1.9.0's four file-open addresses (botw.h BOTW_ADDR_FILE_OPEN...), and no mods. "#open" calls a wrapper as the game's
# file devices would (fake_runtime.c). Run A tries every kind of line, run B the lines-a-second limit, run C the size
# limit; each run keeps the one before as fslog.prev.log.
FSLOG_FNS = {"e8fae0": 0x7000e8fae0, "e92690": 0x7000e92690, "15cba0": 0x700115cba0, "eaba98": 0x7000eaba98}
FSLOG_ARMOR = "Model/Armor_001.sbfres"
FSLOG_REPEATS = 20
FSLOG_LONG = "Pack/" + "L" * 300
FSLOG_ENV = {"SWMOD_FILE_LOG": "1"}
FSLOG_RATE_ENV = {"SWMOD_FILE_LOG": "on", "SWMOD_FILE_LOG_RATE": "5"}
FSLOG_CAP_ENV = {"SWMOD_FILE_LOG": "1", "SWMOD_FILE_LOG_MAX": "4096"}
FSLOG_BURST, FSLOG_FILL = 12, 40
FSLOG_HEADER = "# time\tthread\tfn\tmode\tresult\tms\tdevice\tpath\tnote"
# Paths in memory Windows says can't be read, as a bad pointer into the guest window would be (review 2026-11-21: 0x10
# and 0x20 never reach VirtualQuery): fake_runtime.c's pages. Each its own mode, so each gets its own line.
FSLOG_UNREADABLE = (("uncommitted", 4), ("noaccess", 5), ("guard", 6), ("noaccessobj", 7), ("halfobj", 8))
FSLOG_EDGE = "Model/Edge.sbfres"  # (its last byte the last one before a page that isn't committed: cut with "...")


def fslog_steps() -> list[tuple[str, str]]:
    """Run A's steps: (name, control line or !sleep), in order."""
    e8, e9, c15, ea = (f"{a:#x}" for a in FSLOG_FNS.values())
    return [
        ("status0", "fslog"), ("overrides", "#overrides"), ("ok", f"#open {e8} dev 0 {FSLOG_ARMOR}"),
        *[(f"again{i}", f"#open {e8} dev 0 {FSLOG_ARMOR}") for i in range(FSLOG_REPEATS)],
        ("fail", f"#open {e9} dev 1 missing/Model/Nope.sbfres"), ("nopath", f"#open {c15} dev 0 -"),
        ("badstr", f"#open {ea} dev 0 !bad"), ("badobj", f"#open {ea} 0 3 !badobj"),
        *[(k, f"#open {ea} dev {mode} !{k}") for k, mode in FSLOG_UNREADABLE],
        ("edge", f"#open {e8} dev 0 !edge:{FSLOG_EDGE}"),
        ("long", f"#open {e8} dev 0 {FSLOG_LONG}"), ("tab", f'#open {e8} dev 2 "Pack/A\tB.pack"'),
        ("off", "fslog off"), ("paused_open", f"#open {e8} dev 0 Paused/NotWritten.bin"), ("off_status", "fslog"),
        ("on", "fslog on"), ("mark", "fslog mark fast travel now"), ("wait", "!sleep 1100"), ("status1", "fslog"),
        ("bad_arg", "fslog sideways"),
    ]


def fslog_rate_steps() -> list[tuple[str, str]]:
    """Run B: more different opens at once than the 5 lines a second it allows."""
    e8 = f"{FSLOG_FNS['e8fae0']:#x}"
    return [*[(f"burst{i}", f"#open {e8} dev 0 Burst/File{i:02d}.bin") for i in range(FSLOG_BURST)],
            ("wait", "!sleep 1100"), ("status", "fslog")]


def fslog_cap_steps() -> list[tuple[str, str]]:
    """Run C: long lines until the 4096-byte limit stops the log; the opens still work after that."""
    e8 = f"{FSLOG_FNS['e8fae0']:#x}"
    return [*[(f"fill{i}", f"#open {e8} dev 0 Cap/{'c' * 150}{i:02d}.bin") for i in range(FSLOG_FILL)],
            ("status", "fslog"), ("on_full", "fslog on"), ("after", f"#open {e8} dev 0 Cap/after.bin")]


def fslog_rows(text: str) -> list[list[str]]:
    """The open lines of a file log (not the # notes), split on tabs."""
    return [ln.split("\t") for ln in text.splitlines() if ln and not ln.startswith("#")]


def fslog_runs(out: Path) -> tuple:
    """Run A, B and C (each in a new fake game) and read the fslog.log each wrote. Returns (a, a_text, a_log, b,
    b_text, b_prev, c, c_text) for selftest_expect_fslog."""
    st = out / "selftest"
    mods = st / "fslog-mods"  # (no mods)
    mods.mkdir(parents=True, exist_ok=True)
    file, prev = st / "fslog.log", st / "fslog.prev.log"
    for f in (file, prev):
        f.unlink(missing_ok=True)
    got = []
    for steps, env in ((fslog_steps(), FSLOG_ENV), (fslog_rate_steps(), FSLOG_RATE_ENV),
                       (fslog_cap_steps(), FSLOG_CAP_ENV)):
        res, log = _run_fake_game(out, st / "fake_swgame.dll", mods, [ln for _, ln in steps], env)
        text = file.read_text(encoding="utf-8", errors="replace") if file.is_file() else ""
        before = prev.read_text(encoding="utf-8", errors="replace") if prev.is_file() else ""
        got.append((dict(zip((k for k, _ in steps), res.get("steps", []))), text, log, before))
    (a, a_text, a_log, _), (b, b_text, _, b_prev), (c, c_text, _, _) = got
    return a, a_text, a_log, b, b_text, b_prev, c, c_text


def selftest_expect_fslog(a: dict, a_text: str, a_log: str, b: dict, b_text: str, b_prev: str, c: dict,
                          c_text: str) -> list[str]:
    """The file log runs: a, b and c map each step's name (fslog_steps, fslog_rate_steps, fslog_cap_steps) to its
    reply; *_text is the fslog.log each run wrote, b_prev the fslog.prev.log after run B. Returns the failures."""
    bad = []

    def want(cond, what):
        if not cond:
            bad.append(what)

    def data(k, where=a):
        return where.get(k, {}).get("data", {})

    def x0(k, where=a):
        return data(k, where).get("x0")

    s0 = data("status0")
    want((s0.get("on"), s0.get("paused"), s0.get("full"), s0.get("wrapped")) == (True, False, False, 4)
         and str(s0.get("file", "")).endswith("fslog.log"), f"fslog: the log is on, with 4 opens wrapped ({s0})")
    o = data("overrides")
    addrs = {o.get(f"{i}.addr") for i in range(o.get("count", 0))}
    names = [str(o.get(f"{i}.name", "")) for i in range(o.get("count", 0))]
    want(o.get("count") == 4 and addrs == set(FSLOG_FNS.values())
         and all(n.startswith("modhost_fslog_") for n in names),
         f"modhost overrides the 4 file opens, and nothing else ({o})")
    dev = data("ok").get("dev")
    want(dev and x0("ok") == dev and data("ok").get("x1") == 1,
         f"an open goes on to the game's function ({a.get('ok')})")
    want(all(x0(f"again{i}") == dev for i in range(FSLOG_REPEATS)), "the same open again works each time")
    want(x0("fail") == 0 and data("fail").get("x1") == 2, f"a failed open answers 0 ({a.get('fail')})")
    want(x0("nopath") == 0 and data("nopath").get("x1") == 3, "no path: the game's function still runs")
    want(x0("badstr") == 0 and x0("badobj") == 0 and data("badobj").get("x1") == 4,
         "a path that can't be read: no crash, the game's function still runs")
    unread_steps = {k: a.get(k) for k, _ in FSLOG_UNREADABLE}
    want(all(x0(k) == 0 and data(k).get("x1") == 4 for k in unread_steps),
         "a path in memory that can't be read (not committed, no access, a guard page, half an object): no crash, the "
         f"game's function still runs ({unread_steps})")
    want(x0("edge") == dev and data("edge").get("x1") == 1,
         f"a path that runs into memory that can't be read: the game's function runs ({a.get('edge')})")
    want(x0("long") == dev and x0("paused_open") == dev, "a long path and an open while paused work")
    want(data("off_status").get("paused") is True, "fslog off pauses")
    want(a.get("bad_arg", {}).get("ok") is False, "fslog with a wrong word says how to use it")
    s1 = data("status1")
    opens = {k: sum(1 for _, ln in fslog_steps() if ln.startswith(f"#open {v:#x} ")) for k, v in FSLOG_FNS.items()}
    want(s1.get("paused") is False and s1.get("opens") == sum(opens.values())
         and all(s1.get(f"opens.{k}") == n for k, n in opens.items())
         and s1.get("unreadable") == 2 + len(FSLOG_UNREADABLE),
         f"fslog counts every open, each function's own, and the paths it couldn't read ({s1})")

    rows = fslog_rows(a_text)
    notes = [ln for ln in a_text.splitlines() if ln.startswith("#")]
    want(rows and all(len(r) == 9 for r in rows), "every line has 9 columns")
    want(FSLOG_HEADER in notes, "the log names its columns")
    rows = [r for r in rows if len(r) == 9]
    armor = [r for r in rows if r[7] == FSLOG_ARMOR]
    full = [r for r in armor if r[1] != "-"]
    more = [int(r[8].split()[0].lstrip("+")) for r in armor if r[1] == "-" and r[8].startswith("+")]
    want(full and len(full) + sum(more) == FSLOG_REPEATS + 1 and len(full) <= 3 and sum(more) >= FSLOG_REPEATS - 2,
         f"the same open again within a second is counted, not written each time ({len(full)} lines, more {more})")
    if full:
        r = full[0]
        want(r[2:5] == ["e8fae0", "0", "ok"] and r[6] == "1234abcd" and r[1].isdigit()
             and re.fullmatch(r"\d+\.\d{3}", r[0]) and re.fullmatch(r"\d+\.\d{3}", r[5]),
             f"an open's line: time, thread, function, mode, result, ms, device class, path ({r})")

    def one(path):
        return next((r for r in rows if r[7] == path), [])

    want(one("missing/Model/Nope.sbfres")[2:5] == ["e92690", "1", "fail"], "a failed open says fail")
    want(one("(no path)")[2:5] == ["15cba0", "0", "fail"], "no path says so")
    unread = [r for r in rows if r[7] == "(unreadable)"]
    want(sorted((r[3], r[6]) for r in unread)
         == sorted([("0", "1234abcd"), ("3", "-"), *[(str(m), "1234abcd") for _, m in FSLOG_UNREADABLE]])
         and all(r[2] == "eaba98" and r[4] == "fail" for r in unread),
         f"a path that can't be read says so, also in memory Windows says can't be read ({unread})")
    want(one(FSLOG_EDGE + "...")[2:5] == ["e8fae0", "0", "ok"],
         "a path that runs into memory that can't be read is cut there (with ...)")
    want(one(FSLOG_LONG[:255] + "...") != [], "a long path is cut at 255 bytes")
    want(one("Pack/A?B.pack")[3:5] == ["2", "ok"], "a control character in a path becomes ?")
    want("Paused/NotWritten.bin" not in a_text, "nothing is written while paused")
    for line in ("paused (fslog off)", "resumed (fslog on): 1 opens while paused were not written",
                 "mark: fast travel now", "game closed: "):
        want(any(line in n for n in notes), f"the log has the line '{line}'")
    want(any(ln.startswith("[modhost] file log: on, 4 of 4 file opens wrapped, writing ") for ln in a_log.splitlines()),
         "savagewind.log says the file log is on")

    burst = [r for r in fslog_rows(b_text) if len(r) == 9 and r[7].startswith("Burst/")]
    dropped = sum(int(m) for m in re.findall(r"^# [\d.]+ (\d+) opens not written in the second before: more than 5 "
                                             r"lines a second$", b_text, re.M))
    want(len(burst) <= 10 and dropped >= 2 and len(burst) + dropped == FSLOG_BURST
         and data("status", b).get("rate_dropped") == dropped,
         f"over 5 lines a second, the rest are counted in one line ({len(burst)} lines, {dropped} counted)")
    want(bool(a_text) and b_prev == a_text, "the last game's log is kept as fslog.prev.log")

    cs = data("status", c)
    want(cs.get("full") is True and c.get("on_full", {}).get("ok") is False
         and "full" in str(c.get("on_full", {}).get("error", "")), f"the size limit stops the log ({cs})")
    want(len(c_text.encode("utf-8")) <= 4096 and c_text.count("stopped: the log reached its size limit") == 1
         and "game closed: " in c_text, f"the log stays under its limit and says it stopped ({len(c_text)} bytes)")
    filled = [r for r in fslog_rows(c_text) if len(r) == 9 and r[7].startswith("Cap/")]
    want(0 < len(filled) < FSLOG_FILL and "Cap/after.bin" not in c_text, "after the stop nothing more is written")
    cdev = data("after", c).get("dev")
    want(cdev and x0("after", c) == cdev and x0(f"fill{FSLOG_FILL - 1}", c) == cdev,
         "opens still work after the log stopped")
    return bad


# Run D, the overlap rules (fslog.c fl_install, modhost.c hot_override): fslog_probe (loaded at game start) overrides
# e92690 first, so the file log leaves that one alone and wraps the other three; then modload of fslog_probe_hot, which
# overrides e8fae0, is refused because the file log wraps it.
FSLOG_OVER_FILE = "fslogover-1.dll"  # (fslog_probe_hot, named as the editor names a mod for modload)
FSLOG_OVER_ENV = {"SWMOD_FILE_LOG": "1", "FAKE_MAX_CMDS": "16"}
FSLOG_OVER_NOTE = "# e92690 (0x7000e92690) not wrapped: a mod overrides it already (the first override wins)"
FSLOG_OVER_REFUSED = ("hle_override 0x7000e8fae0 refused: modhost's file log wraps it (File log is on), and the first "
                      "override wins")
FSLOG_OVER_LOG = (
    "[mod] fslog probe: ready",
    "[modhost] file log: 0x7000e92690 not wrapped: a mod overrides it already (the first override wins)",
    f"[modhost] mod {FSLOG_OVER_FILE}: {FSLOG_OVER_REFUSED}",
)


def fslog_overlap_steps(run_dir: Path) -> list[tuple[str, str]]:
    """Run D's steps: (name, control line), in order."""
    e8, e9 = f"{FSLOG_FNS['e8fae0']:#x}", f"{FSLOG_FNS['e92690']:#x}"
    return [("status", "fslog"), ("overrides", "#overrides"), ("mine", f"#open {e9} dev 0 Model/Mine.sbfres"),
            ("load", f"modload {control_word(run_dir / FSLOG_OVER_FILE)}"), ("overrides_after", "#overrides"),
            ("after", f"#open {e8} dev 0 Model/After.sbfres"), ("status_after", "fslog")]


def fslog_overlap_run(out: Path) -> tuple[dict, str, str]:
    """Run D in a new fake game: (its replies by step name, the fslog.log it wrote, its savagewind.log)."""
    st = out / "selftest"
    run_dir = st / "fslog-hot-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SELFTEST_FSLOG[1].output(out), run_dir / FSLOG_OVER_FILE)
    file = st / "fslog.log"
    file.unlink(missing_ok=True)
    steps = fslog_overlap_steps(run_dir)
    res, log = _run_fake_game(out, st / "fake_swgame.dll", SELFTEST_FSLOG[0].output(out).parent,
                              [ln for _, ln in steps], FSLOG_OVER_ENV)
    text = file.read_text(encoding="utf-8", errors="replace") if file.is_file() else ""
    return dict(zip((k for k, _ in steps), res.get("steps", []))), text, log


def selftest_expect_fslog_overlap(d: dict, d_text: str, d_log: str) -> list[str]:
    """Run D: d maps each step's name (fslog_overlap_steps) to its reply, d_text is its fslog.log and d_log its
    savagewind.log. Returns the failures."""
    bad = []

    def want(cond, what):
        if not cond:
            bad.append(what)

    def data(k):
        return d.get(k, {}).get("data", {})

    def names(k):
        o = data(k)
        return sorted(str(o.get(f"{i}.name", "")) for i in range(o.get("count", 0)))

    start_names = sorted(["fslog_probe_start", "modhost_fslog_e8fae0", "modhost_fslog_15cba0",
                          "modhost_fslog_eaba98"])
    s = data("status")
    want(s.get("wrapped") == 3, f"overlap run: a mod at game start overrides e92690 first, so 3 opens are wrapped ({s})")
    want(names("overrides") == start_names,
         f"overlap run: Savage Wind got the mod's override and the file log's other 3 ({names('overrides')})")
    want(data("mine").get("x0") == 0x7777 and data("mine").get("x1") == 9,
         f"overlap run: the mod's own function runs for e92690 (the first override wins) ({d.get('mine')})")
    want(FSLOG_OVER_NOTE in d_text.splitlines(), "overlap run: fslog.log says why e92690 isn't wrapped")
    want("Model/Mine.sbfres" not in d_text, "overlap run: an open the file log doesn't wrap isn't written")
    ld = d.get("load", {})
    want(ld.get("ok") is True and FSLOG_OVER_REFUSED in str(data("load").get("notes", ""))
         and FSLOG_OVER_REFUSED in str(ld.get("text", "")),
         f"overlap run: modload of a mod that overrides e8fae0 is refused that address, with the reason ({ld})")
    want(names("overrides_after") == start_names, f"overlap run: the refused override never reached Savage Wind "
                                                  f"({names('overrides_after')})")
    dev = data("after").get("dev")
    want(dev and data("after").get("x0") == dev and data("after").get("x1") == 1,
         f"overlap run: e8fae0 still runs the game's function ({d.get('after')})")
    want(any(r[2:5] == ["e8fae0", "0", "ok"] and r[7:8] == ["Model/After.sbfres"] for r in fslog_rows(d_text)),
         "overlap run: the file log still writes e8fae0's opens")
    sa = data("status_after")
    want(sa.get("opens.e92690") == 0 and sa.get("opens.e8fae0") == 1,
         f"overlap run: the file log counts only what it wraps ({sa})")
    lines = d_log.splitlines()
    bad += [f"overlap run log line: {w}" for w in FSLOG_OVER_LOG if w not in lines]
    want(any(ln.startswith("[modhost] file log: on, 3 of 4 file opens wrapped, writing ") for ln in lines),
         "overlap run: savagewind.log says 3 of 4 are wrapped")
    return bad


# Run E, chained overrides (modhost.c start_overrides_flush, api_dispatch_lookup): chain1 and chain3 wrap 0x7000001000
# (the fake game's x0 = 41), each adding its tag (x0 * 10 + tag); chain2 wraps it too but fails its swmod_init.
CHAIN_FN, CHAIN_FN_B = "0x7000001000", "0x7000002000"
CHAIN_LOG = (
    "[mod] chain probe 1: ready",
    "[mod] chain probe 2: failing on purpose",
    "[mod] chain probe 3: ready",
    "[modhost] override chain 0x7000001000: chain3 -> chain1 -> game (the mod loaded last runs first)",
    "[modhost] mod chain1.dll: hle_override 0x7000001000 refused: another mod overrides it already, and overrides "
    "only chain while the mods start (swmod_init)",
)
CHAIN_STEPS = (("overrides", "#overrides"), ("call", f"#call {CHAIN_FN}"), ("call_b", f"#call {CHAIN_FN_B}"),
               ("who", "chainwho"), ("late", "chainlate"), ("overrides_after", "#overrides"),
               ("call_after", f"#call {CHAIN_FN}"))


def chain_run(out: Path) -> tuple[dict, str]:
    """Run E in a new fake game: (its replies by step name, its savagewind.log)."""
    res, log = _run_fake_game(out, out / "selftest" / "fake_swgame.dll", SELFTEST_CHAIN[0].output(out).parent,
                              [ln for _, ln in CHAIN_STEPS])
    return dict(zip((k for k, _ in CHAIN_STEPS), res.get("steps", []))), log


def selftest_expect_chain(e: dict, log: str) -> list[str]:
    """Run E: e maps each step's name (CHAIN_STEPS) to its reply, log is its savagewind.log. Returns the failures."""
    bad = []

    def want(cond, what):
        if not cond:
            bad.append(what)

    def data(k):
        return e.get(k, {}).get("data", {})

    def rows(k):
        o = data(k)
        return [(o.get(f"{i}.addr"), o.get(f"{i}.name")) for i in range(o.get("count", 0))]

    a, b = int(CHAIN_FN, 16), int(CHAIN_FN_B, 16)
    want(rows("overrides") == [(a, "chain_probe"), (b, "chain_probe_b")],
         f"chain run: Savage Wind got one override per address, the chain's top ({rows('overrides')})")
    want(data("call").get("x0") == 4113, f"chain run: one call runs the game's function, then chain1, then chain3, "
                                         f"each once, and never the failed chain2 (x0 {data('call').get('x0')}, "
                                         f"want 4113)")
    want(data("call_b").get("x0") == 142, f"chain run: a chain of one still wraps the game's function ({e.get('call_b')})")
    w = data("who")
    want(w.get("own") is False and w.get("same") is True,
         f"chain run: dispatch_lookup never gives a mod its own override, before or after it is added ({w})")
    want(data("late").get("low") is True, "chain run: the bottom link's dispatch_lookup once the game runs gives the "
                                          "game's function, not the chain's top (that would loop)")
    want(e.get("late", {}).get("ok") is True and rows("overrides_after") == rows("overrides"),
         f"chain run: an override added once the game runs is refused and never reaches Savage Wind "
         f"({rows('overrides_after')})")
    want(data("call_after").get("x0") == 4113, f"chain run: the chain is unchanged after that ({e.get('call_after')})")
    lines = log.splitlines()
    bad += [f"chain run log line: {ln}" for ln in CHAIN_LOG if ln not in lines]
    want(not any("override chain 0x7000002000" in ln for ln in lines), "chain run: a chain of one gets no chain line")
    return bad


# The file redirect runs (fslog.c, API 4; linkle-live-swap-design.md 2.1): fsredir_probe (loaded at game start) names
# redirects in two groups. R1: SWMOD_FILE_REDIRECT and the file log on; R2: the redirect alone (no fslog.log); R3:
# neither (every call answers OFF, nothing is wrapped). The fake game reports the path it opened ("opened").
FSR_ARMOR, FSR_COPY = "Model/Armor_001.sbfres", "Model/SwLk_Armor_001.sbfres"
FSR_INIT_CODES = [0, 0, 0, 3, 3, 3, 0, 5, 5]  # (fsredir_probe.c swmod_init: OK, OK, OK again, 3x BAD_ARGS, OK, 2x TAKEN)
FSR_ENV = {"SWMOD_FILE_REDIRECT": "1", "SWMOD_FILE_LOG": "1"}
FSR_ONLY_ENV = {"SWMOD_FILE_REDIRECT": "1"}
FSR_LOG = (
    "[mod] fsredir probe: ready",
    "[modhost] file redirect: group linkle: 2 files (off until a mod turns it on)",
    "[modhost] file redirect: group other: 1 files (off until a mod turns it on)",
    "[modhost] file redirect: on, 4 of 4 file opens wrapped",
    "[modhost] file redirect: group linkle on (2 files)",
    "[modhost] file redirect: group linkle: missing/SwLk_Fail.sbfres didn't open, so Model/Fail.sbfres did (as the "
    "game asked)",
    "[modhost] file redirect: group linkle off (2 files)",
    "[modhost] mod ?: fs_redirect_add refused: only from swmod_init of a mod loaded at game start",  # (as hooks)
)


def fsr_steps() -> list[tuple[str, str]]:
    """Run R1's steps: (name, control line), in order."""
    e8, e9, ea = (f"{FSLOG_FNS[k]:#x}" for k in ("e8fae0", "e92690", "eaba98"))
    return [("init", "fsr init"), ("state0", "fsr state linkle"), ("overrides", "#overrides"),
            ("off_open", f"#open {e8} dev 0 {FSR_ARMOR}"), ("on", "fsr linkle on"),
            ("hit", f"#open {e8} dev 0 {FSR_ARMOR}"), ("prefix", f"#open {e9} dev 0 content:/{FSR_ARMOR}"),
            ("miss", f"#open {e8} dev 0 Model/Armor_002.sbfres"), ("other_off", f"#open {e8} dev 0 Model/Other.sbfres"),
            ("fail", f"#open {e8} dev 0 Model/Fail.sbfres"), ("state1", "fsr state linkle"),
            ("thread_off", "fsr thread linkle off"), ("after_off", f"#open {ea} dev 0 {FSR_ARMOR}"),
            ("late", "fsr late"), ("nogroup", "fsr nope on"), ("state2", "fsr state linkle"), ("flush", "fslog")]


FSR_ONLY_STEPS = (("state", "fsr state linkle"), ("overrides", "#overrides"), ("on", "fsr linkle on"),
                  ("hit", f"#open {FSLOG_FNS['e8fae0']:#x} dev 0 {FSR_ARMOR}"), ("fslog", "fslog"))
FSR_OFF_STEPS = (("init", "fsr init"), ("state", "fsr state linkle"), ("on", "fsr linkle on"),
                 ("overrides", "#overrides"))


def fsr_runs(out: Path) -> tuple:
    """R1, R2 and R3, each in a new fake game: ((replies by step name, fslog.log text, savagewind.log) for R1, then
    (replies, whether fslog.log was written) for R2, then the replies of R3)."""
    st = out / "selftest"
    mods = SELFTEST_FSREDIR.output(out).parent
    file = st / "fslog.log"
    got = []
    for steps, env in ((fsr_steps(), FSR_ENV), (FSR_ONLY_STEPS, FSR_ONLY_ENV), (FSR_OFF_STEPS, {})):
        file.unlink(missing_ok=True)
        res, log = _run_fake_game(out, st / "fake_swgame.dll", mods, [ln for _, ln in steps], env)
        text = file.read_text(encoding="utf-8", errors="replace") if file.is_file() else None
        got.append((dict(zip((k for k, _ in steps), res.get("steps", []))), text, log))
    return got


def selftest_expect_fsr(runs) -> list[str]:
    """The file redirect runs (fsr_runs). Returns the failures."""
    (r1, text, log), (r2, text2, _), (r3, _, _) = runs
    bad = []

    def want(cond, what):
        if not cond:
            bad.append(what)

    def data(k, where=r1):
        return where.get(k, {}).get("data", {})

    init = [data("init").get(f"c{i}") for i in range(len(FSR_INIT_CODES))]
    want(init == FSR_INIT_CODES, f"redirect: fs_redirect_add's answers at swmod_init ({init})")
    s0 = data("state0")
    want((s0.get("code"), s0.get("ready"), s0.get("on"), s0.get("count"), s0.get("wrapped")) == (0, 1, 0, 2, 4),
         f"redirect: the group is ready, off, with 2 files ({s0})")
    o = data("overrides")
    want(o.get("count") == 4 and all(str(o.get(f"{i}.name", "")).startswith("modhost_fslog_") for i in range(4)),
         f"redirect: modhost wraps the 4 file opens ({o})")
    dev = data("hit").get("dev")

    def opened(k, path, where=r1):
        d = data(k, where)
        return d.get("opened") == path and dev and d.get("x0") == dev

    want(opened("off_open", FSR_ARMOR), f"redirect: a group that is off opens the original ({data('off_open')})")
    want(data("on").get("code") == 0, f"redirect: the group turns on ({r1.get('on')})")
    want(opened("hit", FSR_COPY) and data("hit").get("x1") == 1, f"redirect: on, the copy opens ({data('hit')})")
    want(opened("prefix", "content:/" + FSR_COPY) and data("prefix").get("x1") == 2,
         f"redirect: a device prefix is kept ({data('prefix')})")
    want(opened("miss", "Model/Armor_002.sbfres"), "redirect: a file with no redirect opens as asked")
    want(opened("other_off", "Model/Other.sbfres"), "redirect: another group that is off is left alone")
    want(opened("fail", "Model/Fail.sbfres"), f"redirect: a copy that fails: the original opens ({data('fail')})")
    s1 = data("state1")
    want((s1.get("on"), s1.get("opens"), s1.get("redirects"), s1.get("fails"), s1.get("in_flight")) == (1, 4, 3, 1, 0),
         f"redirect: the counts (4 opens of its files, 3 sent to a copy, 1 failed) ({s1})")
    want(data("thread_off").get("code") == 0 and opened("after_off", FSR_ARMOR),
         f"redirect: turned off from another thread, the original opens ({data('after_off')})")
    want(data("late").get("code") == 2 and data("nogroup").get("code") == 6,
         f"redirect: an add after swmod_init is refused, and a group that doesn't exist ({r1.get('late')}, "
         f"{r1.get('nogroup')})")
    want(data("state2").get("on") == 0 and data("state2").get("opens") == 5, f"redirect: off again ({data('state2')})")
    rows = fslog_rows(text or "")
    want(any(r[7:9] == [FSR_ARMOR, f"-> {FSR_COPY}"] for r in rows if len(r) == 9),
         "redirect: the file log notes where an open went")
    want(any(len(r) == 9 and r[7] == "Model/Fail.sbfres" and r[8].startswith("copy missing/SwLk_Fail.sbfres didn't open")
             for r in rows), "redirect: the file log notes a copy that didn't open")
    lines = log.splitlines()
    bad += [f"redirect log line: {w}" for w in FSR_LOG if w not in lines]
    d2 = r2.get("hit", {}).get("data", {})
    want(data("state", r2).get("ready") == 1 and data("overrides", r2).get("count") == 4
         and d2.get("opened") == FSR_COPY and text2 is None and r2.get("fslog", {}).get("ok") is False,
         f"redirect alone (no file log): wrapped and redirected, no fslog.log, no fslog command ({r2})")
    init3 = [data("init", r3).get(f"c{i}") for i in range(len(FSR_INIT_CODES))]
    want(init3 == [1] * len(FSR_INIT_CODES) and data("state", r3).get("code") == 1 and data("on", r3).get("code") == 1
         and data("overrides", r3).get("count") == 0,
         f"redirect off (no SWMOD_FILE_REDIRECT): every call says OFF and nothing is wrapped ({r3})")
    return bad


def _run_fake_game(out: Path, real_lib: Path, mods: Path, lines, env_extra=None, host: Path | None = None
                   ) -> tuple[dict, str]:
    # The child runs in <out>/selftest, so every path it gets must be absolute: ctypes would resolve a relative
    # one (from --out out) against that folder (out\selftest\out\selftest\...).
    out, real_lib, mods = out.resolve(), real_lib.resolve(), mods.resolve()
    host = (host or out / HOST_DLL).resolve()
    st = out / "selftest"
    env = {k: v for k, v in os.environ.items()  # (only when a run asks)
           if not k.upper().startswith(("SWMOD_FILE_LOG", "SWMOD_FILE_REDIRECT"))}
    env.update(SWMOD_REAL_GAME_LIB=str(real_lib), SWMOD_DIR=str(mods), **(env_extra or {}))
    r = run_captured([sys.executable, "-I", "-c", _CHILD, str(st / RUNTIME_DLL), str(host), *lines],
                     env, cwd=st)
    if r.returncode:
        raise BuildError(f"the fake game run crashed (exit {r.returncode}):\n{r.stderr.strip()}")
    return json.loads(r.stdout.strip().splitlines()[-1]), r.stderr


def selftest(tc: Toolchain, out: Path) -> int:
    prepare_hooked_game(out)
    hooked_game = hooked_game_target(out)
    targets = (HOST, *EXAMPLES, *SELFTEST, *HOOK_EXAMPLES, *SELFTEST_HOOKS, *SELFTEST_HOT, HOST_TEST, *SELFTEST_RACE,
               *SELFTEST_FSLOG, *SELFTEST_CHAIN, SELFTEST_FSREDIR, hooked_game)
    built = build(tc, out, targets)
    check_exports(zip(built, targets))
    st = out / "selftest"
    if "swmod_hooks" not in pe_info(hooked_game.output(out).read_bytes()).exports:
        raise BuildError(f"{hooked_game.filename} was built but doesn't export swmod_hooks")
    mods = st / "mods"
    shutil.copy2(EXAMPLES[0].output(out), mods / EXAMPLES[0].filename)
    (mods / "junk.dll").write_bytes(b"not a DLL")
    result, log = _run_fake_game(out, st / "fake_swgame.dll", mods, SELFTEST_LINES)
    missing, missing_log = _run_fake_game(out, st / "no_such_game.dll", mods, ("#commands",))
    other, other_log = _run_fake_game(out, st / "fake_swgame_other.dll", mods, ("#commands",))
    hook_mods = st / "hook-mods"
    shutil.copy2(HOOK_EXAMPLES[0].output(out), hook_mods / HOOK_EXAMPLES[0].filename)
    hooked, hooked_log = _run_fake_game(out, hooked_game.output(out), hook_mods, HOOK_LINES)
    plain, plain_log = _run_fake_game(out, st / "fake_swgame.dll", hook_mods, ("mods", "hookprobe"))
    hot_run, hot_start, hot_none = st / "hot-run", st / "hot-start", st / "hot-none"
    for d in (hot_run, hot_start, hot_none):
        d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SELFTEST_HOOKS[0].output(out), hot_start / SELFTEST_HOOKS[0].filename)
    for t, name in zip(SELFTEST_HOT, HOT_FILES):  # (copies with new names, as the editor makes them)
        shutil.copy2(t.output(out), hot_run / name)
    (hot_run / "junk-1.dll").write_bytes(b"not a DLL")
    steps, plain_steps = hot_steps(hot_run), hot_plain_steps(hot_run)
    hot, hot_log = _run_fake_game(out, hooked_game.output(out), hot_start, [ln for _, ln in steps], HOT_ENV)
    hot_plain, _ = _run_fake_game(out, st / "fake_swgame.dll", hot_none, [ln for _, ln in plain_steps], HOT_ENV)
    race_run = st / "race-run"
    race_run.mkdir(parents=True, exist_ok=True)
    for t, name in zip((SELFTEST_HOT[0], SELFTEST_HOT[0], SELFTEST_RACE[1], SELFTEST_RACE[2]), RACE_FILES):
        shutil.copy2(t.output(out), race_run / name)
    rsteps = race_steps(race_run)
    race, race_log = _run_fake_game(out, hooked_game.output(out), SELFTEST_RACE[0].output(out).parent,
                                    [ln for _, ln in rsteps], RACE_ENV, host=HOST_TEST.output(out))
    rnames = [k for k, _ in rsteps]
    fa, fa_text, fa_log, fb, fb_text, fb_prev, fc, fc_text = fslog_runs(out)
    fd, fd_text, fd_log = fslog_overlap_run(out)
    ce, ce_log = chain_run(out)
    fsr = fsr_runs(out)
    bad = (selftest_expect(result) + selftest_expect_log(log)
           + selftest_expect_fallback(missing, missing_log, other, other_log)
           + selftest_expect_hooks(hooked, hooked_log, plain, plain_log)
           + selftest_expect_hot(dict(zip((k for k, _ in steps), hot.get("steps", []))), hot_log,
                                 dict(zip((k for k, _ in plain_steps), hot_plain.get("steps", []))))
           + selftest_expect_race(dict(zip(rnames, race.get("steps", []))), dict(zip(rnames, race.get("ms", []))),
                                  race_log)
           + selftest_expect_fslog(fa, fa_text, fa_log, fb, fb_text, fb_prev, fc, fc_text)
           + selftest_expect_fslog_overlap(fd, fd_text, fd_log)
           + selftest_expect_chain(ce, ce_log)
           + selftest_expect_fsr(fsr))
    print("Selftest passed: modhost loaded the fake game and 5 test mods as expected, and fell back safely"
          " without the game code or with game code for another version. Hooks: before, after and replace ran"
          " for direct calls, tail jumps and the function table, and stamina_hook worked. Loading while the game"
          " runs: modload, modreload and modunload worked, modunload refused while a thread was inside the"
          " mod's hook, modreload kept the old build when the new file was bad, a thread caught just before the"
          " gate ran the game's own function after the unload, and overrides a hook tried to add were refused."
          " File log: the 4 file opens were wrapped and logged (repeats counted, limits kept, pause and resume,"
          " paths Windows says can't be read), one a mod overrides first was left to it, and modload was refused"
          " the ones the log wraps. Override chains: two mods on one function both ran, in order, each once, and a"
          " failed mod's override was dropped. File redirect: a group sent its files to their copies, kept a"
          " device prefix, fell back to the original when a copy didn't open, flipped from another thread, and was"
          " OFF without SWMOD_FILE_REDIRECT."
          if not bad else f"Selftest FAILED ({len(bad)} problem(s)):")
    for b in bad:
        print(f"  - {b}")
    for name, text in (("main run", log), ("hook run", hooked_log), ("hot-load run", hot_log),
                       ("race run", race_log), ("file log run", fa_log), ("file log overlap run", fd_log),
                       ("override chain run", ce_log), ("file redirect run", fsr[0][2])):
        print(f"modhost log ({name}):")
        for line in text.strip().splitlines():
            print(f"  {line}")
    return 1 if bad else 0


# ------------------------------------------------------------------ command line

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build and install the native code-mod host for Savage Wind.")
    ap.add_argument("action", choices=("build", "install", "uninstall", "check", "imports", "selftest"))
    ap.add_argument("files", nargs="*", help="for imports: DLL or EXE files")
    ap.add_argument("--install", type=Path, default=None, help="your Savage Wind folder")
    ap.add_argument("--data-root", type=Path, default=None, help="Savage Wind's data folder (default: --install)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="build folder")
    ap.add_argument("--dry-run", action="store_true", help="install/uninstall: only show what would change")
    ap.add_argument("--force", action="store_true", help="install: also replace files install didn't write")
    ap.add_argument("--port", type=int, default=7777, help="check: the SAVAGEWIND_CONTROL port")
    a = ap.parse_args(argv)
    if a.files and a.action != "imports":
        ap.error(f"{a.action} takes no file names")
    a.out = a.out.resolve()  # (selftest runs the fake game in <out>/selftest: a relative --out would break there)
    install = a.install or default_install()
    data_root = a.data_root or default_data_root(install)
    try:
        if a.action == "imports":
            if not a.files:
                ap.error("imports needs at least one file")
            for f in a.files:
                print(describe(Path(f)))
            return 0
        if a.action == "check":
            return check_game(a.port)
        if a.action == "install":
            steps = plan_install(a.out, data_root, force=a.force)
            lines = run_steps(steps, a.dry_run)
            if not a.dry_run:
                record_install(data_root, steps)
            print(("Would copy" if a.dry_run else "Installed") + f" {len(steps)} file(s); Savage Wind is unchanged.")
            for ln in lines:
                print(f"  {ln}")
            print("Start the game with modhost (PowerShell):")
            for ln in launch_lines(install, data_root, a.port):
                print(f"  {ln}")
            print("Without SAVAGEWIND_GAME_LIB the game starts as usual, without mods.")
            return 0
        if a.action == "uninstall":
            steps, left = plan_uninstall(data_root)
            lines = run_steps(steps, a.dry_run)
            if not a.dry_run:
                forget_install(data_root)
            print(("Would remove" if a.dry_run else "Removed") + f" {len(steps)} file(s).")
            for ln in lines:
                print(f"  {ln}")
            for path, why in left:
                print(f"  left {path} ({why}; delete it yourself if you don't need it)")
            return 0
        tc = find_toolchain(install)
        if a.action == "selftest":
            return selftest(tc, a.out)
        targets = (HOST, *EXAMPLES, *HOOK_EXAMPLES, *GHOST_EXAMPLES, *BASICS_EXAMPLES, *SETTINGS_EXAMPLES,
                   *EMOTES_EXAMPLES)
        built = build(tc, a.out, targets)
        check_exports(zip(built, targets))
        print(f"Built {len(built)} file(s) in {a.out}")
        for p in built:
            print(f"  {describe(p, a.out)}")
        print("Next: python tools/build_native.py install")
        print(f"({', '.join(t.filename for t in HOOK_EXAMPLES)} in {a.out / 'hook-mods'} needs game code built with"
              " hook slots and isn't installed: see docs/hooks.md)")
        print(f"({', '.join(t.filename for t in GHOST_EXAMPLES)} in {a.out / 'ghost-mods'} is experimental and isn't"
              " installed: see docs/ghost.md)")
        print(f"({', '.join(t.filename for t in BASICS_EXAMPLES)} in {a.out / 'basics-mods'} are examples for"
              " 1.9.0 and aren't installed: see docs/game-basics.md)")
        if SETTINGS_EXAMPLES:
            print(f"({', '.join(t.filename for t in SETTINGS_EXAMPLES)} in {a.out / 'settings-mods'} isn't installed:"
                  " add it with modman, see docs/mod-settings.md)")
        print(f"({', '.join(t.filename for t in EMOTES_EXAMPLES)} in {a.out / 'emotes-mods'} is experimental and isn't"
              " installed: add it with modman, see docs/emotes.md)")
        return 0
    except (BuildError, InstallError, PeError, OSError) as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
