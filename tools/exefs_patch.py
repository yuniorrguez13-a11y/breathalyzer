#!/usr/bin/env python3
"""Apply Switch ExeFS patches (.ips, IPS32, .pchtxt) to the player's own exefs, for Savage Wind to recompile.

    python tools/exefs_patch.py check <exefs folder> <patch file or folder>...
    python tools/exefs_patch.py apply <exefs folder> <output folder> <patch file or folder>... [--stored]

`check` changes nothing: it says which module each patch is for, whether it matches your game version,
and whether patches clash. `apply` writes patched copies of the modules to the output folder and leaves
your exefs alone. Patches are matched to modules by build id, the way Atmosphere does it:
  - .ips / IPS32: the file name is the module's build id (Atmosphere's exefs_patches layout)
  - .pchtxt: the `@nsobid-` line
Offsets follow Atmosphere: an IPS offset counts from the start of the NSO header (0x100 bytes), so
module offset = IPS offset - 0x100. In .pchtxt files, `@flag offset_shift N` is added first.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nso as nsolib  # noqa: E402

NSO_HEADER = nsolib.HEADER_SIZE
MODULE_NAMES = ("rtld", "main", "subsdk0", "subsdk1", "subsdk2", "subsdk3", "subsdk4", "subsdk5", "subsdk6",
                "subsdk7", "subsdk8", "subsdk9", "sdk")
PATCH_EXTS = (".ips", ".pchtxt")
HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


class PatchError(ValueError):
    pass


@dataclass
class Patch:
    source: str          # file the patch came from
    name: str            # patch name (pchtxt section title), or the file name
    build_id: str        # lowercase hex, possibly a prefix of the full 64-digit id
    offset: int          # module offset (already shifted; negative means it touched the NSO header)
    data: bytes
    enabled: bool = True

    @property
    def end(self) -> int:
        return self.offset + len(self.data)

    def label(self) -> str:
        return self.name if self.name == os.path.basename(self.source) else f"{os.path.basename(self.source)}: {self.name}"


# --- parsing -------------------------------------------------------------------------------------------------------

def build_id_from_name(path: str) -> str:
    """Atmosphere names .ips files after the module's build id (16 to 64 hex digits)."""
    stem = os.path.splitext(os.path.basename(path))[0]
    if not HEX_RE.match(stem) or not 16 <= len(stem) <= 64 or len(stem) % 2:
        raise PatchError(f"{os.path.basename(path)}: the file name isn't a build id")
    return stem.lower().rstrip("0") or "0"


def parse_ips(data: bytes, *, source: str, build_id: str) -> list[Patch]:
    if data.startswith(b"IPS32"):
        pos, off_len, eof = 5, 4, b"EEOF"
    elif data.startswith(b"PATCH"):
        pos, off_len, eof = 5, 3, b"EOF"
    else:
        raise PatchError(f"{os.path.basename(source)}: not an IPS or IPS32 patch")
    name = os.path.basename(source)
    out: list[Patch] = []
    n = len(data)
    while True:
        if data[pos:pos + len(eof)] == eof:  # Atmosphere stops here too (so IPS can't use offset 0x454f46)
            break
        if pos + off_len + 2 > n:
            raise PatchError(f"{name}: ends without an {eof.decode()} marker")
        offset = int.from_bytes(data[pos:pos + off_len], "big")
        pos += off_len
        size = int.from_bytes(data[pos:pos + 2], "big")
        pos += 2
        if size == 0:  # RLE record: 2-byte count, 1-byte value
            if pos + 3 > n:
                raise PatchError(f"{name}: truncated RLE record")
            count = int.from_bytes(data[pos:pos + 2], "big")
            value = data[pos + 2:pos + 3]
            pos += 3
            payload = value * count
        else:
            if pos + size > n:
                raise PatchError(f"{name}: record at 0x{offset:x} runs past the end of the file")
            payload = data[pos:pos + size]
            pos += size
        out.append(Patch(source, name, build_id, offset - NSO_HEADER, bytes(payload)))
    return out


_STR_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"'}


def _parse_value(raw: str, where: str) -> bytes:
    raw = raw.strip()
    if raw.startswith('"'):
        if not raw.endswith('"') or len(raw) < 2:
            raise PatchError(f"{where}: unterminated string value")
        body, out, i = raw[1:-1], [], 0
        while i < len(body):
            c = body[i]
            if c == "\\" and i + 1 < len(body):
                out.append(_STR_ESCAPES.get(body[i + 1], body[i + 1]))
                i += 2
            else:
                out.append(c)
                i += 1
        return "".join(out).encode("utf-8")
    hexs = raw.replace(" ", "")
    if hexs.lower().startswith("0x"):
        hexs = hexs[2:]
    if not hexs or not HEX_RE.match(hexs) or len(hexs) % 2:
        raise PatchError(f"{where}: value '{raw}' isn't whole hex bytes")
    return bytes.fromhex(hexs)


def _parse_int(raw: str) -> int:
    raw = raw.strip()
    return int(raw, 16) if raw.lower().startswith("0x") else int(raw, 0 if raw.isdigit() else 16)


def parse_pchtxt(text: str, *, source: str) -> list[Patch]:
    """IPSwitch's text patch format.

    @nsobid-<build id>        which module the patches are for
    @flag offset_shift 0x100  added to every offset (then the 0x100 NSO header is taken off)
    // Patch name              the last comment before @enabled / @disabled names the section
    @enabled | @disabled       starts a section
    <offset> <hex bytes | "string">
    @stop                      ignore the rest of the file
    """
    fname = os.path.basename(source)
    build_id = None
    shift = 0
    enabled = None          # None: before any section
    name = fname
    last_comment = None
    out: list[Patch] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        where = f"{fname}:{lineno}"
        s = line.strip()
        if not s:
            continue
        if s.startswith("//") or s.startswith("#"):
            last_comment = s.lstrip("/#").strip() or last_comment
            continue
        if s.startswith("["):  # some files title sections as [Name]
            last_comment = s.strip("[]").strip() or last_comment
            continue
        if s.startswith("@"):
            word = s.split()[0].lower()
            if word.startswith("@nsobid-"):
                bid = s.split()[0][len("@nsobid-"):]
                if not HEX_RE.match(bid):
                    raise PatchError(f"{where}: bad build id '{bid}'")
                build_id = bid.lower().rstrip("0") or "0"
            elif word == "@flag":
                parts = s.split()
                if len(parts) >= 3 and parts[1].lower() == "offset_shift":
                    shift = _parse_int(parts[2])
            elif word in ("@enabled", "@disabled"):
                enabled = word == "@enabled"
                name = last_comment or f"section at line {lineno}"
                last_comment = None
            elif word == "@stop":
                break
            continue  # other @ lines (@flag print_values, debug_info...) don't change the patches
        if enabled is None:
            continue  # patch lines before any @enabled/@disabled aren't applied by IPSwitch either
        body = s.split("//", 1)[0].strip()
        if not body:
            continue
        parts = body.split(None, 1)
        if len(parts) != 2 or not HEX_RE.match(parts[0].removeprefix("0x").removeprefix("0X")):
            raise PatchError(f"{where}: expected '<offset> <value>', got '{body}'")
        offset = int(parts[0], 16)
        value = _parse_value(parts[1], where)
        if build_id is None:
            raise PatchError(f"{where}: patch before the @nsobid- line")
        out.append(Patch(source, name, build_id, offset + shift - NSO_HEADER, value, enabled))
    if build_id is None:
        raise PatchError(f"{fname}: no @nsobid- line")
    return out


def load_patch_file(path: str) -> list[Patch]:
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as fh:
        raw = fh.read()
    if ext == ".ips":
        return parse_ips(raw, source=path, build_id=build_id_from_name(path))
    if ext == ".pchtxt":
        return parse_pchtxt(raw.decode("utf-8-sig", errors="replace"), source=path)
    raise PatchError(f"{os.path.basename(path)}: not a .ips or .pchtxt file")


def collect_patch_files(paths: list[str]) -> list[str]:
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                files += [os.path.join(root, n) for n in sorted(names) if n.lower().endswith(PATCH_EXTS)]
        else:
            files.append(p)
    return files


# --- matching and checking -----------------------------------------------------------------------------------------

def read_module_ids(exefs: str) -> dict[str, str]:
    """{module name: full build id hex} for every NSO in an exefs folder."""
    ids = {}
    for name in MODULE_NAMES:
        p = os.path.join(exefs, name)
        if os.path.isfile(p):
            try:
                ids[name] = nsolib.read_build_id(p)
            except nsolib.NsoError:
                pass
    if not ids:
        raise PatchError(f"{exefs}: no NSO modules (main, sdk, subsdk0...) found")
    return ids


def module_for(build_id: str, ids: dict[str, str]) -> str | None:
    for name, full in ids.items():
        if full.startswith(build_id) or full.rstrip("0") == build_id:
            return name
    return None


@dataclass
class Report:
    targets: dict[str, list[Patch]] = field(default_factory=dict)     # module -> enabled patches
    wrong_version: list[Patch] = field(default_factory=list)
    disabled: list[Patch] = field(default_factory=list)
    header_writes: list[Patch] = field(default_factory=list)
    conflicts: list[tuple[Patch, Patch]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    code_patches: dict[str, int] = field(default_factory=dict)    # module -> patches that change .text

    @property
    def ok(self) -> bool:
        return not self.conflicts and not self.errors

    @property
    def needs_rebuild(self) -> bool:
        """Savage Wind runs recompiled code, so .text changes need the game code rebuilt.
        rodata/data changes are read from exefs at every start and need no rebuild."""
        return any(self.code_patches.values())


def find_conflicts(patches: list[Patch]) -> list[tuple[Patch, Patch]]:
    """Pairs of patches that write different bytes to the same place."""
    out = []
    ordered = sorted(patches, key=lambda p: p.offset)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if b.offset >= a.end:
                break
            lo, hi = max(a.offset, b.offset), min(a.end, b.end)
            if a.data[lo - a.offset:hi - a.offset] != b.data[lo - b.offset:hi - b.offset]:
                out.append((a, b))
    return out


def check(exefs: str, patch_paths: list[str]) -> Report:
    ids = read_module_ids(exefs)
    rep = Report()
    for f in collect_patch_files(patch_paths):
        try:
            patches = load_patch_file(f)
        except (PatchError, OSError) as e:
            rep.errors.append(str(e))
            continue
        for p in patches:
            if not p.enabled:
                rep.disabled.append(p)
                continue
            mod = module_for(p.build_id, ids)
            if mod is None:
                rep.wrong_version.append(p)
            elif p.offset < 0:
                rep.header_writes.append(p)
            else:
                rep.targets.setdefault(mod, []).append(p)
    for mod, ps in rep.targets.items():
        rep.conflicts += find_conflicts(ps)
        try:
            layout = nsolib.read_layout(os.path.join(exefs, mod))
        except nsolib.NsoError:
            continue
        text = next(((off, size) for name, off, size in layout if name == "text"), None)
        if text:
            rep.code_patches[mod] = sum(1 for p in ps if p.offset < text[0] + text[1] and p.end > text[0])
    return rep


def same_path(a: str, b: str) -> bool:
    """Whether two paths name the same file or folder, however they are spelled: another case, an 8.3 short name,
    a junction or subst drive, or (for files) another hard link. samefile when both exist, else the case-folded
    realpath."""
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def apply(exefs: str, out_dir: str, patch_paths: list[str], *, stored: bool = False) -> tuple[Report, list[str]]:
    """Write patched copies of the modules into out_dir. Returns the report and lines describing what changed.
    Nothing in exefs is written: an out_dir that is exefs itself (spelled any way), or that holds a file which is
    one of exefs's files under another name, is refused before anything is written."""
    rep = check(exefs, patch_paths)
    if not rep.ok:
        return rep, []
    if same_path(out_dir, exefs):
        raise PatchError("the output folder must not be the exefs folder itself")
    names = [n for n in os.listdir(exefs) if os.path.isfile(os.path.join(exefs, n))]
    for name in names:
        src, dst = os.path.join(exefs, name), os.path.join(out_dir, name)
        if os.path.exists(dst) and same_path(src, dst):
            raise PatchError(f"{dst} is {src} itself (a link to it), so writing it would change your exefs: "
                             "use a new, empty output folder")
    os.makedirs(out_dir, exist_ok=True)
    notes = []
    for name in names:
        if name not in rep.targets:
            shutil.copy2(os.path.join(exefs, name), os.path.join(out_dir, name))
    for mod, patches in rep.targets.items():
        module = nsolib.load(os.path.join(exefs, mod))
        changed = 0
        for p in patches:
            if p.end > module.image_size:
                rep.errors.append(f"{p.label()}: writes past the end of {mod} (0x{p.offset:x})")
                continue
            try:
                before = module.read(p.offset, len(p.data))
            except nsolib.NsoError as e:
                rep.errors.append(f"{p.label()}: {e}")
                continue
            if before != p.data:
                module.write(p.offset, p.data)
                changed += 1
        data = module.to_bytes(compress=False if stored else None)
        with open(os.path.join(out_dir, mod), "wb") as fh:
            fh.write(data)
        notes.append(f"{mod}: {len(patches)} patches, {changed} changed bytes-runs")
    return rep, notes


# --- CLI -----------------------------------------------------------------------------------------------------------

def _print_report(rep: Report) -> None:
    for mod, ps in rep.targets.items():
        files = sorted({os.path.basename(p.source) for p in ps})
        print(f"OK  {mod}: {len(ps)} patches from {', '.join(files)}")
    if rep.needs_rebuild:
        n = sum(rep.code_patches.values())
        print(f"NOTE  {n} patches change game code: the game code must be rebuilt (tools/sw_rebuild.py)")
    elif rep.targets:
        print("NOTE  data-only patches: no rebuild needed")
    for p in rep.wrong_version:
        print(f"SKIP  {p.label()}: made for another game version (build id {p.build_id[:16]}); needs porting")
    if rep.disabled:
        print(f"OFF   {len(rep.disabled)} disabled patch sections left out")
    for p in rep.header_writes:
        print(f"SKIP  {p.label()}: writes into the NSO header, which Atmosphere ignores too")
    for a, b in rep.conflicts:
        print(f"CLASH {a.label()} and {b.label()} write different bytes at 0x{max(a.offset, b.offset):x}")
    for e in rep.errors:
        print(f"ERROR {e}")


def _safe_stdout() -> None:
    """Let print() take any path or patch name. Redirected to a file or a pipe, Windows Python writes stdout in the
    ANSI code page with strict errors, so a letter it lacks (a patch named in Chinese, a user folder with an
    L-stroke) ended the run in a UnicodeEncodeError that hid the report; such letters now print as '?'. Left alone
    when the stream already has a lenient handler (as when modman runs this in its own process)."""
    try:
        if sys.stdout.errors in ("strict", "surrogateescape"):
            sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


def main(argv: list[str] | None = None) -> int:
    _safe_stdout()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="say what the patches would do; changes nothing")
    c.add_argument("exefs")
    c.add_argument("patches", nargs="+")
    a = sub.add_parser("apply", help="write patched copies of the modules")
    a.add_argument("exefs")
    a.add_argument("out")
    a.add_argument("patches", nargs="+")
    a.add_argument("--stored", action="store_true", help="write segments uncompressed")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "check":
            rep = check(args.exefs, args.patches)
            _print_report(rep)
            return 0 if rep.ok else 1
        rep, notes = apply(args.exefs, args.out, args.patches, stored=args.stored)
        _print_report(rep)
        for n in notes:
            print(n)
        return 0 if rep.ok else 1
    except (PatchError, nsolib.NsoError, OSError) as e:
        print(f"ERROR {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
