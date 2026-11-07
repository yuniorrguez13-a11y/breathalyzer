#!/usr/bin/env python3
"""Run Atmosphere cheat codes (.txt) against Savage Wind, from outside the game.

    python tools/cheats.py list <cheats.txt>
    python tools/cheats.py run <cheats.txt> [--port 7777] [--enable NAME ...] [--all] [--hz 12] [--force]

Start the game with SAVAGEWIND_CONTROL=<port> first. Every tick runs the master code and the enabled cheats (in
file order) on a small virtual machine; memory goes through the game's control connection (tools/control.py).
Reads happen as needed (later opcodes depend on them), one 256-byte block per round trip, so the reads of one
tick can come from different frames. Writes are buffered and sent at the end of the tick, up to 64 per batch:
up to 64 writes land in the same frame, more spread over several frames.

The cheat format is Atmosphere's public one (docs/features/cheats.md in Atmosphere-NX/Atmosphere). This is an
independent implementation written from that description, not from Atmosphere's code.

Memory regions: Main and Aslr = 0x7000000000 (Savage Wind loads main there and has no separate ASLR base, so both
map to main), Heap = 0x7240000000, Alias = 0x7100000000, type 4 = absolute address. Main-relative cheats match the
console exactly; heap-relative ones may not, because Savage Wind lays out its heap its own way.

Groups: a line "// group: <name>" (a comment to Atmosphere) starts a group, and the cheats after it, up to the next
such line, belong to it. Each group runs as if it were its own cheat file: it may have its own master code, its
registers and saved registers start at 0 at the start of the group every tick, and it has its own static registers.
modman writes its merged file this way, one group per mod, so a mod's cheats never see another mod's registers. A
file without such a line is one program, as before.
"""
from __future__ import annotations

import argparse
import math
import re
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # works with `python -I` too
from control import (ALIAS_BASE, DEFAULT_PORT, GUEST_HI, GUEST_LO, HEAP_BASE, MAIN_BASE, Control, ControlError,  # noqa: E402
                     request_line)

TITLE_ID = "01007EF00011E000"
BUILD_190 = "cd57b23fa4bbad65"
try:
    from mod_inspect import KNOWN_BUILDS
except Exception:  # noqa: BLE001 - only used for nicer messages
    KNOWN_BUILDS = {BUILD_190: "1.9.0"}

M64 = (1 << 64) - 1
WIDTHS = (1, 2, 4, 8)
MAX_STEPS = 100_000          # opcodes one cheat may run per tick before we call it an endless loop
ATMOSPHERE_MAX_WORDS = 0x100
BATCH_MAX = 64               # the game queues at most 64 requests per connection (runtime/control.c MAX_PENDING)

# memory type -> (name, base). Savage Wind has no separate ASLR region: Aslr uses main's base.
REGIONS = {0: ("main", MAIN_BASE), 1: ("heap", HEAP_BASE), 2: ("alias", ALIAS_BASE), 3: ("aslr", MAIN_BASE),
           4: ("absolute", 0)}
COMPARE = {1: ">", 2: ">=", 3: "<", 4: "<=", 5: "==", 6: "!="}
OPENERS = {0x1, 0x8, 0xC0, 0xC4}   # opcodes that begin a conditional block

# Switch button bits used by opcodes 8 and C4
SWITCH_KEYS = {
    "A": 0x1, "B": 0x2, "X": 0x4, "Y": 0x8, "LSTICK": 0x10, "RSTICK": 0x20, "L": 0x40, "R": 0x80,
    "ZL": 0x100, "ZR": 0x200, "PLUS": 0x400, "MINUS": 0x800, "LEFT": 0x1000, "UP": 0x2000, "RIGHT": 0x4000,
    "DOWN": 0x8000, "LS_LEFT": 0x10000, "LS_UP": 0x20000, "LS_RIGHT": 0x40000, "LS_DOWN": 0x80000,
    "RS_LEFT": 0x100000, "RS_UP": 0x200000, "RS_RIGHT": 0x400000, "RS_DOWN": 0x800000,
    "SL": 0x1000000, "SR": 0x2000000, "SL_R": 0x4000000, "SR_R": 0x8000000,
}
KEY_ALIASES = {"+": "PLUS", "-": "MINUS", "LS": "LSTICK", "RS": "RSTICK", "DLEFT": "LEFT", "DUP": "UP",
               "DRIGHT": "RIGHT", "DDOWN": "DOWN"}
# Keyboard keys for --hotkeys (Windows virtual-key codes)
VK = {"SHIFT": 0x10, "CTRL": 0x11, "ALT": 0x12, "SPACE": 0x20, "TAB": 0x09, "ENTER": 0x0D, "ESC": 0x1B,
      "BACKSPACE": 0x08, "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28, "INSERT": 0x2D, "DELETE": 0x2E,
      "HOME": 0x24, "END": 0x23, "PAGEUP": 0x21, "PAGEDOWN": 0x22}
VK.update({chr(c): c for c in range(ord("A"), ord("Z") + 1)})
VK.update({str(d): 0x30 + d for d in range(10)})
VK.update({f"F{n}": 0x6F + n for n in range(1, 25)})
VK.update({f"NUMPAD{d}": 0x60 + d for d in range(10)})


def wmask(width: int) -> int:
    return (1 << (8 * width)) - 1


def key_names(mask: int) -> str:
    names = [n for n, b in SWITCH_KEYS.items() if mask & b]
    rest = mask & ~sum(SWITCH_KEYS.values())
    if rest:
        names.append(hex(rest))
    return "+".join(names) or "no keys"


class DecodeError(ValueError):
    """A cheat has an opcode we can't run."""


class MemoryAccessError(RuntimeError):
    """A read or write hit memory the game doesn't have mapped."""


class GameGone(RuntimeError):
    """The control connection closed or stopped answering."""


class VMError(RuntimeError):
    """A cheat did something that stops it (endless loop, loop end without start)."""


# ---------------------------------------------------------------------------------------------------------------
# Decoding

class Op:
    """One decoded instruction. `code` is 0x0-0xA, 0xC0-0xC4 or 0xFF0/0xFF1/0xFFF; `pos` is its first word index."""

    def __init__(self, code: int, pos: int, **fields):
        self.code = code
        self.pos = pos
        self.__dict__.update(fields)

    @property
    def name(self) -> str:
        return f"{self.code:X}"

    def __repr__(self):
        fields = {k: v for k, v in self.__dict__.items() if k not in ("code", "pos")}
        return f"Op({self.name} @{self.pos} {fields})"


def opcode_of(word: int) -> int:
    code = word >> 28
    if code >= 0xC:          # extended: two nibbles
        code = word >> 24
        if code >= 0xF0:     # double extended: three nibbles
            code = word >> 20
    return code


def _decode_one(words: list[int], i: int) -> tuple[Op, int]:
    w = words[i]
    code = opcode_of(w)
    where = f"opcode {code:X} at word {i + 1}"

    def n(k):  # k-th hex digit of the first word, 0 = leftmost
        return (w >> (28 - 4 * k)) & 0xF

    def get(count):
        if i + count > len(words):
            raise DecodeError(f"{where} is cut short")
        return words[i:i + count]

    def width(t):
        if t not in WIDTHS:
            raise DecodeError(f"{where}: width {t} isn't 1, 2, 4 or 8")
        return t

    def region(m):
        if m not in REGIONS:
            raise DecodeError(f"{where}: memory type {m} doesn't exist")
        return m

    def compare(c):
        if c not in COMPARE:
            raise DecodeError(f"{where}: comparison {c} doesn't exist")
        return c

    def value(ws, t):  # a static value: two words when the width is 8
        return (ws[0] << 32 | ws[1]) if t == 8 else ws[0]

    def bad(what):
        return DecodeError(f"{where}: {what}")

    if code == 0x0:      # 0TMR00AA AAAAAAAA VVVVVVVV (VVVVVVVV): store static value to memory
        t = width(n(1))
        ws = get(3 + (t == 8))
        return Op(code, i, t=t, m=region(n(2)), r=n(3), a=(w & 0xFF) << 32 | ws[1], v=value(ws[2:], t)), len(ws)
    if code == 0x1:      # 1TMCXrAA AAAAAAAA VVVVVVVV (VVVVVVVV): begin conditional on memory
        t = width(n(1))
        if n(4) > 1:
            raise bad(f"operand type {n(4)} doesn't exist")
        ws = get(3 + (t == 8))
        return Op(code, i, t=t, m=region(n(2)), c=compare(n(3)), x=n(4), r=n(5), a=(w & 0xFF) << 32 | ws[1],
                  v=value(ws[2:], t)), len(ws)
    if code == 0x2:      # 2X000000: end (X=0) or else (X=1)
        if n(1) > 1:
            raise bad(f"block end type {n(1)} doesn't exist")
        return Op(code, i, x=n(1)), 1
    if code == 0x3:      # 300R0000 VVVVVVVV: loop start; 310R0000: loop end
        if n(1) == 0:
            ws = get(2)
            return Op(code, i, start=True, r=n(3), v=ws[1]), 2
        if n(1) == 1:
            return Op(code, i, start=False, r=n(3)), 1
        raise bad(f"loop type {n(1)} doesn't exist")
    if code == 0x4:      # 400R0000 VVVVVVVV VVVVVVVV: load register with static value
        ws = get(3)
        return Op(code, i, r=n(3), v=ws[1] << 32 | ws[2]), 3
    if code == 0x5:      # 5TMRxSAA AAAAAAAA: load register from memory (x = 0..3)
        t = width(n(1))
        kind = n(4)
        if kind > 3:
            raise bad(f"load type {kind} doesn't exist")
        ws = get(2)
        m = region(n(2)) if kind in (0, 3) else 0
        return Op(code, i, t=t, m=m, r=n(3), kind=kind, s=n(5), a=(w & 0xFF) << 32 | ws[1]), 2
    if code == 0x6:      # 6T0RIor0 VVVVVVVV VVVVVVVV: store static value to register address
        t = width(n(1))
        ws = get(3)
        return Op(code, i, t=t, r=n(3), inc=n(4) != 0, use_off=n(5) != 0, ro=n(6), v=ws[1] << 32 | ws[2]), 3
    if code == 0x7:      # 7T0RC000 VVVVVVVV: legacy arithmetic
        t = width(n(1))
        if n(4) > 4:
            raise bad(f"legacy math type {n(4)} doesn't exist")
        ws = get(2)
        return Op(code, i, t=t, r=n(3), c=n(4), v=ws[1]), 2
    if code == 0x8:      # 8kkkkkkk: begin keypress conditional
        return Op(code, i, mask=w & 0x0FFFFFFF, repeat=True), 1
    if code == 0x9:      # 9TCRS0s0 or 9TCRS100 VVVVVVVV (VVVVVVVV): arithmetic
        t = width(n(1))
        c = n(2)
        if c > 13:
            raise bad(f"math type {c} doesn't exist")
        if c >= 10 and t not in (4, 8):
            raise bad("float math needs width 4 or 8")
        if n(5) == 0:
            return Op(code, i, t=t, c=c, r=n(3), s=n(4), imm=None, s2=n(6)), 1
        if n(5) == 1:
            ws = get(2 + (t == 8))
            return Op(code, i, t=t, c=c, r=n(3), s=n(4), imm=value(ws[1:], t), s2=0), len(ws)
        raise bad(f"operand type {n(5)} doesn't exist")
    if code == 0xA:      # ATSRIOxa (aaaaaaaa): store register to memory
        t = width(n(1))
        o = n(5)
        if o > 5:
            raise bad(f"offset type {o} doesn't exist")
        a, size = 0, 1
        if o in (2, 4, 5):
            ws = get(2)
            a, size = n(7) << 32 | ws[1], 2
        m = region(n(6)) if o in (3, 4, 5) else 0
        return Op(code, i, t=t, s=n(2), r=n(3), inc=n(4) != 0, o=o, ro=n(6), m=m, a=a), size
    if code in (0xC0, 0xFFF):
        # C0TcSX..: begin register conditional; FFFTIX..: debug log. Both read one operand of type X.
        if code == 0xC0:
            t, extra = width(n(2)), {"c": compare(n(3)), "s": n(4)}
        else:
            t, extra = width(n(3)), {"log": n(4)}
        x = n(5)
        if code == 0xFFF:
            if x > 4:
                raise bad(f"operand type {x} doesn't exist")
            if x == 4:
                x = 5    # FFF's type 4 is "register value", C0's type 5
        if x in (0, 2):
            ws = get(2)
            f = {"m": region(n(6))} if x == 0 else {"rr": n(6)}
            return Op(code, i, t=t, x=x, a=n(7) << 32 | ws[1], **f, **extra), 2
        if x == 1:
            return Op(code, i, t=t, x=x, m=region(n(6)), ro=n(7), **extra), 1
        if x == 3:
            return Op(code, i, t=t, x=x, rr=n(6), ro=n(7), **extra), 1
        if x == 4 and code == 0xC0:
            ws = get(2 + (t == 8))
            return Op(code, i, t=t, x=x, v=value(ws[1:], t), **extra), len(ws)
        if x == 5:
            return Op(code, i, t=t, x=x, other=n(6), **extra), 1
        raise bad(f"operand type {n(5)} doesn't exist")
    if code in (0xC1, 0xC2):  # C10D0Sx0 / C2x0XXXX: save or restore registers
        x = n(6) if code == 0xC1 else n(2)
        if x > 3:
            raise bad(f"save/restore type {x} doesn't exist")
        if code == 0xC1:
            return Op(code, i, x=x, d=n(3), s=n(5)), 1
        return Op(code, i, x=x, mask=w & 0xFFFF), 1
    if code == 0xC3:     # C3000XXx: read or write a static register
        return Op(code, i, idx=(w >> 4) & 0xFF, r=n(7)), 1
    if code == 0xC4:     # C4r00000 kkkkkkkk kkkkkkkk: begin extended keypress conditional
        ws = get(3)
        return Op(code, i, mask=ws[1] << 32 | ws[2], repeat=n(2) != 0), 3
    if code in (0xFF0, 0xFF1):  # pause / resume the game
        return Op(code, i), 1
    raise DecodeError(f"unknown opcode {code:X} at word {i + 1}")


def decode(words: list[int]) -> list[Op]:
    ops, i = [], 0
    while i < len(words):
        op, size = _decode_one(words, i)
        ops.append(op)
        i += size
    return ops


# ---------------------------------------------------------------------------------------------------------------
# Parsing cheat files

HEX8 = re.compile(r"[0-9A-Fa-f]{8}")
COMMENT = ("#", "//", ";")
GROUP_RE = re.compile(r"//\s*group:(.*)", re.I)   # a whole line "// group: <name>" starts a group (see the top)


@dataclass
class Cheat:
    name: str
    master: bool = False
    line: int = 0
    words: list[int] = field(default_factory=list)
    ops: list[Op] = field(default_factory=list)
    error: str | None = None
    notes: list[str] = field(default_factory=list)
    enabled: bool = False
    partial: bool = False
    group: int = 0             # 0 before any "// group:" line, then 1, 2, ... in file order
    group_name: str = ""

    @property
    def status(self) -> str:
        if self.error:
            return "refused"
        if not self.ops:
            return "empty"
        return "partial" if self.partial else "ok"

    @property
    def runnable(self) -> bool:
        return self.status in ("ok", "partial")

    @property
    def opcodes(self) -> list[str]:
        seen = []
        for op in self.ops:
            if op.name not in seen:
                seen.append(op.name)
        return seen


@dataclass
class CheatFile:
    path: str
    cheats: list[Cheat]
    warnings: list[str] = field(default_factory=list)
    build_id: str | None = None

    @property
    def master(self) -> Cheat | None:
        return next((c for c in self.cheats if c.master), None)

    @property
    def masters(self) -> list[Cheat]:
        """Every {master} section in file order. Only the first (of each group) may run; the others are refused."""
        return [c for c in self.cheats if c.master]

    @property
    def grouped(self) -> bool:
        """Whether the file has "// group:" lines (see the top of this file)."""
        return any(c.group for c in self.cheats)

    def groups(self) -> list[tuple[int, str]]:
        """(group number, name) of the groups that have cheats or a master code, in file order."""
        return list(dict.fromkeys((c.group, c.group_name) for c in self.cheats))

    def numbered(self) -> list[tuple[int, Cheat]]:
        """Non-master cheats with code, numbered from 1 in file order (the numbers `list` shows)."""
        out = []
        for c in self.cheats:
            if not c.master and (c.words or c.error):
                out.append((len(out) + 1, c))
        return out


def _name_end(line: str) -> int:
    """Index of the bracket that closes line[0] ('[' or '{'), counting nested pairs, so "[A [v2]] // x" ends
    after "[v2]" and "[A] // needs [M]" ends after "A". Unbalanced names end at the last closing bracket."""
    opener = line[0]
    close = "]" if opener == "[" else "}"
    depth = 0
    for k, c in enumerate(line):
        if c == opener:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return k
    return line.rfind(close)


def parse_text(text: str) -> tuple[list[Cheat], list[str]]:
    """Split a cheat file into cheats. Bad code lines refuse their cheat; stray text only warns. A "// group:" line
    ends the cheat before it and starts a group (each group may have one master code)."""
    cheats, warnings = [], []
    cur = None
    has_master = False
    group, group_name = 0, ""
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip().lstrip("﻿")
        m = GROUP_RE.fullmatch(line)
        if m:
            group, group_name = group + 1, m.group(1).strip()
            cur, has_master = None, False
            continue
        while line:
            if line[0] in "[{":
                close = "]" if line[0] == "[" else "}"
                end = _name_end(line)
                if end < 0:
                    warnings.append(f"line {lineno}: '{line[0]}' without '{close}', ignored")
                    break
                name = line[1:end].strip()
                cur = Cheat(name, master=line[0] == "{", line=lineno, group=group, group_name=group_name)
                if cur.master:
                    if has_master:
                        cur.error = ("a second master code; only one is allowed" if not group else
                                     "a second master code in its group; only one is allowed per group")
                    has_master = True
                cheats.append(cur)
                line = line[end + 1:].strip()
                continue
            tokens = line.split()
            if not HEX8.fullmatch(tokens[0]):
                if not line.startswith(COMMENT):
                    warnings.append(f"line {lineno}: ignored, not a cheat name or code: {line[:40]}")
                break
            if cur is None:
                warnings.append(f"line {lineno}: code before any [cheat name], ignored")
                break
            for tok in tokens:
                if tok.startswith(COMMENT):
                    break
                if not HEX8.fullmatch(tok):
                    if not cur.error:
                        cur.error = f"line {lineno}: '{tok[:20]}' isn't an 8-digit hex word"
                    break
                cur.words.append(int(tok, 16))
            break
    for c in cheats:
        if c.error:
            continue
        try:
            c.ops = decode(c.words)
        except DecodeError as e:
            c.error = str(e)
            c.ops = []
    return cheats, warnings


def parse_toggles(text: str) -> dict[str, bool]:
    """Atmosphere's toggles.txt: a [Cheat Name] line followed by true/false."""
    out, name = {}, None
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
        elif name is not None and line.lower() in ("true", "on", "1", "false", "off", "0"):
            out[name] = line.lower() in ("true", "on", "1")
            name = None
    return out


def build_warnings(path: Path) -> tuple[str | None, list[str]]:
    """Atmosphere names cheat files <first 16 hex digits of the build id>.txt under contents/<title id>/cheats."""
    warnings = []
    build = path.stem.lower() if re.fullmatch(r"[0-9A-Fa-f]{16}", path.stem) else None
    if build and build != BUILD_190:
        version = KNOWN_BUILDS.get(build)
        what = f"BotW {version}" if version else "another game version"
        warnings.append(f"{path.name} is for {what} (build {build}), not 1.9.0 ({BUILD_190}). "
                        "Its addresses will likely be wrong in Savage Wind.")
    parts = [p.lower() for p in path.parts]
    if "cheats" in parts:
        k = len(parts) - 1 - parts[::-1].index("cheats")
        tid = parts[k - 1] if k >= 1 else ""
        if re.fullmatch(r"[0-9a-f]{16}", tid) and tid != TITLE_ID.lower():
            warnings.append(f"the folder says title {tid.upper()}, not BotW ({TITLE_ID}).")
    return build, warnings


def analyse(cheat: Cheat, *, hotkeys: "Hotkeys | None" = None,
            main_text: tuple[int, int] | None = None) -> None:
    """Fill in notes and the partial flag for a decoded cheat."""
    cheat.notes, cheat.partial = [], False
    if cheat.error or not cheat.ops:
        return
    codes = {op.code for op in cheat.ops}
    for op in cheat.ops:
        if op.code in (0x8, 0xC4) and not (hotkeys and hotkeys.covers(op.mask)):
            cheat.partial = True
            note = f"needs keys {key_names(op.mask)}: never pressed unless you map them with --hotkeys"
            if note not in cheat.notes:
                cheat.notes.append(note)
    if codes & {0xFF0, 0xFF1}:
        cheat.notes.append("pause/resume is ignored: the game keeps running")
    if 0xFFF in codes:
        cheat.notes.append("debug log values print here when they change")
    if any(op.code == 0xC3 and op.idx < 0x80 for op in cheat.ops):
        cheat.notes.append("reads static registers, which stay 0 here")
    if any(getattr(op, "m", None) == 1 and _uses_region(op) for op in cheat.ops):
        cheat.notes.append("uses heap offsets, which may not match Savage Wind's heap")
    if len(cheat.words) > ATMOSPHERE_MAX_WORDS:
        cheat.notes.append(f"{len(cheat.words)} words, more than Atmosphere's limit of 256 (runs here anyway)")
    if main_text:
        writes = code_writes = 0
        for op in cheat.ops:
            if op.code in (0x0, 0x6, 0xA):
                writes += 1
                fixed = None
                if op.code == 0x0 and op.m in (0, 3):
                    fixed = REGIONS[op.m][1] + op.a
                elif op.code == 0xA and op.o == 4 and op.m in (0, 3):
                    fixed = REGIONS[op.m][1] + op.a
                if fixed is not None and main_text[0] <= fixed < main_text[1]:
                    code_writes += 1
        if code_writes and code_writes == writes:
            cheat.error = "patches game code only; that does nothing in Savage Wind (the game code is recompiled)"
        elif code_writes:
            cheat.partial = True
            cheat.notes.append("some writes patch game code; those do nothing in Savage Wind and are skipped")


def _uses_region(op: Op) -> bool:
    if op.code in (0x0, 0x1):
        return True
    if op.code == 0x5:
        return op.kind in (0, 3)
    if op.code == 0xA:
        return op.o in (3, 4, 5)
    if op.code in (0xC0, 0xFFF):
        return op.x in (0, 1)
    return False


def load(path, *, hotkeys: "Hotkeys | None" = None, main_text: tuple[int, int] | None = None) -> CheatFile:
    path = Path(path)
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    cheats, warnings = parse_text(text)
    build, bw = build_warnings(path)
    toggles_path = path.with_name("toggles.txt")
    toggles = {}
    if toggles_path.is_file() and toggles_path != path:
        toggles = parse_toggles(toggles_path.read_bytes().decode("utf-8-sig", errors="replace"))
    for c in cheats:
        analyse(c, hotkeys=hotkeys, main_text=main_text)
        c.enabled = c.master or toggles.get(c.name, False)
    return CheatFile(str(path), cheats, bw + warnings, build)


def main_text_range(path) -> tuple[int, int]:
    """Where main's code (.text) sits in Savage Wind, from the NSO header of exefs/main (reads 256 bytes only)."""
    import nso
    p = Path(path)
    if p.is_dir():
        p = p / "main"
    with open(p, "rb") as f:
        head = f.read(nso.HEADER_SIZE)
    if len(head) < nso.HEADER_SIZE or head[:4] != nso.MAGIC:
        raise ValueError(f"{p} isn't an NSO file")
    mem_off, size = struct.unpack_from("<II", head, 0x14)   # .text: memory offset, size
    return MAIN_BASE + mem_off, MAIN_BASE + mem_off + size


# ---------------------------------------------------------------------------------------------------------------
# Memory backends

class Memory:
    """Where the VM reads and writes. `tag` names the cheat running now (for error messages)."""
    tag: str | None = None

    def read(self, addr: int, width: int) -> int:
        raise NotImplementedError

    def write(self, addr: int, width: int, value: int) -> None:
        raise NotImplementedError

    def flush(self) -> list[tuple[str | None, int, str]]:
        """End of a tick: send buffered writes. Returns (cheat name, address, error) for writes that failed."""
        return []


class FakeMemory(Memory):
    """Dict-backed memory for tests. `regions` = [(start, end), ...] limits what is mapped (default: all)."""

    def __init__(self, regions: list[tuple[int, int]] | None = None):
        self.bytes: dict[int, int] = {}
        self.regions = regions
        self.log: list[tuple[str, int, int, int]] = []   # ("r"/"w", addr, width, value)

    def _check(self, addr, width):
        if self.regions is not None and not any(lo <= addr and addr + width <= hi for lo, hi in self.regions):
            raise MemoryAccessError(f"0x{addr:X} isn't mapped")

    def read(self, addr, width):
        self._check(addr, width)
        value = int.from_bytes(bytes(self.bytes.get(addr + k, 0) for k in range(width)), "little")
        self.log.append(("r", addr, width, value))
        return value

    def write(self, addr, width, value):
        self._check(addr, width)
        for k, b in enumerate((value & wmask(width)).to_bytes(width, "little")):
            self.bytes[addr + k] = b
        self.log.append(("w", addr, width, value & wmask(width)))

    # test helpers
    def get(self, addr, width=4):
        return int.from_bytes(bytes(self.bytes.get(addr + k, 0) for k in range(width)), "little")

    def put(self, addr, width, value):
        for k, b in enumerate((value & wmask(width)).to_bytes(width, "little")):
            self.bytes[addr + k] = b


_LINK_ERRORS = ("the game closed the connection", "the game didn't answer in time", "invalid reply from the game",
                "can't connect to the game")


def _link_lost(e: Exception) -> bool:
    return isinstance(e, OSError) or str(e).startswith(_LINK_ERRORS)


class ControlMemory(Memory):
    """Memory over Savage Wind's control connection (tools/control.py).

    Reads fetch whole 256-byte blocks (`mem read <addr> 256 hex`) and keep them for the rest of the tick, so
    reading a block again in the same tick gives the same bytes and costs nothing. Every new block is its own
    round trip and frame, so different blocks of one tick can come from different frames. Writes are buffered,
    later reads in the same tick see them, and flush() sends them in batches of up to BATCH_MAX (64): the writes
    of one batch run in the same frame, each further batch in a later one.
    """

    BLOCK = 256   # divides the 4 KiB page, so a block is either all readable or not at all

    def __init__(self, control: Control):
        self.control = control
        self._cache: dict[int, bytes | str] = {}
        self._pending: dict[tuple[int, int], str | None] = {}
        self._overlay: dict[int, int] = {}
        self.round_trips = 0
        self.batches = 0

    @staticmethod
    def _check(addr, width):
        if addr < GUEST_LO or addr + width > GUEST_HI:
            raise MemoryAccessError(f"0x{addr:X} is outside the game's memory")

    def _block(self, start: int) -> bytes:
        got = self._cache.get(start)
        if got is None:
            try:
                data = self.control.cmd("mem", "read", hex(start), str(self.BLOCK), "hex")
                got = bytes.fromhex(data["hex"])
                if len(got) != self.BLOCK:
                    got = f"short read at 0x{start:X}"
            except (ControlError, OSError) as e:
                if _link_lost(e):
                    raise GameGone(_gone_message(e)) from None
                got = str(e)
            except (KeyError, ValueError) as e:
                got = f"odd reply: {e}"
            self.round_trips += 1
            self._cache[start] = got
        if isinstance(got, str):
            raise MemoryAccessError(f"0x{start:X}: {got}")
        return got

    def read(self, addr, width):
        self._check(addr, width)
        out = bytearray(width)
        if any(addr + k not in self._overlay for k in range(width)):
            a, end = addr, addr + width
            while a < end:
                start = a - a % self.BLOCK
                take = min(end, start + self.BLOCK) - a
                out[a - addr:a - addr + take] = self._block(start)[a - start:a - start + take]
                a += take
        for k in range(width):
            b = self._overlay.get(addr + k)
            if b is not None:
                out[k] = b
        return int.from_bytes(out, "little")

    def write(self, addr, width, value):
        self._check(addr, width)
        key = (addr, width)
        self._pending.pop(key, None)
        self._pending[key] = self.tag
        for k, b in enumerate((value & wmask(width)).to_bytes(width, "little")):
            self._overlay[addr + k] = b

    def flush(self):
        items, overlay = list(self._pending.items()), self._overlay
        self._pending, self._overlay, self._cache = {}, {}, {}
        failures = []
        # Each (address, width) once, with its final bytes: same end result as replaying every write in order.
        reqs = [["mem", "write", hex(a), f"u{8 * w}",
                 str(int.from_bytes(bytes(overlay[a + k] for k in range(w)), "little"))] for (a, w), _ in items]
        for start in range(0, len(reqs), BATCH_MAX):
            replies = self._batch(reqs[start:start + BATCH_MAX])
            for ((addr, _), tag), reply in zip(items[start:start + BATCH_MAX], replies):
                if isinstance(reply, ControlError):
                    failures.append((tag, addr, str(reply)))
        return failures

    def _batch(self, requests) -> list:
        """Like Control.batch(errors=True), but gives up at once when the connection is lost."""
        self.batches += 1
        self.round_trips += 1
        try:
            self.control.sock.sendall(b"".join(request_line(r) for r in requests))
        except OSError as e:
            raise GameGone(_gone_message(e)) from None
        out = []
        for _ in requests:
            try:
                out.append(self.control._reply())
            except (ControlError, OSError) as e:
                if _link_lost(e):
                    raise GameGone(_gone_message(e)) from None
                out.append(e if isinstance(e, ControlError) else ControlError(str(e)))
        return out


def _gone_message(e: Exception) -> str:
    text = str(e)
    if "didn't answer" in text:
        return "The game stopped answering"
    if isinstance(e, OSError) or "closed" in text:
        return "The game closed the connection"
    return f"Lost the game: {text}"


# ---------------------------------------------------------------------------------------------------------------
# Hotkeys (optional: opcode 8/C4 can't see the game's real buttons)

def parse_hotkeys(spec: str) -> dict[int, int]:
    """'ZL=SHIFT,A=J' -> {switch bit: virtual-key code}."""
    out = {}
    for part in filter(None, (p.strip() for p in re.split(r"[,;]", spec))):
        if "=" not in part:
            raise ValueError(f"'{part}' should look like BUTTON=KEY, for example ZL=SHIFT")
        button, key = (s.strip().upper() for s in part.split("=", 1))
        button = KEY_ALIASES.get(button, button)
        if button not in SWITCH_KEYS:
            raise ValueError(f"unknown Switch button '{button}' (use {', '.join(SWITCH_KEYS)})")
        if key in VK:
            vk = VK[key]
        elif re.fullmatch(r"0X[0-9A-F]{1,2}", key):
            vk = int(key, 16)
        else:
            raise ValueError(f"unknown keyboard key '{key}' (letters, digits, F1-F24, SHIFT, CTRL, ALT, SPACE, "
                             "arrows, NUMPAD0-9, or a hex code like 0x70)")
        out[SWITCH_KEYS[button]] = vk
    return out


def windows_key_reader():
    if sys.platform != "win32":
        raise ValueError("--hotkeys works only on Windows")
    import ctypes
    get = ctypes.WinDLL("user32").GetAsyncKeyState
    get.argtypes, get.restype = [ctypes.c_int], ctypes.c_short
    return lambda vk: bool(get(vk) & 0x8000)


class Hotkeys:
    """Switch buttons mapped to keyboard keys; poll() once per tick, then `held` is the Switch button mask."""

    def __init__(self, mapping: dict[int, int], reader=None):
        self.mapping = mapping
        self.reader = reader or windows_key_reader()
        self.held = 0

    def covers(self, mask: int) -> bool:
        return all(mask & b == 0 or b in self.mapping for b in (1 << k for k in range(64)))

    def poll(self) -> None:
        self.held = 0
        for bit, vk in self.mapping.items():
            if self.reader(vk):
                self.held |= bit


# ---------------------------------------------------------------------------------------------------------------
# The virtual machine

def _f32(bits):
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


def _f32_bits(x):
    try:
        return struct.unpack("<I", struct.pack("<f", x))[0]
    except OverflowError:   # too big for a float: IEEE gives infinity
        return 0x7F800000 if x > 0 else 0xFF800000


def _f64(bits):
    return struct.unpack("<d", struct.pack("<Q", bits & M64))[0]


def _f64_bits(x):
    return struct.unpack("<Q", struct.pack("<d", x))[0]


def _fdiv(a, b):
    try:
        return a / b
    except ZeroDivisionError:
        if a == 0 or math.isnan(a):
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)


def arith(c: int, t: int, a: int, b: int) -> int:
    """Opcode 7/9 math on 64-bit values, result cut to `t` bytes. Shifts use the count mod 64 (as ARM64 does)."""
    if c >= 10:
        if t == 4:
            fa, fb = _f32(a), _f32(b)
        else:
            fa, fb = _f64(a), _f64(b)
        r = (fa + fb if c == 10 else fa - fb if c == 11 else fa * fb if c == 12 else _fdiv(fa, fb))
        return _f32_bits(r) if t == 4 else _f64_bits(r)
    if c == 0:
        r = a + b
    elif c == 1:
        r = a - b
    elif c == 2:
        r = a * b
    elif c == 3:
        r = a << (b & 63)
    elif c == 4:
        r = a >> (b & 63)
    elif c == 5:
        r = a & b
    elif c == 6:
        r = a | b
    elif c == 7:
        r = ~a
    elif c == 8:
        r = a ^ b
    else:  # 9: move
        r = a
    return r & M64 & wmask(t)


def _compare(c: int, a: int, b: int) -> bool:
    return (a > b if c == 1 else a >= b if c == 2 else a < b if c == 3 else a <= b if c == 4
            else a == b if c == 5 else a != b)


class CheatVM:
    """Runs cheats one tick at a time.

    Like Atmosphere, the master code runs first and then the other cheats in the order given (select() gives file
    order), as one program per tick: the 16 registers carry over from one cheat to the next, and they and the saved
    registers start at 0 every tick. Static registers (C3) keep their values between ticks.

    The other cheats usually depend on registers the master code sets, so when a master code can't run (refused
    or stopped), the other cheats stop too instead of writing to the wrong addresses.

    Cheats of a file with "// group:" lines run group by group (in the order the groups first appear), each group
    as its own program: its master first, registers and saved registers at 0 at the start of the group, its own
    static registers, and a master that can't run stops only its own group.
    """

    def __init__(self, memory: Memory, *, hotkeys: Hotkeys | None = None, say=None,
                 main_text: tuple[int, int] | None = None, max_steps: int = MAX_STEPS):
        self.mem = memory
        self.hotkeys = hotkeys
        self.say = say or (lambda text: print(text, flush=True))
        self.main_text = main_text
        self.max_steps = max_steps
        self.reg = [0] * 16
        self.saved = [0] * 16
        self.static = [0] * 256               # (group 0's: the only ones of a file without groups)
        self._statics: dict[int, list[int]] = {}   # group number -> its static registers (groups 1, 2, ...)
        self.stopped: dict[int, str] = {}     # id(cheat) -> why
        self._said: set = set()
        self._latch: dict = {}                 # C4 once-per-press state
        self._logs: dict = {}                  # FFF last values
        self._cheat: Cheat | None = None

    # -- helpers --

    def _once(self, key, text):
        name = self._cheat.name if self._cheat else ""
        if (name, key) not in self._said:
            self._said.add((name, key))
            self.say(f"{name}: {text}" if name else text)

    def _read(self, addr, t):
        addr &= M64
        try:
            return self.mem.read(addr, t)
        except MemoryAccessError:
            self._once("read", f"can't read 0x{addr:X} (not loaded yet?). Reads there give 0; it keeps trying.")
            return 0

    def _write(self, addr, t, value):
        addr &= M64
        if self.main_text and self.main_text[0] <= addr < self.main_text[1]:
            self._once("code", f"patches game code at 0x{addr:X}; that does nothing in Savage Wind "
                               "(the game code is recompiled), so those writes are skipped.")
            return
        try:
            self.mem.write(addr, t, value & wmask(t))
        except MemoryAccessError:
            self._once("write", f"can't write 0x{addr:X} (not loaded yet?). It keeps trying.")

    def _keys(self, op) -> bool:
        held = self.hotkeys.held if self.hotkeys else 0
        match = held & op.mask == op.mask
        if op.repeat:
            return match
        key = (id(self._cheat), op.pos)
        before = self._latch.get(key, False)
        self._latch[key] = match
        return match and not before

    def _operand(self, op) -> int:
        """The value of a C0/FFF operand (types 0-5)."""
        reg, x = self.reg, op.x
        if x == 0:
            return self._read(REGIONS[op.m][1] + op.a, op.t)
        if x == 1:
            return self._read(REGIONS[op.m][1] + reg[op.ro], op.t)
        if x == 2:
            return self._read(reg[op.rr] + op.a, op.t)
        if x == 3:
            return self._read(reg[op.rr] + reg[op.ro], op.t)
        if x == 4:
            return op.v
        return reg[op.other]

    @staticmethod
    def _skip(ops, ip, allow_else) -> int:
        """Index after the End (or, with allow_else, the Else) that closes the block we are in."""
        depth = 0
        while ip < len(ops):
            op = ops[ip]
            ip += 1
            if op.code in OPENERS:
                depth += 1
            elif op.code == 0x2:
                if op.x == 0:
                    if depth == 0:
                        return ip
                    depth -= 1
                elif depth == 0 and allow_else:
                    return ip
        return ip

    # -- running --

    def tick(self, cheats: list[Cheat]) -> list[tuple[str | None, int, str]]:
        """Run every cheat once (masters first, then the rest in the order given), then send the tick's writes.

        If a master code is refused or stopped, the other cheats are stopped instead of run. Returns failed writes.
        With groups, that is done for each group in turn, from fresh registers (see the class).
        """
        self.reg = [0] * 16
        self.saved = [0] * 16
        if self.hotkeys:
            self.hotkeys.poll()
        groups: dict[int, list[Cheat]] = {}
        for c in cheats:
            groups.setdefault(c.group, []).append(c)
        base = self.static
        try:
            for g, members in groups.items():
                self.reg = [0] * 16
                self.saved = [0] * 16
                self.static = self._statics.setdefault(g, [0] * 256) if g else base
                self._run_group(members)
        finally:
            self.static = base
        self._cheat = None
        self.mem.tag = None
        failures = self.mem.flush()
        for tag, addr, err in failures:
            if (tag, "flush") not in self._said:
                self._said.add((tag, "flush"))
                self.say(f"{tag}: can't write 0x{addr:X} ({err}). It keeps trying.")
        return failures

    def _run_group(self, cheats: list[Cheat]) -> None:
        """One program: its masters, then the others unless a master is refused or stopped."""
        masters = [c for c in cheats if c.master]
        others = [c for c in cheats if not c.master]
        for cheat in masters:
            self._run_one(cheat)
        down = next((m for m in masters if m.error or id(m) in self.stopped), None)
        if down is not None:
            newly = [c for c in others if id(c) not in self.stopped]
            for c in newly:
                self.stopped[id(c)] = "the master code stopped"
            if newly:
                where = f" of group {down.group_name or '(no name)'}" if down.group else ""
                self.say(f"{len(newly)} other cheat(s) stopped too: they depend on the master code "
                         f"({down.name or 'no name'}){where}.")
        else:
            for cheat in others:
                self._run_one(cheat)

    def _run_one(self, cheat: Cheat) -> None:
        if id(cheat) in self.stopped or not cheat.ops or cheat.error:
            return
        self._cheat = cheat
        self.mem.tag = cheat.name
        try:
            self.run(cheat)
        except VMError as e:
            self.stopped[id(cheat)] = str(e)
            self.say(f"{cheat.name}: stopped, {e}")

    def run(self, cheat: Cheat) -> None:
        ops, reg = cheat.ops, self.reg
        ip, steps, loop_top = 0, 0, {}
        while ip < len(ops):
            steps += 1
            if steps > self.max_steps:
                raise VMError(f"more than {self.max_steps} steps in one tick (endless loop?)")
            op = ops[ip]
            ip += 1
            c = op.code
            if c == 0x0:
                self._write(REGIONS[op.m][1] + op.a + reg[op.r], op.t, op.v)
            elif c == 0x1:
                addr = REGIONS[op.m][1] + op.a + (reg[op.r] if op.x == 1 else 0)
                m = wmask(op.t)
                if not _compare(op.c, self._read(addr, op.t) & m, op.v & m):
                    ip = self._skip(ops, ip, True)
            elif c == 0x2:
                if op.x == 1:          # reached Else after running the "if" part: skip to End
                    ip = self._skip(ops, ip, False)
            elif c == 0x3:
                if op.start:
                    reg[op.r] = op.v
                    loop_top[op.r] = ip
                else:
                    if op.r not in loop_top:
                        raise VMError(f"loop end (word {op.pos + 1}) without a loop start for register {op.r:X}")
                    reg[op.r] = (reg[op.r] - 1) & M64
                    if reg[op.r]:
                        ip = loop_top[op.r]
            elif c == 0x4:
                reg[op.r] = op.v
            elif c == 0x5:
                k = op.kind
                addr = (REGIONS[op.m][1] + op.a if k == 0 else reg[op.r] + op.a if k == 1
                        else reg[op.s] + op.a if k == 2 else REGIONS[op.m][1] + reg[op.s] + op.a)
                reg[op.r] = self._read(addr, op.t)
            elif c == 0x6:
                addr = reg[op.r] + (reg[op.ro] if op.use_off else 0)
                self._write(addr, op.t, op.v)
                if op.inc:
                    reg[op.r] = (reg[op.r] + op.t) & M64
            elif c == 0x7:
                reg[op.r] = arith(op.c, op.t, reg[op.r], op.v)
            elif c in (0x8, 0xC4):
                if not self._keys(op):
                    ip = self._skip(ops, ip, True)
            elif c == 0x9:
                rhs = reg[op.s2] if op.imm is None else op.imm
                reg[op.r] = arith(op.c, op.t, reg[op.s], rhs)
            elif c == 0xA:
                o = op.o
                base = REGIONS[op.m][1]
                addr = (reg[op.r] if o == 0 else reg[op.r] + reg[op.ro] if o == 1 else reg[op.r] + op.a if o == 2
                        else base + reg[op.r] if o == 3 else base + op.a if o == 4 else base + reg[op.r] + op.a)
                self._write(addr, op.t, reg[op.s])
                if op.inc:
                    reg[op.r] = (reg[op.r] + op.t) & M64
            elif c == 0xC0:
                m = wmask(op.t)
                if not _compare(op.c, reg[op.s] & m, self._operand(op) & m):
                    ip = self._skip(ops, ip, True)
            elif c == 0xC1:
                self._save_restore(op.x, op.d, op.s)
            elif c == 0xC2:
                for k in range(16):
                    if op.mask >> k & 1:
                        self._save_restore(op.x, k, k)
            elif c == 0xC3:
                if op.idx < 0x80:
                    reg[op.r] = self.static[op.idx]
                else:
                    self.static[op.idx] = reg[op.r]
            elif c in (0xFF0, 0xFF1):
                pass    # pause/resume: ignored, the game keeps running
            elif c == 0xFFF:
                value = self._operand(op) & wmask(op.t)
                key = (id(self._cheat), op.pos)
                if self._logs.get(key) != value:
                    self._logs[key] = value
                    self.say(f"{self._cheat.name}: log {op.log:X} = 0x{value:X} ({value})")

    def _save_restore(self, x, d, s):
        if x == 0:
            self.reg[d] = self.saved[s]
        elif x == 1:
            self.saved[d] = self.reg[s]
        elif x == 2:
            self.saved[d] = 0
        else:
            self.reg[d] = 0


# ---------------------------------------------------------------------------------------------------------------
# Command line

def select(cf: CheatFile, names: list[str] | None, everything: bool, skipped: list | None = None, *,
           force: bool = False) -> list[Cheat]:
    """Master code (if any) plus the chosen cheats (--all, --enable names/numbers, or toggles.txt), in file order.

    Chosen cheats that can't run, and extra master codes, go into `skipped` (when given) instead. An empty master
    section counts as no master. Raises ValueError when a master code with code in it can't run, because the
    other cheats usually depend on its registers; `force` skips it and runs the rest anyway.

    In a file with groups, each group with a chosen cheat gets its own master code (a group with none chosen
    doesn't run at all), and a master code that can't run puts its group's chosen cheats in `skipped` instead of
    stopping everything (`force` runs them anyway). The result is in file order, each master before its group."""
    numbered = cf.numbered()
    chosen = []
    if everything:
        chosen = [c for _, c in numbered]
    elif names:
        for want in names:
            hits = [c for _, c in numbered if c.name.lower() == want.strip().lower()]
            if not hits and want.strip().isdigit():
                hits = [c for k, c in numbered if k == int(want)]
            if not hits:
                hits = [c for _, c in numbered if want.strip().lower() in c.name.lower()]
                if len(hits) > 1:
                    raise ValueError(f"'{want}' matches {len(hits)} cheats: "
                                     + ", ".join(c.name for c in hits[:5]) + ". Use the full name or its number.")
            if not hits:
                raise ValueError(f"no cheat named '{want}'. Run 'list' to see the names and numbers.")
            chosen += [c for c in hits if c not in chosen]
    else:
        chosen = [c for _, c in numbered if c.enabled]
    # Registers carry over from one cheat to the next, so run in file order whatever order --enable used.
    where = {id(c): k for k, c in enumerate(cf.cheats)}
    chosen.sort(key=lambda c: where[id(c)])
    if cf.grouped:
        return _select_groups(cf, chosen, skipped, force)
    masters = cf.masters
    broken = [m for m in masters if m.words and not m.runnable]
    if broken and not force:
        m = broken[0]
        raise ValueError(f"the master code '{m.name or '(no name)'}' can't run: {m.error}. The other cheats "
                         "usually depend on it. Fix the file, or add --force to run them without it.")
    lead = [m for m in masters[:1] if m.status != "empty"] + masters[1:]
    chosen = lead + chosen
    if skipped is not None:
        skipped += [c for c in chosen if not c.runnable]
    return [c for c in chosen if c.runnable]


def _select_groups(cf: CheatFile, chosen: list[Cheat], skipped: list | None, force: bool) -> list[Cheat]:
    out, left = [], []
    for g, _ in cf.groups():
        mine = [c for c in chosen if c.group == g]
        if not mine:
            continue
        masters = [c for c in cf.cheats if c.master and c.group == g]
        lead = [m for m in masters[:1] if m.status != "empty"] + masters[1:]
        if any(m.words and not m.runnable for m in masters) and not force:
            left += [m for m in lead if not m.runnable] + mine   # (cmd_run says why: its group's master)
            continue
        left += [c for c in lead + mine if not c.runnable]
        out += [c for c in lead + mine if c.runnable]
    if skipped is not None:
        skipped += left
    return out


def _skip_reason(c: Cheat) -> str:
    if c.error or not c.ops:
        return f"can't run: {c.error or 'no code'}"
    return "the master code of its group can't run"


def _print_list(cf: CheatFile) -> None:
    numbered = cf.numbered()
    runnable = sum(c.runnable for _, c in numbered)
    partial = sum(c.status == "partial" for _, c in numbered)
    refused = sum(c.status == "refused" for _, c in numbered)
    version = f" for {KNOWN_BUILDS.get(cf.build_id, 'build ' + cf.build_id)}" if cf.build_id else ""
    summary = f"{Path(cf.path).name}{version}: {len(numbered)} cheat(s), {runnable} can run"
    if partial:
        summary += f" ({partial} partly)"
    if refused:
        summary += f", {refused} can't"
    masters = cf.masters
    groups = cf.groups() if cf.grouped else [(0, "")]
    if cf.grouped:
        summary += f", in {len(groups)} groups"
        if masters:
            summary += f", {len(masters)} master code(s)"
        if any(sum(m.group == g for m in masters) > 1 for g, _ in groups):
            summary += " (only one is allowed per group)"
    elif len(masters) > 1:
        summary += f", plus {len(masters)} master codes (only one is allowed)"
    elif masters:
        summary += ", plus a master code"
    print(summary + ".")
    for w in cf.warnings:
        print(f"Warning: {w}")
    if not numbered and not masters:
        return
    print()
    print(f"  {'#':>3}  {'on':<6} {'status':<8} {'opcodes':<14} name")
    for g, name in groups:
        mine = [m for m in masters if m.group == g]
        rows = [("M" if k == 0 else f"M{k + 1}", m) for k, m in enumerate(mine)]
        rows += [(str(k), c) for k, c in numbered if c.group == g]
        if cf.grouped:
            print(f"  group: {name or ('(no name)' if g else '(before the first group line)')}")
        # (a group's master runs when one of its cheats is on; a file without groups always runs its master)
        lead_on = "always" if not cf.grouped else "yes" if any(c.enabled for _, c in rows if not c.master) else "no"
        for label, c in rows:
            on = (lead_on if c is mine[0] else "no") if c.master else ("yes" if c.enabled else "no")
            ops = " ".join(c.opcodes) or "-"
            print(f"  {label:>3}  {on:<6} {c.status:<8} {ops:<14} {c.name or '(no name)'}")
            if c.error:
                print(f"       can't run: {c.error}")
            for note in c.notes:
                print(f"       {note}")


def _setup_options(args) -> tuple[Hotkeys | None, tuple[int, int] | None]:
    hotkeys = Hotkeys(parse_hotkeys(args.hotkeys)) if args.hotkeys else None
    main_text = main_text_range(args.main) if args.main else None
    return hotkeys, main_text


def cmd_list(args) -> int:
    try:
        hotkeys, main_text = _setup_options(args)
        cf = load(args.file, hotkeys=hotkeys, main_text=main_text)
    except (OSError, ValueError) as e:
        print(f"Can't read the cheats: {e}")
        return 2
    _print_list(cf)
    return 0


def cmd_run(args) -> int:
    if args.hz <= 0:
        print("--hz must be above 0.")
        return 2
    try:
        hotkeys, main_text = _setup_options(args)
        cf = load(args.file, hotkeys=hotkeys, main_text=main_text)
        skipped = []
        chosen = select(cf, args.enable, args.all, skipped, force=args.force)
    except (OSError, ValueError) as e:
        print(f"Can't start: {e}")
        return 2
    for w in cf.warnings:
        print(f"Warning: {w}")
    for c in skipped:
        group = f", group {c.group_name or '(no name)'}" if c.group else ""
        print(f"Skipped: {c.name or '(no name)'} ({_skip_reason(c)}{group})")
    if not any(not c.master for c in chosen):
        if any(not c.master for c in skipped):
            print("Nothing to run: none of the chosen cheats can run.")
        elif args.all:
            print("Nothing to run: the file has no cheats" + (" besides the master code." if cf.masters else "."))
        else:
            only = ": only the master code would run" if chosen else ""
            print(f"No cheats are on{only}. Use --enable NAME (or a number from 'list') or --all.")
        return 1
    control = None
    try:
        control = Control(args.port, timeout=args.timeout, wait=args.wait)
        control.cmd("ping")
    except KeyboardInterrupt:
        if control:
            control.close()
        print("Stopped.")
        return 0
    except (ControlError, OSError) as e:
        if control:
            control.close()
        print(f"Can't reach the game on port {args.port}: {e}. Start it with SAVAGEWIND_CONTROL={args.port}.")
        return 1
    with control:
        memory = ControlMemory(control)
        vm = CheatVM(memory, hotkeys=hotkeys, main_text=main_text)
        for c in chosen:
            extra = f" ({'; '.join(c.notes)})" if c.notes else ""
            master = ""
            if c.master:
                master = f" [master of group {c.group_name or '(no name)'}]" if c.group else " [master]"
            print(f"Started: {c.name or '(no name)'}{master}{extra}", flush=True)
        if not args.once:
            print(f"Running {len(chosen)} cheat(s) at {args.hz:g} Hz on port {args.port}. Press Ctrl+C to stop.",
                  flush=True)
        try:
            return _loop(vm, chosen, args.hz, args.once)
        except KeyboardInterrupt:
            print("Stopped.")
            return 0
        except GameGone as e:
            print(f"{e}. Stopped.")
            return 0


def _loop(vm: CheatVM, chosen: list[Cheat], hz: float, once: bool) -> int:
    period = 1.0 / hz
    next_t = time.monotonic()
    while True:
        vm.tick(chosen)
        if once:
            print("Applied once.")
            return 0
        if all(id(c) in vm.stopped for c in chosen):
            print("All cheats stopped.")
            return 1
        next_t += period
        delay = next_t - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            next_t = time.monotonic()


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(prog="cheats.py", description="Run Atmosphere cheat codes against Savage Wind.")
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("file", help="Atmosphere cheat file, e.g. cd57b23fa4bbad65.txt")
        p.add_argument("--hotkeys", metavar="MAP",
                       help="map Switch buttons to keyboard keys for keypress cheats, e.g. ZL=SHIFT,A=J (Windows)")
        p.add_argument("--main", metavar="PATH",
                       help="the game's exefs/main (or its folder): skip cheats that patch game code")

    p = sub.add_parser("list", help="show the cheats in a file and whether they can run")
    common(p)
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("run", help="run cheats until Ctrl+C or the game closes")
    common(p)
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="the game's SAVAGEWIND_CONTROL port (7777)")
    p.add_argument("--enable", nargs="+", action="extend", metavar="NAME", help="cheat names or numbers to run")
    p.add_argument("--all", action="store_true", help="run every cheat that can run")
    p.add_argument("--force", action="store_true",
                   help="run the other cheats even when the master code can't run (they may write to wrong places)")
    p.add_argument("--hz", type=float, default=12.0, help="ticks per second (12, like Atmosphere)")
    p.add_argument("--once", action="store_true", help="run one tick and exit")
    p.add_argument("--wait", type=float, default=0.0, help="seconds to wait for the game to open its port")
    p.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for each reply (30)")
    p.set_defaults(fn=cmd_run)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
