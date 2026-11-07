#!/usr/bin/env python3
"""Add hook slots to the generated C of the game's main module, so native mods can hook any recompiled function.

    python tools/gen_hooks.py show <hooks file>...                  # the addresses the files list
    python tools/gen_hooks.py check <generated main folder> <hooks file>...   # are they function starts?
    python tools/gen_hooks.py apply <generated main folder> <hooks file>...   # add the slots
    python tools/gen_hooks.py remove <generated main folder>                  # undo apply

Normally you don't run this yourself: `sw_rebuild.py build --hooks <file>` runs it between generating and compiling.

How it works. Savage Wind's generator (tools/gen.py at 0.4.1-alpha.4) writes every guest function as
`void f_<addr>(Cpu *c) {` in one of the chunk files main_NNN.c, calls it directly by that name (`f_<addr>(c);` for a
`bl`, `__attribute__((musttail)) return f_<addr>(c);` for a tail jump) and lists it in main_table.c, which the
dispatcher uses for indirect calls. For each hooked address this tool renames the definition to f_<addr>__orig and
writes main_hooks.c, which defines f_<addr> again as a small wrapper:

    void f_<addr>(Cpu *c) {
        SwHookSlot *s = &swmod_slots[i];
        SwHookEntry e = <load s->entry>;
        if (e && e(c, s)) return;                          // a mod's hook ran (and maybe the function too)
        __attribute__((musttail)) return f_<addr>__orig(c);  // no hook: on to the function, no extra stack
    }

So every call from another function reaches the wrapper: direct calls in any chunk (they use the name), tail jumps
and the dispatch table (it lists the name). Calls in the same chunk can't be inlined any more either: the wrapper is in
another file and the build has no link-time optimisation. The library exports `swmod_hooks()`, which returns the slot
table; modhost fills in `entry` before any game code runs (native/modhost/modhost.c).

One exception: a jump from a function back to its own start (a loop that begins at the first instruction, or a call
to itself as its last step) is `goto L_<addr>;` inside the function, so it doesn't pass the wrapper again. That is
left alone on purpose: going through the wrapper there would run before hooks on every loop pass, and an after or
replace hook would add a stack frame per pass. apply and sw_rebuild name the hooked functions that do this
(self_jumps()).

The output depends only on the sorted set of addresses, and running apply again with the same list changes nothing.
Savage Wind is MIT licensed; the generated-C conventions above follow its tools/gen.py.
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field

FORMAT = 1             # bump when the generated C changes (part of sw_rebuild's cache key)
HOOK_ABI = 1           # the SwHookTable layout (modhost.c checks it)
EXPORT = "swmod_hooks"  # the function the library exports
MAIN_BASE = 0x7000000000  # where Savage Wind loads main (tools/layout.py: main first, at MAIN_BASE)
MAX_HOOKS = 4096
MARK = "/* gen_hooks: the wrapper is in {hooks} */"

_DEF_RE = re.compile(r"^void f_([0-9a-f]+)\(Cpu \*c\) \{(\r?)$", re.M)
# what apply writes in place of a definition line (the prototype keeps calls further down pointing at the wrapper)
_HOOKED_RE = re.compile(r"^void f_([0-9a-f]+)\(Cpu \*c\); /\* gen_hooks: [^*\r\n]* \*/(\r?)\n"
                        r"void f_\1__orig\(Cpu \*c\) \{\r?$", re.M)
_ADDR_RE = re.compile(r"^(?:(main)\s*\+\s*)?(0[xX][0-9a-fA-F]+|[0-9a-fA-F]+)$")
_END_RE = re.compile(r"^\}\r?$", re.M)  # the generator closes each function with "}" alone on a line


class HookError(Exception):
    """A hooks list or a generated folder that can't be used; the message says what."""


# --- hooks files -------------------------------------------------------------------------------------------------

def parse_address(token, where: str = "") -> int:
    """A guest address: 0x7000cc7410, 7000cc7410 or main+0xcc7410 (an offset into main), or an int."""
    if isinstance(token, bool) or not isinstance(token, (int, str)):
        raise HookError(f"{where}{token!r} is not an address")
    if isinstance(token, int):
        value = token
    else:
        m = _ADDR_RE.match(token.strip())
        if not m:
            raise HookError(f"{where}{token!r} is not an address (write it like 0x7000cc7410 or main+0xcc7410)")
        value = int(m.group(2), 16) + (MAIN_BASE if m.group(1) else 0)
    if value <= 0 or value >= 1 << 64:
        raise HookError(f"{where}{token!r} is not an address")
    return value


def parse_hooks_text(text: str, source: str = "hooks") -> list[int]:
    """The addresses a hooks list names, sorted, each once. Two formats:
    text: one address per line, `#` starts a comment, anything after the address is ignored;
    JSON: a list of addresses, or {"hooks": [...]}, where an entry is an address or {"addr": address, ...}."""
    out = set()
    body = text.lstrip("﻿").strip()
    if body.startswith(("[", "{")):
        try:
            data = json.loads(body)
        except ValueError as e:
            raise HookError(f"{source}: not valid JSON ({e})") from None
        items = data.get("hooks") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise HookError(f'{source}: expected a list of addresses or {{"hooks": [...]}}')
        for i, item in enumerate(items):
            if isinstance(item, dict):
                if "addr" not in item:
                    raise HookError(f'{source}: entry {i + 1} has no "addr"')
                item = item["addr"]
            out.add(parse_address(item, f"{source}: entry {i + 1}: "))
    else:
        for n, line in enumerate(body.splitlines(), 1):
            words = line.split("#", 1)[0].split()
            if words:
                out.add(parse_address(words[0].rstrip(","), f"{source}: line {n}: "))
    if len(out) > MAX_HOOKS:
        raise HookError(f"{source}: {len(out)} addresses; at most {MAX_HOOKS}")
    return sorted(out)


def read_hooks_files(paths) -> list[int]:
    """The addresses of one or more hooks files together, sorted, each once."""
    if isinstance(paths, (str, os.PathLike)):
        paths = [paths]
    out = set()
    for p in paths:
        try:
            with open(p, encoding="utf-8-sig") as f:
                text = f.read()
        except OSError as e:
            raise HookError(f"can't read the hooks file {p}: {e.strerror or e}") from None
        except UnicodeDecodeError:
            raise HookError(f"{p} is not a text file") from None
        out.update(parse_hooks_text(text, os.path.basename(str(p))))
    if len(out) > MAX_HOOKS:
        raise HookError(f"{len(out)} addresses; at most {MAX_HOOKS}")
    return sorted(out)


def check_in_code(addrs, lo: int, hi: int, module: str = "main") -> list[str]:
    """Problems with addresses that can't be function starts in [lo, hi) (the module's code): a quick check before
    a build; the generated function table has the final say."""
    bad = []
    for a in addrs:
        if not lo <= a < hi:
            bad.append(f"{a:#x} is not in {module}'s code ({lo:#x}-{hi:#x})")
        elif a % 4:
            bad.append(f"{a:#x} is not a multiple of 4, so it can't be a function start")
    return bad


# --- the generated C ---------------------------------------------------------------------------------------------

def fname(a: int) -> str:
    return f"f_{a:x}"


def hooks_file_name(module: str = "main") -> str:
    return f"{module}_hooks.c"


def chunk_files(outdir: str, module: str = "main") -> list[str]:
    """The chunk files (<module>_NNN.c) in outdir, in order."""
    try:
        names = os.listdir(outdir)
    except OSError as e:
        raise HookError(f"can't read {outdir}: {e.strerror or e}") from None
    pat = re.compile(re.escape(module) + r"_(\d{3,})\.c")
    return [os.path.join(outdir, n) for n in sorted(names, key=lambda n: (len(n), n)) if pat.fullmatch(n)]


def _read(path: str) -> str:  # (byte for byte: latin-1, line ends as they are)
    with open(path, encoding="latin-1", newline="") as f:
        return f.read()


def _write(path: str, text: str, tries: int = 6) -> None:
    """A new file renamed over path (retried a few times: a virus scanner may hold a file the generator just
    wrote)."""
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="latin-1", newline="") as f:
            f.write(text)
        for i in range(tries):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if i == tries - 1:
                    raise
                time.sleep(0.1 * 2 ** i)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def rewrite_chunk(text: str, addrs, module: str = "main") -> tuple[str, set[int]]:
    """(the chunk with each hooked definition renamed, the hooked addresses defined in it). A definition renamed
    earlier for an address no longer in addrs gets its name back, so the result only depends on addrs."""
    want = set(addrs)
    mark = MARK.format(hooks=hooks_file_name(module))
    found = set()

    def unhook(m):
        a = int(m.group(1), 16)
        if a in want:
            found.add(a)
            return m.group(0)
        return f"void {fname(a)}(Cpu *c) {{{m.group(2)}"

    text = _HOOKED_RE.sub(unhook, text)

    def hook(m):
        a = int(m.group(1), 16)
        if a not in want:
            return m.group(0)
        found.add(a)
        nl = m.group(2) + "\n"
        return f"void {fname(a)}(Cpu *c); {mark}{nl}void {fname(a)}__orig(Cpu *c) {{{m.group(2)}"

    return _DEF_RE.sub(hook, text), found


def _rewrite_file(path: str, addrs, module: str) -> tuple[str, set[int]]:
    text = _read(path)
    new, found = rewrite_chunk(text, addrs, module)
    if new != text:
        _write(path, new)
    return new, found


def rewrite_chunk_file(path: str, addrs, module: str = "main") -> set[int]:
    """rewrite_chunk on a file (written again only when it changes). Returns the hooked addresses it defines."""
    return _rewrite_file(path, addrs, module)[1]


def self_jumps(text: str, addr: int) -> int:
    """How many jumps the definition of f_<addr> (or f_<addr>__orig) in a chunk makes back to its own start. The
    generator writes them as `goto L_<addr>;` inside the function (gen.py jump(): a target within the function), so
    they don't pass the hook wrapper. The all-instruction switch after `L_dispatch:` is not counted: only a computed
    jump gets there."""
    m = re.search(rf"^void {fname(addr)}(?:__orig)?\(Cpu \*c\) \{{\r?$", text, re.M)
    if not m:
        return 0
    end = _END_RE.search(text, m.end())
    body = text[m.end():end.start() if end else len(text)]
    body = re.split(r"^L_dispatch:", body, maxsplit=1, flags=re.M)[0]
    return body.count(f"goto L_{addr:x};")


def self_jump_note(jumps) -> str:
    """One line for the hooked functions that jump back to their own start ('' when none do)."""
    if not jumps:
        return ""
    names = ", ".join(fname(a) for a in sorted(jumps))
    return (f"Note: {names} jump{'s' if len(jumps) == 1 else ''} back to {'its' if len(jumps) == 1 else 'their'} own "
            "start (a loop, or a call to itself). Hooks run for the outer call only, not for those jumps "
            "(docs/hooks.md, Limits).")


def table_addrs(text: str, module: str = "main") -> list[int]:
    """The function addresses of a generated <module>_table.c (its <module>_fn_addrs array)."""
    m = re.search(re.escape(module) + r"_fn_addrs\[\]\s*=\s*\{(.*?)\};", text, re.S)
    if not m:
        raise HookError(f"{module}_table.c has no {module}_fn_addrs (not the generator's output?)")
    return [int(x, 16) for x in re.findall(r"0[xX]([0-9a-fA-F]+)", m.group(1))]


def read_table(outdir: str, module: str = "main") -> list[int]:
    path = os.path.join(outdir, f"{module}_table.c")
    try:
        return table_addrs(_read(path), module)
    except OSError:
        raise HookError(f"no {module}_table.c in {outdir}: generate the C first") from None


def entry_problems(addrs, entries) -> list[str]:
    """Why some addresses can't be hooked: they aren't the start of a recompiled function (the generator's function
    table, which the dispatcher uses too, is the list of starts)."""
    entries = sorted(set(entries))
    have = set(entries)
    bad = []
    for a in addrs:
        if a in have:
            continue
        i = bisect.bisect_right(entries, a) - 1
        if i >= 0 and (i + 1 == len(entries) or a < entries[i + 1]) and a - entries[i] < 0x100000:
            bad.append(f"{a:#x} is not the start of a function (it is {a - entries[i]:#x} bytes into "
                       f"{fname(entries[i])})")
        else:
            bad.append(f"{a:#x} is not the start of a function")
    return bad


def hooks_c(addrs, module: str = "main") -> str:
    """The C file with the slot table, the export and one wrapper per hooked function (deterministic)."""
    addrs = sorted(set(addrs))
    n = len(addrs)
    lines = [
        f"// {hooks_file_name(module)}: hook slots for {n} function{'s' if n != 1 else ''} of {module}.",
        "// Written by Breathalyzer tools/gen_hooks.py; each f_<addr> below replaces the generated function, which",
        "// was renamed f_<addr>__orig. A mod host fills in the slots through the exported function swmod_hooks().",
        '#include "cpu.h"',
        "",
        "typedef struct SwHookSlot SwHookSlot;",
        "// Returns nonzero when it handled the call (the function then isn't run again here).",
        "typedef int (*SwHookEntry)(Cpu *c, SwHookSlot *slot);",
        "struct SwHookSlot {",
        "    uint64_t addr;      // the function's guest address",
        "    GuestFn orig;       // the function itself (without the hook)",
        "    SwHookEntry entry;  // set by the mod host before game code runs; NULL = not hooked",
        "    void *user;         // the mod host's own data",
        "};",
        "typedef struct {",
        f"    uint32_t abi;       // {HOOK_ABI}",
        "    uint32_t count;",
        "    SwHookSlot *slots;  // sorted by addr",
        "    const char *made_by;",
        "} SwHookTable;",
        "",
    ]
    lines += [f"void {fname(a)}__orig(Cpu *c);" for a in addrs]
    lines += ["", f"static SwHookSlot swmod_slots[{max(n, 1)}] = {{"]
    lines += [f"    {{0x{a:x}ull, {fname(a)}__orig, 0, 0}}," for a in addrs] or ["    {0, 0, 0, 0},"]
    lines += [
        "};",
        "",
        f"__declspec(dllexport) const SwHookTable *{EXPORT}(void) {{",
        f'    static const SwHookTable t = {{{HOOK_ABI}, {n}, swmod_slots, "gen_hooks {FORMAT}"}};',
        "    return &t;",
        "}",
    ]
    for i, a in enumerate(addrs):
        lines += [
            "",
            f"void {fname(a)}(Cpu *c) {{",
            f"    SwHookSlot *s = &swmod_slots[{i}];",
            "    SwHookEntry e = __atomic_load_n(&s->entry, __ATOMIC_ACQUIRE);",
            "    if (e && e(c, s)) return;",
            f"    __attribute__((musttail)) return {fname(a)}__orig(c);",
            "}",
        ]
    return "\n".join(lines) + "\n"


@dataclass
class Report:
    hooked: dict[int, str] = field(default_factory=dict)  # address -> the chunk file that defines it
    hooks_c: str = ""                                      # the file written
    self_jumps: dict[int, int] = field(default_factory=dict)  # address -> jumps back to its own start (self_jumps())


class HookPass:
    """Applies the hooks to a folder while the generator is still writing it: chunk() on each chunk file as soon as it
    is complete (before it is compiled), then finish() once the table is written. sw_rebuild uses this so the
    compiler never waits for the whole module."""

    def __init__(self, addrs, module: str = "main"):
        self.addrs = sorted(set(addrs))
        self.module = module
        self.hooked: dict[int, str] = {}
        self.self_jumps: dict[int, int] = {}  # hooked functions that jump back to their own start: how often
        if not self.addrs:
            raise HookError("no addresses to hook")

    def chunk(self, path: str) -> set[int]:
        text, found = _rewrite_file(path, self.addrs, self.module)
        for a in found:
            if a in self.hooked and self.hooked[a] != os.path.basename(path):
                raise HookError(f"{fname(a)} is defined twice ({self.hooked[a]}, {os.path.basename(path)})")
            self.hooked[a] = os.path.basename(path)
            n = self_jumps(text, a)
            if n:
                self.self_jumps[a] = n
        return found

    def finish(self, outdir: str) -> Report:
        """Checks every address against the function table, then writes <module>_hooks.c. Raises HookError naming
        every address that can't be hooked."""
        bad = entry_problems(self.addrs, read_table(outdir, self.module))
        if not bad:
            bad = [f"{fname(a)} is in the function table, but no chunk defines it"
                   for a in self.addrs if a not in self.hooked]
        if bad:
            raise HookError("can't hook " + "; ".join(bad))
        path = os.path.join(outdir, hooks_file_name(self.module))
        text = hooks_c(self.addrs, self.module)
        try:
            same = _read(path) == text
        except OSError:
            same = False
        if not same:
            _write(path, text)
        return Report(dict(self.hooked), path, dict(self.self_jumps))


def check(outdir: str, addrs, module: str = "main") -> list[str]:
    """What apply would refuse (empty: every address is a function start)."""
    return entry_problems(sorted(set(addrs)), read_table(outdir, module))


def apply(outdir: str, addrs, module: str = "main") -> Report:
    """Add hook slots for addrs to a generated folder: checked first (nothing changes when an address is refused),
    then every chunk is rewritten and <module>_hooks.c written. Running it again with the same list changes
    nothing; with another list, only that list's functions stay hooked."""
    addrs = sorted(set(addrs))
    bad = check(outdir, addrs, module)
    if bad:
        raise HookError("can't hook " + "; ".join(bad))
    p = HookPass(addrs, module)
    for path in chunk_files(outdir, module):
        p.chunk(path)
    return p.finish(outdir)


def remove(outdir: str, module: str = "main") -> int:
    """Undo apply: every renamed definition gets its name back and <module>_hooks.c goes. Returns how many."""
    n = 0
    for path in chunk_files(outdir, module):
        text = _read(path)
        n += len(_HOOKED_RE.findall(text))
        new, _found = rewrite_chunk(text, (), module)
        if new != text:
            _write(path, new)
    hooks = os.path.join(outdir, hooks_file_name(module))
    if os.path.exists(hooks):
        os.remove(hooks)
    return n


# --- command line ------------------------------------------------------------------------------------------------

def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Add hook slots to Savage Wind's generated C for main (docs/hooks.md).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show", help="list the addresses in hooks files")
    s.add_argument("hooks", nargs="+")
    for name, what in (("check", "say whether the addresses are function starts"), ("apply", "add the hook slots")):
        p = sub.add_parser(name, help=what)
        p.add_argument("folder", help="the generated C of main (with main_table.c)")
        p.add_argument("hooks", nargs="+", help="hooks files (text or JSON)")
    r = sub.add_parser("remove", help="undo apply")
    r.add_argument("folder")
    return ap


def _safe_stdout() -> None:
    """Let print() take any path. Redirected to a file or a pipe, Windows Python writes stdout in the ANSI code page
    with strict errors, so a letter it lacks (a user folder with an L-stroke, say) ended the run in a
    UnicodeEncodeError that hid the real message; such letters now print as '?'. Left alone when the stream already
    has a lenient handler (as when another tool runs this in its own process)."""
    try:
        if sys.stdout.errors in ("strict", "surrogateescape"):
            sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


def main(argv: list[str] | None = None) -> int:
    _safe_stdout()
    a = parser().parse_args(argv)
    try:
        if a.cmd == "show":
            for addr in read_hooks_files(a.hooks):
                print(f"{addr:#x}")
            return 0
        if a.cmd == "remove":
            print(f"Removed {remove(a.folder)} hook(s) from {a.folder}")
            return 0
        addrs = read_hooks_files(a.hooks)
        if a.cmd == "check":
            bad = check(a.folder, addrs)
            for b in bad:
                print(f"no: {b}")
            print(f"{len(addrs) - len(bad)} of {len(addrs)} address(es) can be hooked")
            return 1 if bad else 0
        rep = apply(a.folder, addrs)
        print(f"Hooked {len(rep.hooked)} function(s); slots in {rep.hooks_c}")
        for addr, chunk in sorted(rep.hooked.items()):
            print(f"  {fname(addr)}  ({chunk})")
        if rep.self_jumps:
            print(self_jump_note(rep.self_jumps))
        return 0
    except HookError as e:
        print(f"ERROR {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
