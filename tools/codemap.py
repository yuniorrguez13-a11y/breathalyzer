#!/usr/bin/env python3
"""Name functions in the player's own BotW main from data inside it: C++ RTTI, vtables and dynamic symbols.

    python tools/codemap.py <path to exefs/main> [--out DIR] [--sites FILE | --no-sites]

Writes functions.csv, classes.csv and summary.txt to DIR (default: out/codemap in this repo). It prints counts only,
never code bytes. The output is made from your game files: keep it to yourself, never share or commit it.

Where the names come from (all from your own copy of main, nothing shipped with this tool):
  - RTTI: every typeinfo object carries its class's mangled name; si/vmi typeinfo also name the base classes.
  - vtables: a vtable points at its class's typeinfo, so slot N of class C's vtable is named C::vf_N.
    In BotW only the SDK's own libraries (nn::) have RTTI; the game code was built without it. Its vtables
    ([offset-to-top][0][function pointers], in .data.rel.ro before .dynamic) are still found, when code uses them,
    and named vt_<address>::vf_N. The startup tables (.init_array and the like) are skipped: not vtables.
  - dynamic symbols: main's few exports, and its imports (named at their PLT stub when there is one).
  - Savage Wind site names: the release's tools/game_sites.py KNOWN['1.9.0'] table, read as text from the local
    clone (upstream/SavageWindRecomp, commit 76e61af), used only for a 1.9.0 main (build id cd57b23fa4bbad65...).
Relocations are applied the way Savage Wind's runtime/loader.c (76e61af) does: DT_RELR packed relative ones, then
DT_RELA and DT_JMPREL. Savage Wind is MIT licensed; the relocation logic follows its loader.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nso as nsolib  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "out" / "codemap"
DEFAULT_CLONE = REPO / "upstream" / "SavageWindRecomp"
RELEASE_COMMIT = "76e61af"        # Savage Wind 0.4.1-alpha.4
MAIN_BASE = 0x7000000000          # where Savage Wind (and the Switch) loads main

# ELF dynamic tags (the ones loader.c reads, plus the Android packed ones we refuse)
DT_NULL, DT_PLTRELSZ, DT_HASH, DT_STRTAB, DT_SYMTAB, DT_RELA, DT_RELASZ, DT_RELAENT = 0, 2, 4, 5, 6, 7, 8, 9
DT_STRSZ, DT_SYMENT, DT_JMPREL, DT_RELRSZ, DT_RELR = 10, 11, 23, 35, 36
# the startup / shutdown function tables (arrays of function pointers that are not vtables)
FUNC_ARRAYS = ((25, 27), (26, 28), (32, 33))   # (DT_INIT_ARRAY, SZ), (DT_FINI_ARRAY, SZ), (DT_PREINIT_ARRAY, SZ)
DT_GNU_HASH = 0x6FFFFEF5
DT_ANDROID = {0x6000000F: "DT_ANDROID_REL", 0x60000010: "DT_ANDROID_RELSZ", 0x60000011: "DT_ANDROID_RELA",
              0x60000012: "DT_ANDROID_RELASZ"}
# AArch64 relocation types
R_ABS64, R_GLOB_DAT, R_JUMP_SLOT, R_RELATIVE = 257, 1025, 1026, 1027
SYMBOL_RELOCS = (R_ABS64, R_GLOB_DAT, R_JUMP_SLOT)
STT_NOTYPE, STT_OBJECT, STT_FUNC = 0, 1, 2

# The libc++abi vtables a class typeinfo object points at (at +16, the address point)
CXXABI = {
    "_ZTVN10__cxxabiv117__class_type_infoE": "class",
    "_ZTVN10__cxxabiv120__si_class_type_infoE": "si",
    "_ZTVN10__cxxabiv121__vmi_class_type_infoE": "vmi",
}
TYPE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_$.]*$")
MAX_OTT = 0x1000000               # largest |offset-to-top| we believe (16 MB)
MAX_SLOTS = 4096                  # longest vtable we follow
NOTE_NAMES = 50                   # other names listed in a function's notes before "+N more"


class CodeMapError(ValueError):
    """The file can't be mapped (no MOD0, no dynamic section, unsupported relocations...)."""


# ===================================================================================================================
# Itanium C++ demangler (a small one: names, nested names, std::, templates, common types, substitutions)
# ===================================================================================================================

class _Fail(Exception):
    pass


class _FuncType:
    """A function type, kept apart so pointers to it print as `ret (*)(args)`."""

    def __init__(self, ret: str, params: str, quals: str = ""):
        self.ret, self.params, self.quals = ret, params, quals

    def wrap(self, op: str) -> str:
        return f"{self.ret} ({op})({self.params}){self.quals}"

    def __str__(self) -> str:
        return f"{self.ret} ({self.params}){self.quals}"


class _Pack:
    """A template argument pack (J...E): its elements stay apart so a pack expansion (Dp) can print each one."""

    def __init__(self, items: list[str]):
        self.items = items

    def __str__(self) -> str:
        return ", ".join(i for i in self.items if i != "")


class _ArrayType:
    def __init__(self, elem: str, dim: str):
        self.elem, self.dim = elem, dim

    def wrap(self, op: str) -> str:
        return f"{self.elem} ({op}) [{self.dim}]"

    def __str__(self) -> str:
        return f"{self.elem} [{self.dim}]"


_BUILTIN = {
    "v": "void", "w": "wchar_t", "b": "bool", "c": "char", "a": "signed char", "h": "unsigned char",
    "s": "short", "t": "unsigned short", "i": "int", "j": "unsigned int", "l": "long", "m": "unsigned long",
    "x": "long long", "y": "unsigned long long", "n": "__int128", "o": "unsigned __int128", "f": "float",
    "d": "double", "e": "long double", "g": "__float128", "z": "...",
}
_D_BUILTIN = {"Dn": "std::nullptr_t", "Ds": "char16_t", "Di": "char32_t", "Du": "char8_t", "Dh": "half",
              "Da": "auto", "Dc": "decltype(auto)"}
_STD_SUBS = {"St": "std", "Sa": "std::allocator", "Sb": "std::basic_string", "Ss": "std::string",
             "Si": "std::istream", "So": "std::ostream", "Sd": "std::iostream"}
_LITERAL_SUFFIX = {"i": "", "j": "u", "l": "l", "m": "ul", "x": "ll", "y": "ull"}
_OPS = {
    "nw": "new", "na": "new[]", "dl": "delete", "da": "delete[]", "ps": "+", "ng": "-", "ad": "&", "de": "*",
    "co": "~", "pl": "+", "mi": "-", "ml": "*", "dv": "/", "rm": "%", "an": "&", "or": "|", "eo": "^",
    "aS": "=", "pL": "+=", "mI": "-=", "mL": "*=", "dV": "/=", "rM": "%=", "aN": "&=", "oR": "|=", "eO": "^=",
    "ls": "<<", "rs": ">>", "lS": "<<=", "rS": ">>=", "eq": "==", "ne": "!=", "lt": "<", "gt": ">", "le": "<=",
    "ge": ">=", "ss": "<=>", "nt": "!", "aa": "&&", "oo": "||", "pp": "++", "mm": "--", "cm": ",", "pm": "->*",
    "pt": "->", "cl": "()", "ix": "[]", "qu": "?",
}


def _digit(c: str) -> bool:
    return len(c) == 1 and "0" <= c <= "9"


class _Demangler:
    def __init__(self, s: str):
        self.s, self.i = s, 0
        self.subs: list = []          # substitution candidates (S_, S0_, ...)
        self.tparams: list = []       # template arguments T_, T0_, ... refer to (str, or _Pack for a pack)
        self.capture = False          # record template args as tparams (while reading an encoding's name)
        self.expand: int | None = None  # inside a pack expansion: which element of each pack T_ stands for
        self.pack_len: int | None = None  # the length of the last pack a T_ named (to size an expansion)
        self.depth = 0
        # facts about the last name read
        self.quals = ""
        self.ends_targs = False
        self.cdtor = False            # constructor, destructor or conversion operator (no return type)
        self.last_simple = ""         # last plain identifier, for constructor / destructor names

    # -- input --
    def peek(self, k: int = 0) -> str:
        j = self.i + k
        return self.s[j] if j < len(self.s) else "\0"

    def eat(self, t: str) -> bool:
        if self.s.startswith(t, self.i):
            self.i += len(t)
            return True
        return False

    def need(self, t: str) -> None:
        if not self.eat(t):
            raise _Fail

    def at_end(self) -> bool:
        return self.i >= len(self.s)

    def number(self, allow_neg: bool = True) -> int:
        neg = allow_neg and self.eat("n")
        j = self.i
        while _digit(self.peek()):
            self.i += 1
        if j == self.i:
            raise _Fail
        v = int(self.s[j:self.i])
        return -v if neg else v

    def seq_id(self) -> int:
        """base-36 number before '_' (S<seq>_, T<seq>_): '_' alone is 0, otherwise seq + 1"""
        if self.eat("_"):
            return 0
        j = self.i
        while self.peek() != "_" and (self.peek().isdigit() or "A" <= self.peek() <= "Z"):
            self.i += 1
        if j == self.i or not self.eat("_"):
            raise _Fail
        return int(self.s[j:self.i - 1], 36) + 1

    def _enter(self) -> None:
        self.depth += 1
        if self.depth > 200:
            raise _Fail

    # -- names --
    def source_name(self) -> str:
        n = self.number(allow_neg=False)
        if n <= 0 or self.i + n > len(self.s):
            raise _Fail
        ident = self.s[self.i:self.i + n]
        self.i += n
        self.last_simple = ident
        if ident.startswith("_GLOBAL__N"):
            return "(anonymous namespace)"
        return ident

    def abi_tags(self, name: str) -> str:
        while self.peek() == "B":
            self.i += 1
            keep = self.last_simple
            name += f"[abi:{self.source_name()}]"
            self.last_simple = keep
        return name

    def unqualified_name(self) -> str:
        c = self.peek()
        self.cdtor = False
        if c == "L":                      # internal linkage marker (static functions / variables)
            self.i += 1
            c = self.peek()
        if _digit(c):
            return self.abi_tags(self.source_name())
        if c == "C" and (self.peek(1) in "12345" or self.peek(1) == "I"):
            self.i += 1
            if self.eat("I"):             # inheriting constructor: CI1 <base class type>
                if self.peek() not in "12":
                    raise _Fail
                self.i += 1
                keep = self.last_simple
                self.type()
                self.last_simple = keep
            else:
                self.i += 1
            if not self.last_simple:
                raise _Fail
            self.cdtor = True
            return self.abi_tags(self.last_simple)
        if c == "D" and self.peek(1) in "01245":
            self.i += 2
            if not self.last_simple:
                raise _Fail
            self.cdtor = True
            return self.abi_tags("~" + self.last_simple)
        if c == "U":
            if self.eat("Ut"):
                n = 1 if self.eat("_") else self.number(allow_neg=False) + 2
                if n > 1:
                    self.need("_")
                return f"{{unnamed type#{n}}}"
            if self.eat("Ul"):
                params = []
                while not self.eat("E"):
                    params.append(str(self.type()))
                if params == ["void"]:
                    params = []
                n = 1 if self.eat("_") else self.number(allow_neg=False) + 2
                if n > 1:
                    self.need("_")
                return f"{{lambda({', '.join(params)})#{n}}}"
            raise _Fail
        two = self.s[self.i:self.i + 2]
        if two == "cv":                   # conversion operator
            self.i += 2
            keep = self.capture
            self.capture = False
            t = str(self.type())
            self.capture = keep
            self.cdtor = True
            return f"operator {t}"
        if two == "li":                   # user-defined literal
            self.i += 2
            return f'operator"" {self.source_name()}'
        if two in _OPS:
            self.i += 2
            op = _OPS[two]
            return self.abi_tags(f"operator {op}" if op[0].isalpha() else f"operator{op}")
        raise _Fail

    def nested_name(self) -> str:
        self._enter()
        self.need("N")
        quals = ""
        if self.eat("r"):
            quals += " restrict"
        if self.eat("V"):
            quals += " volatile"
        if self.eat("K"):
            quals = " const" + quals
        if self.eat("R"):
            quals += " &"
        elif self.eat("O"):
            quals += " &&"
        so_far = None
        ends_targs = cdtor = False
        while not self.eat("E"):
            c = self.peek()
            if c == "\0":
                raise _Fail
            if so_far is None and self.eat("St"):
                so_far = "std"
                continue
            if c == "S":
                if so_far is not None:
                    raise _Fail
                so_far = str(self.substitution())
                self.last_simple = so_far.rsplit("::", 1)[-1].split("<", 1)[0]
                continue
            if c == "T":
                if so_far is not None:
                    raise _Fail
                so_far = self.template_param()
                self.subs.append(so_far)
                continue
            if c == "I":
                if so_far is None:
                    raise _Fail
                so_far += self.template_args()
                ends_targs = True
                self.subs.append(so_far)
                continue
            if c == "M":                  # closure prefix marker (lambdas in default member initialisers)
                self.i += 1
                continue
            comp = self.unqualified_name()
            cdtor = self.cdtor
            so_far = comp if so_far is None else f"{so_far}::{comp}"
            ends_targs = False
            self.subs.append(so_far)
        if so_far is None or not self.subs:
            raise _Fail
        self.subs.pop()                   # the whole name is not a candidate here (a type adds itself)
        self.quals, self.ends_targs, self.cdtor = quals, ends_targs, cdtor
        self.depth -= 1
        return so_far

    def local_name(self) -> str:
        self.need("Z")
        saved = (self.capture, self.tparams)
        enc = self.encoding()
        self.capture, self.tparams = saved
        self.need("E")
        if self.eat("s"):
            self.discriminator()
            self.quals, self.ends_targs, self.cdtor = "", False, False
            return f"{enc}::string literal"
        if self.eat("d"):
            if not self.eat("_"):
                self.number(allow_neg=False)
                self.need("_")
        ent = self.name()
        info = (self.quals, self.ends_targs, self.cdtor)
        self.discriminator()
        self.quals, self.ends_targs, self.cdtor = info
        return f"{enc}::{ent}"

    def discriminator(self) -> None:
        if self.peek() != "_":
            return
        if self.peek(1) == "_":
            self.i += 2
            self.number(allow_neg=False)
            self.need("_")
        elif _digit(self.peek(1)):
            self.i += 2

    def name(self) -> str:
        c = self.peek()
        if c == "N":
            return self.nested_name()
        if c == "Z":
            return self.local_name()
        if c == "S" and self.peek(1) != "t":
            sub = str(self.substitution())
            if self.peek() != "I":
                raise _Fail
            r = sub + self.template_args()
            self.quals, self.ends_targs, self.cdtor = "", True, False
            return r
        std = self.eat("St")
        n = self.unqualified_name()
        cdtor = self.cdtor
        if std:
            n = "std::" + n
        ends = False
        if self.peek() == "I":
            self.subs.append(n)           # an unscoped template name is a candidate
            n += self.template_args()
            ends = True
        self.quals, self.ends_targs, self.cdtor = "", ends, cdtor
        return n

    def substitution(self):
        self.need("S")
        two = "S" + self.peek()
        if two in _STD_SUBS:
            self.i += 1
            return _STD_SUBS[two]
        idx = self.seq_id()
        if idx >= len(self.subs):
            raise _Fail
        return self.subs[idx]

    def template_param(self) -> str:
        self.need("T")
        idx = self.seq_id()
        if idx >= len(self.tparams):
            raise _Fail
        p = self.tparams[idx]
        if isinstance(p, _Pack):
            if self.expand is None:
                self.pack_len = len(p.items)
                return str(p)
            if self.expand >= len(p.items):
                raise _Fail
            return p.items[self.expand]
        return p

    def template_args(self) -> str:
        self._enter()
        self.need("I")
        capture, simple = self.capture, self.last_simple
        self.capture = False              # arguments of arguments are not the entity's parameters
        args = []
        while not self.eat("E"):
            if self.at_end():
                raise _Fail
            args.append(self.template_arg())
        self.capture, self.last_simple = capture, simple
        if capture:
            self.tparams = args
        self.depth -= 1
        return "<" + ", ".join(t for t in map(str, args) if t != "") + ">"

    def template_arg(self):
        c = self.peek()
        if c == "L":
            return self.expr_primary()
        if c == "J":                      # argument pack
            self.i += 1
            pack = []
            while not self.eat("E"):
                if self.at_end():
                    raise _Fail
                pack.append(str(self.template_arg()))
            return _Pack(pack)
        if c == "X":
            raise _Fail                   # expressions: not worth it here
        return str(self.type())

    def expr_primary(self) -> str:
        self.need("L")
        if self.eat("_Z") or self.eat("Z"):
            keep = (self.capture, self.tparams)
            enc = self.encoding()
            self.capture, self.tparams = keep
            self.need("E")
            return enc
        c = self.peek()
        builtin = _BUILTIN.get(c) if c != "z" else None
        if builtin:
            self.i += 1
            tname = builtin
        else:
            tname = str(self.type())
        neg = self.eat("n")
        j = self.i
        while self.peek() not in ("E", "\0"):
            self.i += 1
        value = self.s[j:self.i]
        self.need("E")
        if not value:
            raise _Fail
        if c == "b" and value in ("0", "1"):
            return "true" if value == "1" else "false"
        text = ("-" if neg else "") + value
        if builtin and c in _LITERAL_SUFFIX:
            return text + _LITERAL_SUFFIX[c]
        return f"({tname}){text}"

    # -- types --
    def type(self):
        self._enter()
        r = self._type()
        self.depth -= 1
        return r

    def _type(self):
        c = self.peek()
        if c in _BUILTIN:
            self.i += 1
            return _BUILTIN[c]
        if c == "u":                      # vendor extended type
            self.i += 1
            r = self.source_name()
            self.subs.append(r)
            return r
        if c == "D":
            two = self.s[self.i:self.i + 2]
            if two in _D_BUILTIN:
                self.i += 2
                return _D_BUILTIN[two]
            if two == "Dp":               # pack expansion: the pattern once for each element of the pack it names
                self.i += 2
                start, n_subs = self.i, len(self.subs)
                keep_expand, keep_len = self.expand, self.pack_len
                self.expand, self.pack_len = None, None
                pattern = str(self.type())
                count = self.pack_len
                if count is None or keep_expand is not None:
                    r = f"{pattern}..."   # no pack in it (or nested in another expansion): print it as written
                else:
                    end, parts = self.i, []
                    for k in range(count):
                        self.i, self.expand = start, k
                        del self.subs[n_subs:]
                        parts.append(str(self.type()))
                    if self.i != end:
                        raise _Fail
                    r = ", ".join(parts)
                self.expand, self.pack_len = keep_expand, keep_len
                self.subs.append(r)
                return r
            raise _Fail
        if c in "rVK":
            quals = []
            while self.peek() in "rVK":
                quals.append({"r": "restrict", "V": "volatile", "K": "const"}[self.peek()])
                self.i += 1
            inner = self.type()
            if isinstance(inner, _FuncType):
                r = _FuncType(inner.ret, inner.params, inner.quals + "".join(" " + q for q in reversed(quals)))
            else:
                r = str(inner) + "".join(" " + q for q in reversed(quals))
            self.subs.append(r)
            return r
        if c in "PRO":
            self.i += 1
            op = {"P": "*", "R": "&", "O": "&&"}[c]
            inner = self.type()
            r = inner.wrap(op) if isinstance(inner, (_FuncType, _ArrayType)) else f"{inner}{op}"
            self.subs.append(r)
            return r
        if c == "F":
            self.i += 1
            self.eat("Y")
            ret = str(self.type())
            params = []
            quals = ""
            while not self.eat("E"):
                if self.at_end():
                    raise _Fail
                if self.peek() in "RO" and self.peek(1) == "E":
                    quals = " &" if self.peek() == "R" else " &&"
                    self.i += 1
                    continue
                params.append(str(self.type()))
            if params == ["void"]:
                params = []
            r = _FuncType(ret, ", ".join(params), quals)
            self.subs.append(r)
            return r
        if c == "A":
            self.i += 1
            dim = ""
            if _digit(self.peek()):
                dim = str(self.number(allow_neg=False))
            elif self.peek() != "_":
                raise _Fail
            self.need("_")
            r = _ArrayType(str(self.type()), dim)
            self.subs.append(r)
            return r
        if c == "M":
            self.i += 1
            cls = str(self.type())
            mem = self.type()
            r = mem.wrap(f"{cls}::*") if isinstance(mem, (_FuncType, _ArrayType)) else f"{mem} {cls}::*"
            self.subs.append(r)
            return r
        if c == "T":
            r = self.template_param()
            self.subs.append(r)
            if self.peek() == "I":
                r += self.template_args()
                self.subs.append(r)
            return r
        if c == "S" and self.peek(1) != "t":
            sub = self.substitution()
            if self.peek() == "I":
                r = str(sub) + self.template_args()
                self.subs.append(r)
                return r
            return sub
        if c in "NZS" or _digit(c):
            capture = self.capture
            self.capture = False
            r = self.name()
            self.capture = capture
            self.subs.append(r)
            return r
        raise _Fail

    # -- whole symbols --
    def encoding(self) -> str:
        if self.peek() == "T" or self.s.startswith("GV", self.i):
            return self.special_name()
        self.capture = True
        name = self.name()
        self.capture = False
        quals, ends_targs, cdtor = self.quals, self.ends_targs, self.cdtor
        if self.at_end() or self.peek() in "E.":
            return name                   # a variable
        ret = ""
        if ends_targs and not cdtor:
            ret = str(self.type()) + " "
        params = []
        while not self.at_end() and self.peek() not in "E.":
            params.append(str(self.type()))
        if params == ["void"]:
            params = []
        params = [p for p in params if p != ""]   # an empty pack expansion adds nothing
        return f"{ret}{name}({', '.join(params)}){quals}"

    def special_name(self) -> str:
        labels = {"TV": "vtable for ", "TT": "VTT for ", "TI": "typeinfo for ", "TS": "typeinfo name for "}
        two = self.s[self.i:self.i + 2]
        if two in labels:
            self.i += 2
            return labels[two] + str(self.type())
        if self.eat("Th"):
            self.number()
            self.need("_")
            return "non-virtual thunk to " + self.encoding()
        if self.eat("Tv"):
            self.number()
            self.need("_")
            self.number()
            self.need("_")
            return "virtual thunk to " + self.encoding()
        if self.eat("GV"):
            return "guard variable for " + self.name()
        raise _Fail


def demangle_type(mangled: str) -> str:
    """A mangled type name as RTTI stores it (N4ksys3act5ActorE) -> ksys::act::Actor. Unknown forms come back as is."""
    s = mangled[1:] if mangled.startswith("*") else mangled
    try:
        d = _Demangler(s)
        r = str(d.type())
        if d.i == len(s) and r:
            return r
    except (_Fail, RecursionError, ValueError, IndexError):
        pass
    return mangled


def demangle(symbol: str) -> str:
    """A mangled symbol (_ZN3foo3barEv) -> foo::bar(). Anything else, or anything it can't read, comes back as is."""
    if not symbol.startswith("_Z"):
        return symbol
    try:
        d = _Demangler(symbol[2:])
        r = d.encoding()
        if d.at_end():
            return r
        if d.peek() == ".":
            return f"{r} [clone {d.s[d.i:]}]"
    except (_Fail, RecursionError, ValueError, IndexError):
        pass
    return symbol


# ===================================================================================================================
# The module: flat image, dynamic section, symbols, relocations
# ===================================================================================================================

@dataclass
class Symbol:
    name: str
    value: int
    info: int
    shndx: int
    size: int

    @property
    def defined(self) -> bool:
        return self.shndx != 0

    @property
    def type(self) -> int:
        return self.info & 0xF


class Module:
    """main loaded at offset 0 with its relocations applied: pointer fields hold module offsets."""

    def __init__(self, n: nsolib.Nso):
        self.nso = n
        self.size = n.image_size + n.bss_size
        img = bytearray(self.size)
        for s in n.segments:
            img[s.mem_offset:s.end] = s.data
        self.img = img
        text = n.segment("text")
        self.text = (text.mem_offset, text.end)
        self.ptr: dict[int, int] = {}                     # slot offset -> target module offset
        self.symref: dict[int, tuple[int, int, int]] = {}  # slot offset -> (symbol index, addend, reloc type)
        self.stats = {"relr": 0, "rela_relative": 0, "symbol": 0, "unknown": 0}
        self._dynamic()
        self._symbols()
        self._relocate()

    # -- raw reads --
    def u32(self, off: int) -> int:
        if off < 0 or off + 4 > self.size:
            raise CodeMapError(f"read at 0x{off:x} is outside the module")
        return struct.unpack_from("<I", self.img, off)[0]

    def u64(self, off: int) -> int:
        if off < 0 or off + 8 > self.size:
            raise CodeMapError(f"read at 0x{off:x} is outside the module")
        return struct.unpack_from("<Q", self.img, off)[0]

    def i64(self, off: int) -> int:
        v = self.u64(off)
        return v - (1 << 64) if v >> 63 else v

    def cstr(self, off: int, limit: int = 4096) -> str | None:
        if not 0 <= off < self.size:
            return None
        end = self.img.find(b"\0", off, min(self.size, off + limit))
        if end < 0:
            return None
        try:
            return self.img[off:end].decode("ascii")
        except UnicodeDecodeError:
            return None

    def in_text(self, off: int) -> bool:
        return self.text[0] <= off < self.text[1] and off % 4 == 0

    # -- dynamic section --
    def _dynamic(self) -> None:
        mod0 = self.u32(4)
        if mod0 + 8 > self.size or self.img[mod0:mod0 + 4] != b"MOD0":
            raise CodeMapError("no MOD0 header (is this a Switch main?)")
        dyn = mod0 + struct.unpack_from("<i", self.img, mod0 + 4)[0]
        self.dyn_off = dyn                # where .dynamic starts (vtables sit before it, in .data.rel.ro)
        self.dyn: dict[int, int] = {}
        self.dyn_tags: list[int] = []
        for k in range(4096):
            tag, val = self.u64(dyn + 16 * k), self.u64(dyn + 16 * k + 8)
            if tag == DT_NULL:
                break
            self.dyn_tags.append(tag)
            self.dyn.setdefault(tag, val)
        else:
            raise CodeMapError("the dynamic section has no end")
        packed = sorted(DT_ANDROID[t] for t in set(self.dyn_tags) if t in DT_ANDROID)
        if packed:
            raise CodeMapError(f"Android packed relocations ({', '.join(packed)}) are not supported")
        if DT_SYMTAB not in self.dyn or DT_STRTAB not in self.dyn:
            raise CodeMapError("the dynamic section has no symbol table")

    def _nsyms(self) -> int:
        if DT_HASH in self.dyn:
            return self.u32(self.dyn[DT_HASH] + 4)
        if DT_GNU_HASH in self.dyn:      # the end of the last hash chain (as loader.c's gnu_hash_nsyms)
            h = self.dyn[DT_GNU_HASH]
            nbuckets, symoffset, bloom = self.u32(h), self.u32(h + 4), self.u32(h + 8)
            buckets = h + 16 + 8 * bloom
            chain = buckets + 4 * nbuckets
            last = max((self.u32(buckets + 4 * i) for i in range(nbuckets)), default=0)
            if last < symoffset:
                return symoffset
            while not self.u32(chain + 4 * (last - symoffset)) & 1:
                last += 1
            return last + 1
        symtab, strtab = self.dyn[DT_SYMTAB], self.dyn[DT_STRTAB]
        return (strtab - symtab) // self.dyn.get(DT_SYMENT, 24) if strtab > symtab else 0

    def _symbols(self) -> None:
        symtab, strtab = self.dyn[DT_SYMTAB], self.dyn[DT_STRTAB]
        strsz = self.dyn.get(DT_STRSZ, self.size - strtab)
        ent = self.dyn.get(DT_SYMENT, 24)
        n = self._nsyms()
        if n > 1_000_000:
            raise CodeMapError("the symbol table looks damaged")
        self.syms: list[Symbol] = []
        for k in range(n):
            if symtab + k * ent + 24 > self.size:
                raise CodeMapError("the symbol table runs past the module")
            name_off, info, _other, shndx, value, size = struct.unpack_from("<IBBHQQ", self.img, symtab + k * ent)
            name = self.cstr(strtab + name_off) if name_off < strsz else None
            self.syms.append(Symbol(name or "", value, info, shndx, size))

    # -- relocations --
    def _relocate(self) -> None:
        d = self.dyn
        if DT_RELR in d:
            self._relr(d[DT_RELR], d.get(DT_RELRSZ, 0))
        ent = d.get(DT_RELAENT, 24)
        tables = [(name, d[tag], d.get(sz, 0)) for name, tag, sz in (("DT_RELA", DT_RELA, DT_RELASZ),
                                                                     ("DT_JMPREL", DT_JMPREL, DT_PLTRELSZ)) if tag in d]
        if tables and ent < 24:
            raise CodeMapError(f"DT_RELAENT is {ent}: too small for a relocation entry (the file looks damaged)")
        for name, start, size in tables:
            count = size // ent
            if count and (start + (count - 1) * ent + 24 > self.size):
                raise CodeMapError(f"the {name} relocation table runs past the module (the file looks damaged)")
            for k in range(count):
                off, info, addend = struct.unpack_from("<QQq", self.img, start + k * ent)
                rtype, si = info & 0xFFFFFFFF, info >> 32
                if rtype == R_RELATIVE:
                    self._set(off, addend)
                    self.stats["rela_relative"] += 1
                elif rtype in SYMBOL_RELOCS:
                    self.stats["symbol"] += 1
                    if not (0 <= off and off + 8 <= self.size):
                        continue
                    self.symref[off] = (si, addend, rtype)
                    s = self.syms[si] if si < len(self.syms) else None
                    if s is not None and s.defined:
                        self._set(off, s.value + addend)   # a symbol of main itself: an ordinary pointer
                    else:
                        struct.pack_into("<Q", self.img, off, 0)
                else:
                    self.stats["unknown"] += 1

    def _set(self, off: int, target: int) -> None:
        if 0 <= off and off + 8 <= self.size:
            self.ptr[off] = target
            struct.pack_into("<Q", self.img, off, target & 0xFFFFFFFFFFFFFFFF)

    def _relr(self, relr: int, size: int) -> None:
        """DT_RELR, as Savage Wind's loader.c relocate_relr: an even entry is the offset of a word to relocate, an
        odd one a bitmap of the 63 words after the last one relocated. The addend is the word itself."""
        where = None
        n = 0
        for k in range(size // 8):
            e = self.u64(relr + 8 * k)
            if not e & 1:
                self.ptr[e] = self.u64(e)
                where = e + 8
                n += 1
                continue
            if where is None:
                raise CodeMapError("DT_RELR bitmap before any address")
            for b in range(63):
                if (e >> (b + 1)) & 1:
                    w = where + 8 * b
                    self.ptr[w] = self.u64(w)
                    n += 1
            where += 63 * 8
        self.stats["relr"] = n

    def sym_at(self, off: int) -> Symbol | None:
        ref = self.symref.get(off)
        if ref is None or ref[0] >= len(self.syms):
            return None
        return self.syms[ref[0]]

    def plt_stubs(self) -> dict[int, int]:
        """{.got.plt slot offset: PLT stub offset} for lld's AArch64 PLT (adrp x16; ldr x17,[x16,#o]; add; br x17)."""
        t0, t1 = self.text
        out = {}
        pos = t0
        while True:
            p = self.img.find(b"\x20\x02\x1f\xd6", pos, t1)   # br x17
            if p < 0:
                break
            pos = p + 1
            if p % 4 or p - 12 < t0:
                continue
            adrp, ldr, add = struct.unpack_from("<III", self.img, p - 12)
            if (adrp & 0x9F00001F) != 0x90000010 or (ldr & 0xFFC003FF) != 0xF9400211 \
                    or (add & 0xFFC003FF) != 0x91000210:
                continue
            imm = ((adrp >> 3) & 0x1FFFFC) | ((adrp >> 29) & 3)
            if imm & (1 << 20):
                imm -= 1 << 21
            page = ((p - 12) & ~0xFFF) + (imm << 12)
            slot = page + ((ldr >> 10) & 0xFFF) * 8
            stub = p - 12
            if stub - 4 >= t0 and self.u32(stub - 4) == 0xD503245F:   # bti c
                stub -= 4
            out.setdefault(slot, stub)
        return out

    def code_refs(self, window: int = 64) -> set[int]:
        """Module offsets that code builds with ADRP + ADD, or loads from with ADRP + LDR (64-bit), when the second
        instruction uses the ADRP's register within `window` instructions. A heuristic, good enough to tell which
        tables the code uses (a constructor storing a vtable pointer, for instance)."""
        t0, t1 = self.text
        n = (t1 - t0) // 4
        words = struct.unpack_from(f"<{n}I", self.img, t0)
        out: set[int] = set()
        last: dict[int, tuple[int, int]] = {}
        for i, w in enumerate(words):
            if (w & 0x9F000000) == 0x90000000:               # adrp
                imm = ((w >> 3) & 0x1FFFFC) | ((w >> 29) & 3)
                if imm & (1 << 20):
                    imm -= 1 << 21
                last[w & 31] = (i, ((t0 + 4 * i) & ~0xFFF) + (imm << 12))
                continue
            top = w & 0xFFC00000
            if top == 0x91000000 or top == 0xF9400000:      # add xd, xn, #imm / ldr xt, [xn, #imm]
                a = last.get((w >> 5) & 31)
                if a is not None and i - a[0] < window:
                    out.add(a[1] + ((w >> 10) & 0xFFF) * (8 if top == 0xF9400000 else 1))
        return out


# ===================================================================================================================
# RTTI and vtables
# ===================================================================================================================

@dataclass
class Base:
    ti: int | None          # typeinfo offset in main, or None for one outside it
    mangled: str
    offset: int = 0
    virtual: bool = False
    public: bool = True


@dataclass
class Vtable:
    offset: int             # the vtable's start (offset-to-top); objects' vptr = offset + 0x10
    ott: int                # offset-to-top: 0 for a primary vtable, -N for the base subobject at +N
    slots: list             # int = function offset in .text; str = an imported function's symbol name
    label: str = ""         # "" for the primary vtable, else "@0x10" (secondary) or "@vt2" (another one at 0)


@dataclass
class TypeInfo:
    offset: int | None      # the typeinfo object; None for a class without RTTI (known only by its vtable)
    kind: str               # class / si / vmi, or none (no RTTI)
    mangled: str
    name: str
    bases: list[Base]
    size: int
    vtables: list[Vtable] = field(default_factory=list)
    depth: int = 0

    @property
    def primary(self) -> Vtable | None:
        return next((v for v in self.vtables if v.label == ""), None)


def _ti_ref(m: Module, slot: int) -> tuple[int | None, str] | None:
    """What a typeinfo pointer field points at: (offset in main or None, mangled name or '')."""
    if slot in m.ptr:
        return m.ptr[slot], ""
    s = m.sym_at(slot)
    if s is not None and s.name.startswith("_ZTI"):
        return None, s.name[4:]
    return None


def find_typeinfos(m: Module) -> dict[int, TypeInfo]:
    local_abi = {}
    for s in m.syms:
        if s.defined and s.name in CXXABI:
            local_abi[s.value + 16] = CXXABI[s.name]
    cands: dict[int, str] = {}
    for off, (si, addend, _t) in m.symref.items():
        name = m.syms[si].name if si < len(m.syms) else ""
        if name in CXXABI and addend == 16:
            cands[off] = CXXABI[name]
    if local_abi:
        for off, tgt in m.ptr.items():
            if tgt in local_abi:
                cands.setdefault(off, local_abi[tgt])
    tis: dict[int, TypeInfo] = {}
    for off, kind in sorted(cands.items()):
        try:
            if off + 8 in m.ptr:
                mangled = m.cstr(m.ptr[off + 8])
            else:
                s = m.sym_at(off + 8)
                mangled = s.name[4:] if s is not None and s.name.startswith("_ZTS") else None
            if not mangled:
                continue
            bare = mangled[1:] if mangled.startswith("*") else mangled
            if not TYPE_NAME_RE.match(bare) or len(bare) > 2048:
                continue
            bases: list[Base] = []
            size = 16
            if kind == "si":
                ref = _ti_ref(m, off + 16)
                if ref is not None:
                    bases.append(Base(ref[0], ref[1]))
                size = 24
            elif kind == "vmi":
                count = m.u32(off + 20)
                if count > 256:
                    continue
                for k in range(count):
                    bp = off + 24 + 16 * k
                    ref = _ti_ref(m, bp)
                    of = m.i64(bp + 8)
                    if ref is not None:
                        bases.append(Base(ref[0], ref[1], of >> 8, bool(of & 1), bool(of & 2)))
                size = 24 + 16 * count
            tis[off] = TypeInfo(off, kind, bare, demangle_type(bare), bases, size)
        except CodeMapError:
            continue
    # base names, and inheritance depth (for "first name wins": the class that introduced a slot comes first)
    for ti in tis.values():
        for b in ti.bases:
            if b.ti is not None:
                b.mangled = tis[b.ti].mangled if b.ti in tis else b.mangled
    memo: dict[int, int] = {}

    def depth(off: int, seen: frozenset) -> int:
        if off in memo:
            return memo[off]
        if off in seen or off not in tis:
            return 0
        ds = [depth(b.ti, seen | {off}) if b.ti is not None else 0 for b in tis[off].bases]
        memo[off] = 1 + max(ds) if ds else 0
        return memo[off]

    for off, ti in tis.items():
        ti.depth = depth(off, frozenset())
    return tis


def _slot(m: Module, pos: int) -> int | str | None:
    """What the word at pos holds as a vtable slot: a function offset in .text, an imported function's name (an
    ABS64 relocation, as vtables use; GOT entries use GLOB_DAT / JUMP_SLOT), or None if it can't be a slot."""
    if pos in m.ptr:
        t = m.ptr[pos]
        return t if m.in_text(t) else None
    ref = m.symref.get(pos)
    if ref is None or ref[2] != R_ABS64:
        return None
    s = m.sym_at(pos)
    if s is None or s.defined or s.type not in (STT_FUNC, STT_NOTYPE) or not s.name:
        return None
    return s.name


def _vtable_slots(m: Module, start: int) -> list:
    out: list = []
    pos = start
    while len(out) < MAX_SLOTS:
        t = _slot(m, pos)
        if t is None:
            break
        out.append(t)
        pos += 8
    return out


def find_vtables(m: Module, tis: dict[int, TypeInfo]) -> int:
    """Attach vtables to their typeinfo. Returns how many were found."""
    inside_ti = set()
    for ti in tis.values():
        inside_ti.update(range(ti.offset, ti.offset + ti.size, 8))
    found = 0
    for slot in sorted(o for o, t in m.ptr.items() if t in tis):
        vt = slot - 8
        if vt in inside_ti or slot in inside_ti or vt in m.ptr or vt in m.symref or vt < 0:
            continue
        try:
            ott = m.i64(vt)
        except CodeMapError:
            continue
        if ott > 0 or ott < -MAX_OTT or ott % 8:
            continue
        slots = _vtable_slots(m, slot + 8)
        if not slots:
            continue
        ti = tis[m.ptr[slot]]
        if ott < 0:
            label = f"@0x{-ott:x}"
        else:
            zeros = sum(1 for v in ti.vtables if v.ott == 0)
            label = "" if zeros == 0 else f"@vt{zeros + 1}"
        ti.vtables.append(Vtable(vt, ott, slots, label))
        found += 1
    return found


def find_unnamed_vtables(m: Module, known: set[int], refs: set[int] | None = None) -> list[TypeInfo]:
    """Vtables of classes built without RTTI (most game code): [offset-to-top][0][function pointers...].

    Two zero words before function pointers could be other data, so a primary vtable (offset-to-top 0) counts only
    when the code uses it (ADRP + ADD/LDR to its start or its address point, or a pointer to it in data). A
    secondary one (offset-to-top -N) counts only when it follows straight on from a vtable of the same group.
    The first slot can be an imported function (an abstract class: __cxa_pure_virtual).

    Only where the linker puts vtables counts: the data segment before .dynamic (.data.rel.ro, read-only once
    relocated; every vtable with RTTI sits there). The startup and shutdown tables there (DT_INIT_ARRAY,
    DT_FINI_ARRAY, DT_PREINIT_ARRAY) are function pointers too, but not vtables: they are skipped, and so is
    anything in writable .data after .dynamic. Each group becomes a class named vt_<address of its primary vtable>."""
    if refs is None:
        refs = m.code_refs()
    ptr_targets = set(m.ptr.values())
    data = m.nso.segment("data")
    lo, hi = data.mem_offset, data.end
    if lo <= m.dyn_off < hi:
        hi = m.dyn_off
    arrays = [(m.dyn[a], m.dyn[a] + max(m.dyn.get(sz, 0), 8)) for a, sz in FUNC_ARRAYS if a in m.dyn]
    out: list[TypeInfo] = []
    group: TypeInfo | None = None
    group_end = -1
    for slot in sorted(set(m.ptr) | set(m.symref)):
        if _slot(m, slot) is None:
            continue
        ti, vt = slot - 8, slot - 16
        if vt < 0 or vt in known or ti in m.ptr or vt in m.ptr or ti in m.symref or vt in m.symref:
            continue
        if m.u64(ti) != 0:
            continue
        ott = m.i64(vt)
        if ott > 0 or ott < -MAX_OTT or ott % 8:
            continue
        slots = _vtable_slots(m, slot)
        end = slot + 8 * len(slots)
        if vt < lo or end > hi or any(vt < a1 and a0 < end for a0, a1 in arrays):
            if ott == 0:
                group = None
            continue
        if ott == 0:
            if not (vt in refs or vt + 16 in refs or vt in ptr_targets or vt + 16 in ptr_targets):
                group = None
                continue
            group = TypeInfo(None, "none", "", f"vt_{MAIN_BASE + vt:x}", [], 0)
            group.vtables.append(Vtable(vt, 0, slots, ""))
            out.append(group)
        else:
            if group is None or vt != group_end:
                continue
            group.vtables.append(Vtable(vt, ott, slots, f"@0x{-ott:x}"))
        group_end = end
    return out


# ===================================================================================================================
# Savage Wind site names (read as text from the release's tools/game_sites.py, never imported)
# ===================================================================================================================

SITES_VERSION = "1.9.0"                 # the only version whose site addresses are used
SITES_BUILD_ID = "cd57b23fa4bbad65"     # its main's build id (first 8 bytes), as the release's VERSIONS lists it
SITE_NOTES = {
    "code": "Savage Wind site: an instruction",
    "call": "Savage Wind site: a function entry (call target)",
    "ref": "Savage Wind site: a data address",
    "got": "Savage Wind site: a data address (through the GOT)",
}
CODE_AT_ENTRY = "Savage Wind site: an instruction (here also a function entry)"


def release_sites_text(clone: Path = DEFAULT_CLONE, commit: str = RELEASE_COMMIT) -> str | None:
    """The release's tools/game_sites.py as text, from the local clone (None if git or the clone isn't there)."""
    try:
        r = subprocess.run(["git", "-C", str(clone), "show", f"{commit}:tools/game_sites.py"],
                           capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace")


def parse_sites(text: str, build_id_hex: str) -> tuple[str | None, list[tuple[str, int, str]]]:
    """(version name, [(site name, absolute address, site kind)]) for this main's build id, from game_sites.py text.

    Only KNOWN['1.9.0'] is used, and only for a main whose build id starts with cd57b23fa4bbad65 (which VERSIONS
    must also list for 1.9.0). Any other main, 1.8.2 included, gets (None, [])."""
    if not build_id_hex.lower().startswith(SITES_BUILD_ID):
        return None, []
    listed = dict(re.findall(r"Version\(\s*'([^']+)'\s*,\s*'([0-9a-fA-F]*)'", text))
    if listed.get(SITES_VERSION, "").lower() != SITES_BUILD_ID:
        return None, []
    version = SITES_VERSION
    kinds = dict(re.findall(r"Site\(\s*'(\w+)'\s*,\s*'(\w+)'", text))
    k = re.search(r"^KNOWN\s*=\s*\{", text, re.M)
    if not k:
        return version, []
    block = re.compile(r"'" + re.escape(version) + r"'\s*:\s*\{(.*?)\}", re.S).search(text, k.end())
    if not block:
        return version, []
    out = []
    for name, value in re.findall(r"'(\w+)'\s*:\s*(0x[0-9a-fA-F]+|\d+)", block.group(1)):
        out.append((name, int(value, 0), kinds.get(name, "")))
    return version, out


# ===================================================================================================================
# The map
# ===================================================================================================================

@dataclass
class FunctionRow:
    offset: int
    name: str
    kind: str               # vfunc / export / import / site
    notes: str = ""


@dataclass
class CodeMap:
    module: Module
    typeinfos: dict[int, TypeInfo]      # classes with RTTI, by typeinfo offset
    unnamed: list[TypeInfo]             # classes without RTTI, known by their vtables (vt_<address>)
    functions: list[FunctionRow]
    stats: dict
    sites_note: str

    @property
    def classes(self) -> list[TypeInfo]:
        return list(self.typeinfos.values()) + self.unnamed


def _hex(off: int | None) -> str:
    return "" if off is None else f"0x{MAIN_BASE + off:x}"


def build(n: nsolib.Nso, sites_text: str | None = None, unnamed: bool = True,
          sites_source: str = f"Savage Wind {RELEASE_COMMIT}") -> CodeMap:
    """The map of main. sites_text is a game_sites.py as text; sites_source says where it came from (for notes)."""
    m = Module(n)
    tis = find_typeinfos(m)
    find_vtables(m, tis)
    known = {v.offset for ti in tis.values() for v in ti.vtables}
    groups = find_unnamed_vtables(m, known) if unnamed else []
    classes = list(tis.values()) + groups
    rows: list[FunctionRow] = []

    # vfuncs: one row per address, every name in notes. First name wins: classes with RTTI before the others,
    # primary vtables before secondary ones, base classes (fewer ancestors, then fewer slots) before derived ones
    names: dict[int, list[tuple]] = {}
    slot_total = 0
    for ti in classes:
        for vt in ti.vtables:
            for k, t in enumerate(vt.slots):
                slot_total += 1
                if isinstance(t, int):
                    key = (ti.kind == "none", vt.label != "", ti.depth, len(vt.slots), vt.offset, k)
                    names.setdefault(t, []).append((key, f"{ti.name}::vf_{k}{vt.label}"))
    shared = 0
    for addr, lst in names.items():
        lst.sort()
        uniq = list(dict.fromkeys(n for _, n in lst))
        notes = ""
        if len(uniq) > 1:
            shared += 1
            extra = uniq[1:]
            notes = "also " + "; ".join(extra[:NOTE_NAMES])
            if len(extra) > NOTE_NAMES:
                notes += f"; +{len(extra) - NOTE_NAMES} more"
        rows.append(FunctionRow(addr, uniq[0], "vfunc", notes))

    # exports (main's own dynamic symbols)
    n_exports = 0
    for s in m.syms:
        if s.defined and s.name and s.type in (STT_FUNC, STT_OBJECT, STT_NOTYPE) and s.shndx < 0xFF00:
            kind_note = {STT_FUNC: "function", STT_OBJECT: "data"}.get(s.type, "symbol")
            pretty = demangle(s.name)
            note = kind_note + ("" if pretty == s.name else f"; {s.name}")
            rows.append(FunctionRow(s.value, pretty, "export", note))
            n_exports += 1

    # imports: at their PLT stub, or else at their GOT slot
    stubs = m.plt_stubs()
    seen: dict[str, FunctionRow] = {}
    abs_only = set()
    for off in sorted(m.symref):
        si, _addend, rtype = m.symref[off]
        s = m.syms[si] if si < len(m.syms) else None
        if s is None or s.defined or not s.name:
            continue
        pretty = demangle(s.name)
        mangled = "" if pretty == s.name else f"; {s.name}"
        if rtype == R_JUMP_SLOT and off in stubs:
            row = FunctionRow(stubs[off], pretty, "import", "PLT stub" + mangled)
        elif rtype in (R_GLOB_DAT, R_JUMP_SLOT):
            row = FunctionRow(off, pretty, "import", "pointer slot (data), no PLT stub" + mangled)
        else:
            abs_only.add(s.name)
            continue
        old = seen.get(s.name)
        if old is None or (old.notes.startswith("pointer") and row.notes.startswith("PLT")):
            seen[s.name] = row
    rows.extend(seen.values())
    abs_only -= set(seen)

    # Savage Wind sites
    n_sites = 0
    sites_note = "not used (no game_sites.py text)"
    if sites_text is not None:
        version, sites = parse_sites(sites_text, n.build_id_hex)
        if version is None:
            sites_note = (f"not used (they are for {SITES_VERSION} only, build id {SITES_BUILD_ID}; this main is "
                          f"{n.build_id_hex[:16]})")
        else:
            entries = set(names) | {s.value for s in m.syms if s.defined and s.type == STT_FUNC}
            for name, addr, kind in sites:
                off = addr - MAIN_BASE
                if kind in ("imm", "data", "end") or not 0 <= off < m.size:
                    continue
                note = CODE_AT_ENTRY if kind == "code" and off in entries else SITE_NOTES.get(kind, "Savage Wind site")
                rows.append(FunctionRow(off, name, "site", note))
                n_sites += 1
            sites_note = f"{n_sites} from {sites_source} (version {version})"

    order = {"export": 0, "site": 1, "vfunc": 2, "import": 3}
    rows.sort(key=lambda r: (r.offset, order[r.kind], r.name))
    stats = {
        "classes": len(tis),
        "class_kinds": {k: sum(1 for t in tis.values() if t.kind == k) for k in ("class", "si", "vmi")},
        "classes_with_vtable": sum(1 for ti in tis.values() if ti.vtables),
        "unnamed_classes": len(groups),
        "vtables": sum(len(t.vtables) for t in classes),
        "rtti_vtables": len(known),
        "secondary_vtables": sum(1 for t in classes for v in t.vtables if v.label),
        "vtable_slots": slot_total,
        "vfuncs": len(names),
        "shared_vfuncs": shared,
        "exports": n_exports,
        "imports": len(seen),
        "imports_without_address": len(abs_only),
        "sites": n_sites,
        "relocs": dict(m.stats),
        "symbols": len(m.syms),
        "named": len({r.offset for r in rows if m.in_text(r.offset)}),   # distinct named code addresses
    }
    return CodeMap(m, tis, groups, rows, stats, sites_note)


# ===================================================================================================================
# Output
# ===================================================================================================================

_LITERAL_PREFIXES = ("\\\\?\\", "\\\\.\\", "\\??\\")


def _plain_path(p: str) -> str:
    """Drop Windows' literal-path prefix (\\\\?\\C:\\..., \\\\?\\UNC\\host\\share\\...), which Path.resolve() keeps, so
    the path compares like an ordinary one. Other prefixed forms (\\\\?\\Volume{...}) are left as they are."""
    if os.name != "nt":
        return p
    s = p.replace("/", "\\")
    for pre in _LITERAL_PREFIXES:
        if s[:len(pre)] != pre:
            continue
        rest = s[len(pre):]
        if rest[:4].upper() == "UNC\\":
            return "\\\\" + rest[4:]
        if re.match(r"[A-Za-z]:(\\|$)", rest):
            return rest
    return p


def _same_file(a: os.stat_result, b: os.stat_result) -> bool:
    return a.st_ino != 0 and (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def check_out_dir(out: Path) -> None:
    """Output made from the game must stay out of git: inside this repo, only under out/.

    Checked twice: by name (after resolving the path), and by identity, so that another spelling of a folder in the
    repo (\\\\localhost\\C$\\..., a \\\\?\\Volume{...} path, a subst drive) is caught too."""
    repo = REPO.resolve()
    allowed = repo / "out"
    o = Path(_plain_path(str(Path(_plain_path(str(out))).resolve())))
    refuse = CodeMapError(f"{out} is inside the repo; write under {allowed} (git ignores it) or outside the repo")
    if (o == repo or repo in o.parents) and not (o == allowed or allowed in o.parents):
        raise refuse
    try:
        repo_st = os.stat(repo)
    except OSError:
        return
    chain = [o, *o.parents]
    for k, a in enumerate(chain):
        try:
            st = os.stat(a)
        except OSError:
            continue                  # not made yet (or not reachable): look at the folder above it
        if _same_file(st, repo_st):
            if k == 0 or os.path.normcase(chain[k - 1].name) != "out":
                raise refuse
            return


def _bases_text(ti: TypeInfo) -> str:
    parts = []
    for b in ti.bases:
        name = demangle_type(b.mangled) if b.mangled else (f"typeinfo {_hex(b.ti)}" if b.ti is not None else "?")
        if b.virtual:
            name = "virtual " + name
        if not b.public and ti.kind == "vmi":
            name = "non-public " + name
        if b.offset and not b.virtual:
            name += f" +0x{b.offset:x}"
        parts.append(name)
    return "; ".join(parts)


def write(cm: CodeMap, out: Path) -> None:
    check_out_dir(out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "functions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["address", "name", "kind", "notes"])
        for r in cm.functions:
            w.writerow([_hex(r.offset), r.name, r.kind, r.notes])
    with open(out / "classes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["class", "typeinfo", "vtables", "bases", "virtuals", "rtti_kind", "mangled"])
        for ti in sorted(cm.classes, key=lambda t: (t.kind == "none", t.name, t.offset or 0)):
            vts = [v for v in ti.vtables if not v.label] + [v for v in ti.vtables if v.label]
            prim = ti.primary
            w.writerow([ti.name, _hex(ti.offset), "; ".join(_hex(v.offset) + v.label for v in vts),
                        _bases_text(ti), len(prim.slots) if prim else 0, ti.kind, ti.mangled])
    s = cm.stats
    ck, rl = s["class_kinds"], s["relocs"]
    kinds = {k: sum(1 for r in cm.functions if r.kind == k) for k in ("vfunc", "export", "import", "site")}
    lines = [
        f"Code map of main, build {cm.module.nso.build_id_hex[:16]}",
        "Made from your own game files. Keep it to yourself: never share or commit it.",
        "",
        f"named code        {s['named']} different code addresses have a name",
        f"rows              {len(cm.functions)} (vfunc {kinds['vfunc']}, export {kinds['export']}, "
        f"import {kinds['import']}, site {kinds['site']})",
        f"classes           {s['classes']} with RTTI (class {ck['class']}, si {ck['si']}, vmi {ck['vmi']}), "
        f"{s['classes_with_vtable']} of them with a vtable; {s['unnamed_classes']} without RTTI (vt_<address>)",
        f"vtables           {s['vtables']} ({s['rtti_vtables']} with RTTI, {s['secondary_vtables']} secondary), "
        f"{s['vtable_slots']} slots",
        f"vfuncs            {s['vfuncs']} different functions, {s['shared_vfuncs']} of them in more than one slot",
        f"dynamic symbols   {s['symbols']}; imports without one address (only in data): "
        f"{s['imports_without_address']}",
        f"relocations       {rl['relr']} packed relative (DT_RELR), {rl['rela_relative']} RELA relative, "
        f"{rl['symbol']} by symbol, {rl['unknown']} of other types",
        f"Savage Wind sites {cm.sites_note}",
        "",
        "Names: Class::vf_N is slot N of Class's vtable (N counts from 0, at vtable+0x10).",
        "Class::vf_N@0x10 is slot N of the vtable for the base part at offset 0x10 (multiple inheritance).",
        "vt_7001234560 is a class without RTTI, named after its vtable's address.",
        "Addresses are guest addresses: main starts at 0x7000000000. An object's vptr is its vtable + 0x10.",
    ]
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Name functions in your own BotW main from its RTTI, vtables and "
                                             "symbols. Prints counts only.")
    ap.add_argument("main", help="path to exefs/main (your own dump)")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help=f"output folder (default {DEFAULT_OUT})")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--sites", help="a copy of Savage Wind's tools/game_sites.py (default: the release's, from "
                                   "the local clone)")
    g.add_argument("--no-sites", action="store_true", help="leave out Savage Wind site names")
    args = ap.parse_args(argv)
    out = Path(args.out)
    try:
        check_out_dir(out)
        n = nsolib.load(args.main)
        source = f"Savage Wind {RELEASE_COMMIT}"
        if args.no_sites:
            text = None
        elif args.sites:
            text = Path(args.sites).read_text(encoding="utf-8", errors="replace")
            source = args.sites
        else:
            text = release_sites_text()
        cm = build(n, text, sites_source=source)
        write(cm, out)
    except (CodeMapError, nsolib.NsoError, OSError) as e:
        print(f"codemap: {e}", file=sys.stderr)
        return 1
    except struct.error as e:         # a table that points outside the file
        print(f"codemap: {args.main} looks damaged ({e})", file=sys.stderr)
        return 1
    s = cm.stats
    print(f"Named {s['named']} code addresses. Map written to {out}")
    print(f"  classes   {s['classes']} with RTTI ({s['classes_with_vtable']} with a vtable), "
          f"{s['unnamed_classes']} without (named vt_<address>)")
    print(f"  vtables   {s['vtables']} ({s['secondary_vtables']} secondary)")
    print(f"  vfuncs    {s['vfuncs']}")
    print(f"  exports   {s['exports']}, imports {s['imports']}")
    print(f"  sites     {cm.sites_note}")
    print("Made from your game files: never share or commit the output.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
