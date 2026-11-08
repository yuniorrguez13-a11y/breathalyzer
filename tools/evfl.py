#!/usr/bin/env python3
"""Read and write BFEVFL event flows (the talk and cutscene scripts in Event/*.sbeventpack), Switch layout.

Our own code, written from the bytes of the player's own files: no other tool's code or names. Used by
tools/castle_build.py to build the castle date's event packs on the player's PC.

    python tools/evfl.py show FILE [FLOW]                 print a flow (.bfevfl, or a .sbeventpack and the flow's name)
    python tools/evfl.py check ROMFS [--flow PACK[/FLOW]]... [--all] [--out DIR]
                                                          rebuild game flows and compare them byte for byte

`check` reads the game's extracted\\romfs (or Savage Wind's extracted folder) and only reads it. Without --flow it
checks InitTalk, Npc_HyruleDepthHatago001 and HyruleCastle; --all checks every flow of every event pack. --out
writes each rebuilt flow there (never into the repo: game data), plus the game's copy of any that differ.

In code:

    import evfl
    flow = evfl.load(data)                  # bytes of one .bfevfl -> Flow
    data = evfl.dump(flow)                  # Flow -> bytes (a game flow comes back byte for byte)

    flow = evfl.Flow("SwZelda")             # a new flow: actors and their action lists are made as they are used
    hi = flow.act("SwZelda", "Demo_Talk", evfl.params(MessageId="EventFlowMsg/SwZelda:Talk00", IsWaitFinish=True))
    ask = flow.switch("EventSystemActor", "GeneralChoice2")
    yes = flow.act("SwZelda", "Demo_Talk", evfl.params(MessageId="EventFlowMsg/SwZelda:Yes00", IsWaitFinish=True))
    flow.chain(hi, ask)                     # hi -> ask
    flow.case(ask, 0, yes)                  # answer 0 -> yes
    flow.case(ask, 1, evfl.NONE)            # answer 1 -> end
    flow.entry("Talk", hi)
    data = evfl.dump(flow)                  # counts, string pool, subflow lists: worked out here

Layout (all little-endian, every block 8-aligned; "ptr" = u64 offset from the file start, 0 = none):
- header 0x48: "BFEVFL\\0\\0", 00 03 00 00, BOM FFFE, u8 3, u8 0, u32 offset of the flow name's characters, u16 0,
  u16 offset of the flowchart (0x90), u32 offset of RELT, u32 file size, u16 flowcharts (1), u16 timelines (0), u32 0,
  ptr flowchart list, ptr flowchart dictionary, ptr timeline list (0), ptr timeline dictionary.
- the flowchart list (one ptr), its dictionary, the empty timeline dictionary. A dictionary is the Switch BFRES
  trie ("DIC ", i32 count, then 16-byte nodes: i32 bit, u16 left, u16 right, ptr key string), see
  tools/wiiu_model/dicts.py.
- "EVFL" 0x48: u32 offset of the string pool from "EVFL", u32 0, u32 0, u16 counts of actors, actions (all actors),
  queries (all actors), events, entry points, 3 x u16 0, ptr name, ptr actors, ptr events, ptr entry dictionary,
  ptr entry points.
- actors, 0x38 each: ptr name, ptr second name, ptr argument name, ptr actions, ptr queries, ptr parameters, u16
  actions, u16 queries, u16 argument entry point (0xFFFF none), u16 1. Actions and queries are lists of name ptrs.
- events, 0x28 each: ptr name, u8 kind, u8 0, then by kind (u16 x3, ptr x3):
  0 action   next, actor, action;  parameters, 0, 0
  1 switch   case count, actor, query;  parameters, cases, 0      (a case: u32 value, u16 event, u16 0)
  2 fork     branch count, join event, 0;  branches (u16 events), 0, 0
  3 join     next, 0, 0;  0, 0, 0
  4 subflow  next, 0, 0;  parameters, flow name, entry name
  next/event 0xFFFF = end.
- the entry dictionary, then entry points, 0x20 each: ptr subflow event list (u16 each), ptr 0, ptr 0, u16 subflow
  events, u16 0, u16 first event, u16 0. The subflow list names the subflow events reachable from the entry point.
- then, in order: per event its cases or branches, then its parameters; per actor its parameters, actions, queries;
  per entry point its subflow list and 0x18 zero bytes.
- parameters: a group (u8 1, u8 0, u16 count, u32 0, ptr dictionary, one ptr per value; then the dictionary, then the
  values in order) or a value (u8 kind, u8 0, u16 count, u32 0, u64 0, then the data). Kinds: 0 argument name and
  5 text (a ptr to its own copy: u16 length, bytes, 0, right after the value), 2 int, 3 bool (true = 0x80000001),
  4 float (4 bytes each, `count` of them), 7 ints, 8 bools, 9 floats (arrays of `count`).
- "STR " 0x10 (all zero after the magic), u32 string count, the empty string, then every name once (u16 length,
  bytes, 0, padded to 2). The order is the dictionaries' bit order: strings sorted by their bits counted from bit 0
  of the last byte. A ptr to a name points at its length.
- "RELT": u32 its own offset, u32 1, u32 0; one section (u64 0, u32 0, u32 end of the string pool, u32 0, u32 entry
  count); entries (u32 offset, u32 mask: bit i = the ptr at offset + 8 * i). Listed even when 0: the header's,
  flowchart's, dictionaries', parameters' and name lists' ptrs, an actor's names, actions and queries, an entry
  point's first and third ptr. Listed only when set: an event's ptrs after its name, an actor's parameters, an entry
  point's second ptr.

Not seen in the checked flows, so refused: more than one flowchart, timelines, entry point variables, parameter
kinds 6 and 10-12 (text in UTF-16, text lists, actor ids). The full round trip of every game flow (S2) will show them.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
from dataclasses import dataclass, field

TOOLS = os.path.dirname(os.path.abspath(__file__))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
from wiiu_model import dicts  # noqa: E402  (the Switch dictionary trie)

MAGIC = b"BFEVFL\0\0"
NONE = 0xFFFF            # "no event" (end of a branch)
TRUE_BITS = 0x80000001   # how the game writes a true bool

# Parameter kinds (the u8 type of a parameter)
ARG, GROUP, INT, BOOL, FLOAT, TEXT, WTEXT, INTS, BOOLS, FLOATS, TEXTS, WTEXTS, ACTOR_ID = range(13)
KIND_NAMES = {ARG: "arg", GROUP: "group", INT: "int", BOOL: "bool", FLOAT: "float", TEXT: "text", WTEXT: "wtext",
              INTS: "ints", BOOLS: "bools", FLOATS: "floats", TEXTS: "texts", WTEXTS: "wtexts", ACTOR_ID: "actor"}
_SCALAR = {INT: "i", BOOL: "I", FLOAT: "f"}
_ARRAY = {INTS: "i", BOOLS: "I", FLOATS: "f"}
_INLINE_TEXT = (ARG, TEXT)

HEADER_SIZE, FLOWCHART_SIZE, ACTOR_SIZE, EVENT_SIZE, ENTRY_SIZE = 0x48, 0x48, 0x38, 0x28, 0x20
ENTRY_GAP = 0x18         # zero bytes after each entry point's subflow list
MAX_DEPTH = 16           # nested parameter groups


class EvflError(ValueError):
    """A damaged or unsupported file, or a flow that can't be written; the message says what."""


# ---- model ---------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Param:
    """One parameter: kind (ARG, INT, ...) and value. INT/FLOAT: a number; BOOL: a bool (or the raw u32 when the game
    used other bits); INTS/BOOLS/FLOATS: a list (of the same); ARG/TEXT: a str; GROUP: a dict of Params."""
    kind: int
    value: object


def Int(v: int) -> Param:
    return Param(INT, int(v))


def Bool(v: bool) -> Param:
    return Param(BOOL, bool(v))


def Float(v: float) -> Param:
    return Param(FLOAT, float(v))


def Text(s: str) -> Param:
    return Param(TEXT, str(s))


def Arg(name: str) -> Param:
    """A value taken from the entry point's argument of that name."""
    return Param(ARG, str(name))


def Ints(v) -> Param:
    return Param(INTS, [int(x) for x in v])


def Floats(v) -> Param:
    return Param(FLOATS, [float(x) for x in v])


def Group(d: dict) -> Param:
    return Param(GROUP, params(**d))


def params(**kw) -> dict:
    """A parameter dict from plain values: bool, int, float, str (text), a list of numbers (floats, or ints when
    they all are), a dict (group) or a Param."""
    out = {}
    for k, v in kw.items():
        if isinstance(v, Param):
            out[k] = v
        elif isinstance(v, bool):
            out[k] = Bool(v)
        elif isinstance(v, int):
            out[k] = Int(v)
        elif isinstance(v, float):
            out[k] = Float(v)
        elif isinstance(v, str):
            out[k] = Text(v)
        elif isinstance(v, dict):
            out[k] = Group(v)
        elif isinstance(v, (list, tuple)) and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
            out[k] = Ints(v) if all(isinstance(x, int) for x in v) else Floats(v)
        else:
            raise EvflError(f"parameter {k}: can't tell the kind of {v!r}")
    return out


@dataclass
class Actor:
    name: str
    sub: str = ""                    # second name (several actors of one name: "Checker1"...)
    arg: str = ""                    # argument name ("Self")
    actions: list = field(default_factory=list)
    queries: list = field(default_factory=list)
    params: dict | None = None       # None: no parameter group
    arg_entry: int = NONE            # entry point whose argument this actor is
    flags: int = 1                   # (always 1 in the game's flows)


@dataclass
class Action:
    name: str
    actor: int                       # index into Flow.actors
    action: int                      # index into that actor's actions
    params: dict | None = None
    next: int = NONE


@dataclass
class Switch:
    name: str
    actor: int
    query: int                       # index into that actor's queries
    cases: list = field(default_factory=list)   # [(value, event)]
    params: dict | None = None


@dataclass
class Fork:
    name: str
    branches: list = field(default_factory=list)  # [event]
    join: int = NONE


@dataclass
class Join:
    name: str
    next: int = NONE


@dataclass
class Subflow:
    name: str
    flow: str | None = ""            # "" = this flow
    entry: str | None = ""
    params: dict | None = None
    next: int = NONE


@dataclass
class Entry:
    name: str
    main: int                        # first event
    subflows: list | None = None     # subflow events reachable from it (None: worked out when written)


EVENT_KINDS = {Action: 0, Switch: 1, Fork: 2, Join: 3, Subflow: 4}


@dataclass
class Flow:
    name: str
    actors: list = field(default_factory=list)
    events: list = field(default_factory=list)
    entries: list = field(default_factory=list)
    file_name: str | None = None     # header name, when it is not the flow's name

    # -- building --
    def actor(self, name: str, sub: str = "", params: dict | None = None) -> int:
        """Index of the actor (name, sub), added if new (with `params`, else the game's 9 placement values)."""
        for i, a in enumerate(self.actors):
            if a.name == name and a.sub == sub and a.arg_entry == NONE:
                return i
        self.actors.append(Actor(name, sub, params=placement_params() if params is None else params))
        return len(self.actors) - 1

    def _name(self, name: str | None) -> str:
        if name:
            if any(e.name == name for e in self.events):
                raise EvflError(f"two events named {name}")
            return name
        used = {e.name for e in self.events}
        n = len(self.events)
        while f"Event{n}" in used:
            n += 1
        return f"Event{n}"

    def _add(self, ev) -> int:
        self.events.append(ev)
        return len(self.events) - 1

    def act(self, actor: str, action: str, prm: dict | None = None, *, sub: str = "", name: str | None = None,
            next: int = NONE) -> int:
        """Add an action event; returns its index."""
        a = self.actors[self.actor(actor, sub)]
        if action not in a.actions:
            a.actions.append(action)
        return self._add(Action(self._name(name), self.actor(actor, sub), a.actions.index(action), prm, next))

    def switch(self, actor: str, query: str, cases: dict | None = None, prm: dict | None = None, *, sub: str = "",
               name: str | None = None) -> int:
        """Add a switch event (cases: {value: event}); returns its index."""
        a = self.actors[self.actor(actor, sub)]
        if query not in a.queries:
            a.queries.append(query)
        return self._add(Switch(self._name(name), self.actor(actor, sub), a.queries.index(query),
                                list((cases or {}).items()), prm))

    def fork(self, branches: list, join: int, *, name: str | None = None) -> int:
        return self._add(Fork(self._name(name), list(branches), join))

    def join(self, next: int = NONE, *, name: str | None = None) -> int:
        return self._add(Join(self._name(name), next))

    def subflow(self, flow: str, entry: str, prm: dict | None = None, *, name: str | None = None,
                next: int = NONE) -> int:
        """Run another flow's entry point (flow "" = this flow), then go on to `next`."""
        return self._add(Subflow(self._name(name), flow, entry, prm, next))

    def case(self, switch: int, value: int, event: int) -> None:
        """Add (or change) one case of a switch event."""
        ev = self.events[switch]
        if not isinstance(ev, Switch):
            raise EvflError(f"event {ev.name} is not a switch")
        ev.cases = [(v, t) for v, t in ev.cases if v != value] + [(value, event)]

    def chain(self, *events: int) -> int:
        """Point each event's `next` at the one after it; returns the first."""
        for a, b in zip(events, events[1:]):
            ev = self.events[a]
            if not isinstance(ev, (Action, Join, Subflow)):
                raise EvflError(f"event {ev.name} ({type(ev).__name__.lower()}) has no single next event")
            ev.next = b
        return events[0]

    def entry(self, name: str, main: int) -> Entry:
        if any(e.name == name for e in self.entries):
            raise EvflError(f"two entry points named {name}")
        e = Entry(name, main)
        self.entries.append(e)
        return e


def placement_params() -> dict:
    """The 9 values the game's flows give each actor (all zero)."""
    return params(CreateMode=0, IsGrounding=False, IsWorld=False, PosX=0.0, PosY=0.0, PosZ=0.0, RotX=0.0,
                  RotY=0.0, RotZ=0.0)


def reachable_subflows(flow: Flow, main: int) -> list:
    """The subflow events reachable from event `main` (not through the flows they run), in the order the game's flows
    list them: depth first, a switch's cases and a fork's branches in their order, a fork's join last."""
    out, seen, stack = [], set(), [main]
    while stack:
        i = stack.pop()
        if i == NONE or i in seen:
            continue
        if not 0 <= i < len(flow.events):
            raise EvflError(f"event index {i} is out of range")
        seen.add(i)
        ev = flow.events[i]
        if isinstance(ev, Subflow):
            out.append(i)
        if isinstance(ev, (Action, Join, Subflow)):
            nxt = [ev.next]
        elif isinstance(ev, Switch):
            nxt = [e for _, e in ev.cases]
        else:
            nxt = list(ev.branches) + [ev.join]
        stack.extend(reversed(nxt))
    return out


# ---- strings -------------------------------------------------------------------------------------------------------

_REV8 = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))


def pool_key(s: str) -> bytes:
    """Sort key of the string pool: the string's bits from bit 0 of the last byte up (the dictionaries' bit order)."""
    return bytes(_REV8[b] for b in reversed(s.encode("utf-8")))


def pool_order(strings) -> list:
    return sorted(set(strings) - {""}, key=pool_key)


def _pooled(flow: Flow) -> set:
    out = {flow.name, flow.file_name or flow.name}
    for a in flow.actors:
        out.update((a.name, a.sub, a.arg), a.actions, a.queries)
        _param_keys(a.params, out, 0)
    for e in flow.events:
        out.add(e.name)
        if isinstance(e, Subflow):
            out.update(x for x in (e.flow, e.entry) if x is not None)
        _param_keys(getattr(e, "params", None), out, 0)
    out.update(e.name for e in flow.entries)
    return out


def _param_keys(prm, out: set, depth: int) -> None:
    if prm is None:
        return
    if depth > MAX_DEPTH:
        raise EvflError("parameter groups nested too deep")
    for k, v in prm.items():
        out.add(k)
        if v.kind == GROUP:
            _param_keys(v.value, out, depth + 1)


# ---- reading -------------------------------------------------------------------------------------------------------

class _Reader:
    def __init__(self, data: bytes):
        self.d = memoryview(data)

    def u8(self, o): return self.d[o]
    def u16(self, o): return struct.unpack_from("<H", self.d, o)[0]
    def u32(self, o): return struct.unpack_from("<I", self.d, o)[0]
    def ptr(self, o): return struct.unpack_from("<Q", self.d, o)[0]

    def text(self, p: int, what: str):
        if not p:
            return None
        if p + 2 > len(self.d):
            raise EvflError(f"{what}: string at {p:#x} is outside the file")
        n = self.u16(p)
        if p + 2 + n > len(self.d):
            raise EvflError(f"{what}: string at {p:#x} runs past the end of the file")
        return bytes(self.d[p + 2:p + 2 + n]).decode("utf-8")

    def name_at(self, slot: int, what: str) -> str:
        s = self.text(self.ptr(slot), what)
        return "" if s is None else s

    def names(self, p: int, n: int, what: str) -> list:
        return [self.name_at(p + 8 * i, what) for i in range(n)]

    def dic(self, p: int, what: str) -> list:
        if self.d[p:p + 4] != b"DIC ":
            raise EvflError(f"{what}: no dictionary at {p:#x}")
        _, ptrs = dicts.read_nx_dict(self.d, p)
        return [self.text(k, what) or "" for k in ptrs]

    def group(self, p: int, what: str, depth: int = 0):
        if not p:
            return None
        if depth > MAX_DEPTH:
            raise EvflError(f"{what}: parameter groups nested too deep")
        if self.u8(p) != GROUP:
            raise EvflError(f"{what}: parameters at {p:#x} are not a group")
        n = self.u16(p + 2)
        keys = self.dic(self.ptr(p + 8), what)
        if len(keys) != n:
            raise EvflError(f"{what}: {n} parameters but {len(keys)} names")
        return {k: self.value(self.ptr(p + 0x10 + 8 * i), f"{what}.{k}", depth) for i, k in enumerate(keys)}

    def value(self, p: int, what: str, depth: int) -> Param:
        kind, n = self.u8(p), self.u16(p + 2)
        if kind == GROUP:
            return Param(GROUP, self.group(p, what, depth + 1))
        b = p + 0x10
        if kind in _SCALAR:
            if n != 1:
                raise EvflError(f"{what}: {KIND_NAMES[kind]} with count {n}")
            v = struct.unpack_from("<" + _SCALAR[kind], self.d, b)[0]
            if kind == BOOL and v in (0, TRUE_BITS):
                v = bool(v)
            return Param(kind, v)
        if kind in _ARRAY:
            vals = list(struct.unpack_from(f"<{n}{_ARRAY[kind]}", self.d, b))
            if kind == BOOLS:
                vals = [bool(v) if v in (0, TRUE_BITS) else v for v in vals]
            return Param(kind, vals)
        if kind in _INLINE_TEXT:
            if n != 1:
                raise EvflError(f"{what}: {KIND_NAMES[kind]} with count {n}")
            s = self.text(self.ptr(b), what)
            if s is None:
                raise EvflError(f"{what}: {KIND_NAMES[kind]} without text")
            return Param(kind, s)
        raise EvflError(f"{what}: parameter kind {kind} ({KIND_NAMES.get(kind, 'unknown')}) is not supported yet")

    def events(self, p: int, n: int, actors: list) -> list:
        out = []
        for i in range(n):
            e = p + EVENT_SIZE * i
            name = self.name_at(e, f"event {i}")
            kind = self.u8(e + 8)
            a, b, c = struct.unpack_from("<3H", self.d, e + 0xA)
            p1, p2, p3 = (self.ptr(e + 0x10 + 8 * k) for k in range(3))
            what = f"event {name}"
            if kind == 0:
                out.append(Action(name, b, c, self.group(p1, what), a))
            elif kind == 1:
                cases = [(self.u32(p2 + 8 * k), self.u16(p2 + 8 * k + 4)) for k in range(a)]
                out.append(Switch(name, b, c, cases, self.group(p1, what)))
            elif kind == 2:
                out.append(Fork(name, list(struct.unpack_from(f"<{a}H", self.d, p1)) if a else [], b))
            elif kind == 3:
                out.append(Join(name, a))
            elif kind == 4:
                out.append(Subflow(name, self.text(p2, what), self.text(p3, what), self.group(p1, what), a))
            else:
                raise EvflError(f"{what}: unknown event kind {kind}")
            ev = out[-1]
            if isinstance(ev, (Action, Switch)):
                if not 0 <= ev.actor < len(actors):
                    raise EvflError(f"{what}: actor {ev.actor} is out of range")
                lst = actors[ev.actor].actions if isinstance(ev, Action) else actors[ev.actor].queries
                if not 0 <= (ev.action if isinstance(ev, Action) else ev.query) < len(lst):
                    raise EvflError(f"{what}: action/query index is out of range")
        return out


def load(data: bytes) -> Flow:
    """Flow from the bytes of a .bfevfl."""
    if bytes(data[:8]) != MAGIC:
        raise EvflError("not a BFEVFL file")
    try:
        return _load(_Reader(bytes(data)))
    except (struct.error, IndexError, UnicodeDecodeError) as e:
        raise EvflError(f"damaged BFEVFL file ({e})") from None
    except Exception as e:  # wiiu_model's Damaged (dictionary outside the file)
        if type(e).__name__ == "Damaged":
            raise EvflError(f"damaged BFEVFL file ({e})") from None
        raise


def _load(r: _Reader) -> Flow:
    nfc, ntl = r.u16(0x20), r.u16(0x22)
    if nfc != 1 or ntl != 0:
        raise EvflError(f"{nfc} flowcharts and {ntl} timelines: only one flowchart and no timelines are supported")
    f = r.ptr(r.ptr(0x28))
    if bytes(r.d[f:f + 4]) != b"EVFL":
        raise EvflError("no flowchart block")
    na, _, _, ne, nep = struct.unpack_from("<5H", r.d, f + 0x10)
    name = r.name_at(f + 0x20, "flow name")
    ap, ep, edp, epp = (r.ptr(f + 0x28 + 8 * i) for i in range(4))
    actors = []
    for i in range(na):
        a = ap + ACTOR_SIZE * i
        nm, sub, arg = (r.name_at(a + 8 * k, f"actor {i}") for k in range(3))
        n_act, n_q, arg_entry, flags = struct.unpack_from("<4H", r.d, a + 0x30)
        actors.append(Actor(nm, sub, arg, r.names(r.ptr(a + 0x18), n_act, f"actor {nm}"),
                            r.names(r.ptr(a + 0x20), n_q, f"actor {nm}"),
                            r.group(r.ptr(a + 0x28), f"actor {nm}"), arg_entry, flags))
    events = r.events(ep, ne, actors)
    entries = []
    for i, k in enumerate(r.dic(edp, "entry points") if nep else []):
        p = epp + ENTRY_SIZE * i
        nsub, nvar, main = struct.unpack_from("<3H", r.d, p + 0x18)
        if nvar or r.ptr(p + 8) or r.ptr(p + 16):
            raise EvflError(f"entry point {k}: variables are not supported yet")
        subs = list(struct.unpack_from(f"<{nsub}H", r.d, r.ptr(p))) if nsub else []
        entries.append(Entry(k, main, subs))
    if len(entries) != nep:
        raise EvflError("entry point count and dictionary differ")
    hname = r.text(r.u32(0x10) - 2, "file name") if r.u32(0x10) >= 2 else None
    return Flow(name, actors, events, entries, None if hname == name else hname)


# ---- writing -------------------------------------------------------------------------------------------------------

class _Writer:
    def __init__(self):
        self.buf = bytearray()
        self.rel: set[int] = set()
        self.str_slots: list[tuple[int, str]] = []    # ptr slots to fill with a pooled name
        self.dics: list[tuple[int, list]] = []        # dictionaries to fill once the pool is placed

    def alloc(self, size: int) -> int:
        while len(self.buf) % 8:
            self.buf.append(0)
        at = len(self.buf)
        self.buf += bytes(size)
        return at

    def put(self, fmt: str, at: int, *v) -> None:
        struct.pack_into("<" + fmt, self.buf, at, *v)

    def ptr(self, slot: int, target: int, always: bool) -> None:
        if target:
            self.put("Q", slot, target)
        if target or always:
            self.rel.add(slot)

    def name(self, slot: int, s: str | None, always: bool = True) -> None:
        if s is None:
            if always:
                self.rel.add(slot)
            return
        self.str_slots.append((slot, s))
        self.rel.add(slot)

    def dic(self, keys: list) -> int:
        at = self.alloc(dicts.nx_dict_size(len(keys)))
        self.dics.append((at, keys))
        return at

    def inline_text(self, s: str) -> int:
        b = s.encode("utf-8")
        if len(b) > 0xFFFF:
            raise EvflError(f"text too long ({len(b)} bytes)")
        at = self.alloc(2 + len(b) + 1)
        self.put("H", at, len(b))
        self.buf[at + 2:at + 2 + len(b)] = b
        return at

    def group(self, prm: dict, what: str, depth: int = 0) -> int:
        if depth > MAX_DEPTH:
            raise EvflError(f"{what}: parameter groups nested too deep")
        if len(prm) > 0xFFFF:
            raise EvflError(f"{what}: too many parameters")
        at = self.alloc(0x10 + 8 * len(prm))
        self.put("BxH", at, GROUP, len(prm))
        self.ptr(at + 8, self.dic(list(prm)), True)
        for i, (k, v) in enumerate(prm.items()):
            if not isinstance(v, Param):
                raise EvflError(f"{what}.{k}: {v!r} is not a Param (use evfl.params())")
            self.ptr(at + 0x10 + 8 * i, self.value(v, f"{what}.{k}", depth), True)
        return at

    def value(self, v: Param, what: str, depth: int) -> int:
        k = v.kind
        if k == GROUP:
            if not isinstance(v.value, dict):
                raise EvflError(f"{what}: a group needs a dict")
            return self.group(v.value, what, depth + 1)
        if k in _SCALAR:
            at = self.alloc(0x14)
            self.put("BxH", at, k, 1)
            x = v.value
            if k == BOOL:
                x = (TRUE_BITS if x else 0) if isinstance(x, bool) else int(x) & 0xFFFFFFFF
            self.put(_SCALAR[k], at + 0x10, x)
            return at
        if k in _ARRAY:
            vals = list(v.value)
            if not 0 < len(vals) <= 0xFFFF:
                raise EvflError(f"{what}: {KIND_NAMES[k]} needs 1 to 65535 values")
            if k == BOOLS:
                vals = [(TRUE_BITS if x else 0) if isinstance(x, bool) else int(x) & 0xFFFFFFFF for x in vals]
            at = self.alloc(0x10 + 4 * len(vals))
            self.put("BxH", at, k, len(vals))
            self.put(f"{len(vals)}{_ARRAY[k]}", at + 0x10, *vals)
            return at
        if k in _INLINE_TEXT:
            if not isinstance(v.value, str):
                raise EvflError(f"{what}: {KIND_NAMES[k]} needs a str")
            at = self.alloc(0x18)
            self.put("BxH", at, k, 1)
            self.ptr(at + 0x10, self.inline_text(v.value), True)
            return at
        raise EvflError(f"{what}: parameter kind {k} ({KIND_NAMES.get(k, 'unknown')}) is not supported yet")


def _check(flow: Flow) -> None:
    ne = len(flow.events)
    for n, what in ((len(flow.actors), "actors"), (ne, "events"), (len(flow.entries), "entry points")):
        if n >= NONE:
            raise EvflError(f"too many {what} ({n})")

    def target(i, what):
        if i != NONE and not 0 <= i < ne:
            raise EvflError(f"{what}: event index {i} is out of range")

    for e in flow.events:
        if type(e) not in EVENT_KINDS:
            raise EvflError(f"{e!r} is not an event")
        what = f"event {e.name}"
        if isinstance(e, (Action, Switch)):
            if not 0 <= e.actor < len(flow.actors):
                raise EvflError(f"{what}: actor {e.actor} is out of range")
            lst = flow.actors[e.actor].actions if isinstance(e, Action) else flow.actors[e.actor].queries
            idx = e.action if isinstance(e, Action) else e.query
            if not 0 <= idx < len(lst):
                raise EvflError(f"{what}: action/query {idx} is out of range")
        if isinstance(e, (Action, Join, Subflow)):
            target(e.next, what)
        elif isinstance(e, Switch):
            vals = [c for c, _ in e.cases]
            if len(set(vals)) != len(vals):
                raise EvflError(f"{what}: two cases with the same value")
            for c, t in e.cases:
                if not 0 <= c <= 0xFFFFFFFF:
                    raise EvflError(f"{what}: case value {c} is out of range")
                target(t, what)
        elif isinstance(e, Fork):
            target(e.join, what)
            for t in e.branches:
                target(t, what)
    for en in flow.entries:
        if not 0 <= en.main < ne:
            raise EvflError(f"entry point {en.name}: first event {en.main} is out of range")
        for t in en.subflows or []:
            if not (0 <= t < ne and isinstance(flow.events[t], Subflow)):
                raise EvflError(f"entry point {en.name}: event {t} is not a subflow")
    names = [e.name for e in flow.events]
    if len(set(names)) != len(names):
        raise EvflError("two events with the same name")
    if len({e.name for e in flow.entries}) != len(flow.entries):
        raise EvflError("two entry points with the same name")


def dump(flow: Flow) -> bytes:
    """Bytes of a .bfevfl."""
    _check(flow)
    w = _Writer()
    hdr = w.alloc(HEADER_SIZE)
    fc_list = w.alloc(8)
    fc_dic = w.dic([flow.name])
    tl_dic = w.dic([])
    fc = w.alloc(FLOWCHART_SIZE)
    actors = w.alloc(ACTOR_SIZE * len(flow.actors))
    events = w.alloc(EVENT_SIZE * len(flow.events))
    ep_dic = w.dic([e.name for e in flow.entries])
    eps = w.alloc(ENTRY_SIZE * len(flow.entries))

    w.ptr(fc_list, fc, True)
    for i, a in enumerate(flow.actors):
        at = actors + ACTOR_SIZE * i
        for k, s in enumerate((a.name, a.sub, a.arg)):
            w.name(at + 8 * k, s)
        w.put("4H", at + 0x30, len(a.actions), len(a.queries), a.arg_entry, a.flags)
    for i, e in enumerate(flow.events):
        at = events + EVENT_SIZE * i
        what = f"event {e.name}"
        w.name(at, e.name)
        w.put("B", at + 8, EVENT_KINDS[type(e)])
        if isinstance(e, Action):
            w.put("3H", at + 0xA, e.next, e.actor, e.action)
            if e.params is not None:
                w.ptr(at + 0x10, w.group(e.params, what), False)
        elif isinstance(e, Switch):
            w.put("3H", at + 0xA, len(e.cases), e.actor, e.query)
            if e.cases:
                cp = w.alloc(8 * len(e.cases))
                for k, (val, t) in enumerate(e.cases):
                    w.put("IH", cp + 8 * k, val, t)
                w.ptr(at + 0x18, cp, False)
            if e.params is not None:
                w.ptr(at + 0x10, w.group(e.params, what), False)
        elif isinstance(e, Fork):
            w.put("3H", at + 0xA, len(e.branches), e.join, 0)
            if e.branches:
                bp = w.alloc(2 * len(e.branches))
                w.put(f"{len(e.branches)}H", bp, *e.branches)
                w.ptr(at + 0x10, bp, False)
        elif isinstance(e, Join):
            w.put("H", at + 0xA, e.next)
        else:
            w.put("H", at + 0xA, e.next)
            if e.params is not None:
                w.ptr(at + 0x10, w.group(e.params, what), False)
            w.name(at + 0x18, e.flow, False)
            w.name(at + 0x20, e.entry, False)
    for i, a in enumerate(flow.actors):
        at = actors + ACTOR_SIZE * i
        if a.params is not None:
            w.ptr(at + 0x28, w.group(a.params, f"actor {a.name}"), False)
        for slot, lst in ((at + 0x18, a.actions), (at + 0x20, a.queries)):
            lp = w.alloc(8 * len(lst)) if lst else 0
            for k, s in enumerate(lst):
                w.name(lp + 8 * k, s)
            w.ptr(slot, lp, True)
    for i, en in enumerate(flow.entries):
        at = eps + ENTRY_SIZE * i
        subs = reachable_subflows(flow, en.main) if en.subflows is None else list(en.subflows)
        sp = 0
        if subs:
            sp = w.alloc(2 * len(subs))
            w.put(f"{len(subs)}H", sp, *subs)
        w.ptr(at, sp, True)
        w.rel.add(at + 16)
        w.put("4H", at + 0x18, len(subs), 0, en.main, 0)
        w.alloc(ENTRY_GAP)

    # string pool
    body, offs = _pool(_pooled(flow))
    pool = w.alloc(0x10 + len(body))
    w.buf[pool:pool + 4] = b"STR "
    w.buf[pool + 0x10:pool + 0x10 + len(body)] = body
    spos = {s: pool + 0x10 + o for s, o in offs.items()}
    str_end = len(w.buf)
    for slot, s in w.str_slots:
        w.put("Q", slot, spos[s])
    for at, keys in w.dics:
        w.buf[at:at + dicts.nx_dict_size(len(keys))] = dicts.nx_dict(b"DIC ", keys, [spos[k] for k in keys],
                                                                       spos[""])
        w.rel.update(at + 8 + 16 * k + 8 for k in range(len(keys) + 1))

    # flowchart and header
    w.put("4sI", fc, b"EVFL", pool - fc)
    w.put("5H", fc + 0x10, len(flow.actors), sum(len(a.actions) for a in flow.actors),
          sum(len(a.queries) for a in flow.actors), len(flow.events), len(flow.entries))
    w.put("Q", fc + 0x20, spos[flow.name])
    for k, p in enumerate((actors, events, ep_dic, eps)):
        w.put("Q", fc + 0x28 + 8 * k, p)
    w.rel.update(fc + 0x20 + 8 * k for k in range(5))
    for k, p in enumerate((fc_list, fc_dic, 0, tl_dic)):
        w.ptr(hdr + 0x28 + 8 * k, p, True)

    # relocation table
    slots = sorted(w.rel)
    entries, i = [], 0
    while i < len(slots):
        base, mask = slots[i], 0
        while i < len(slots) and slots[i] < base + 8 * 32:
            mask |= 1 << ((slots[i] - base) // 8)
            i += 1
        entries.append((base, mask))
    relt = w.alloc(0x10 + 0x18 + 8 * len(entries))
    w.put("4sIII", relt, b"RELT", relt, 1, 0)
    w.put("QIIII", relt + 0x10, 0, 0, str_end, 0, len(entries))
    for k, (base, mask) in enumerate(entries):
        w.put("II", relt + 0x28 + 8 * k, base, mask)
    w.put("8s4BHBBIHHIIHH", hdr, MAGIC, 0, 3, 0, 0, 0xFEFF, 3, 0, spos[flow.file_name or flow.name] + 2, 0, fc,
          relt, len(w.buf), 1, 0)
    return bytes(w.buf)


def _pool(strings: set) -> tuple[bytes, dict]:
    """(body after the 16-byte "STR " header, {string: offset in the body of its length})."""
    out = bytearray(struct.pack("<I", 0)) + b"\0\0\0\0"
    offs = {"": 4}
    for s in pool_order(strings):
        b = s.encode("utf-8")
        if len(b) > 0xFFFF:
            raise EvflError(f"name too long ({len(b)} bytes)")
        offs[s] = len(out)
        out += struct.pack("<H", len(b)) + b + b"\0"
        if len(out) % 2:
            out += b"\0"
    struct.pack_into("<I", out, 0, len(offs) - 1)
    return bytes(out), offs


# ---- text view -----------------------------------------------------------------------------------------------------

def show_param(p: Param) -> str:
    if p.kind == GROUP:
        return "{" + ", ".join(f"{k}={show_param(v)}" for k, v in p.value.items()) + "}"
    if p.kind == ARG:
        return f"<arg {p.value}>"
    return repr(p.value)


def describe(flow: Flow) -> str:
    """Readable text of a flow (actors, entry points, events)."""
    def prm(d):
        return "" if d is None else " " + show_param(Param(GROUP, d))

    def tgt(i):
        return "end" if i == NONE else f"{i}:{flow.events[i].name}" if 0 <= i < len(flow.events) else f"?{i}"

    lines = [f"flow {flow.name}: {len(flow.actors)} actors, {len(flow.events)} events, "
             f"{len(flow.entries)} entry points"]
    for i, a in enumerate(flow.actors):
        extra = (f" sub={a.sub}" if a.sub else "") + (f" arg={a.arg}" if a.arg else "") + (
            f" arg_entry={a.arg_entry}" if a.arg_entry != NONE else "")
        lines.append(f"  actor {i} {a.name}{extra} actions={a.actions} queries={a.queries}{prm(a.params)}")
    for en in flow.entries:
        lines.append(f"  entry {en.name} -> {tgt(en.main)} subflows={en.subflows}")
    for i, e in enumerate(flow.events):
        if isinstance(e, Action):
            a = flow.actors[e.actor]
            lines.append(f"  {i:3d} {e.name}: {a.name}.{a.actions[e.action]}{prm(e.params)} -> {tgt(e.next)}")
        elif isinstance(e, Switch):
            a = flow.actors[e.actor]
            cases = ", ".join(f"{v}: {tgt(t)}" for v, t in e.cases)
            lines.append(f"  {i:3d} {e.name}: {a.name}.{a.queries[e.query]}?{prm(e.params)} [{cases}]")
        elif isinstance(e, Fork):
            lines.append(f"  {i:3d} {e.name}: fork {[tgt(t) for t in e.branches]} join {tgt(e.join)}")
        elif isinstance(e, Join):
            lines.append(f"  {i:3d} {e.name}: join -> {tgt(e.next)}")
        else:
            lines.append(f"  {i:3d} {e.name}: run {e.flow or '(this flow)'}<{e.entry}>{prm(e.params)} -> {tgt(e.next)}")
    return "\n".join(lines)


# ---- game files ----------------------------------------------------------------------------------------------------

DEFAULT_FLOWS = ("InitTalk", "Npc_HyruleDepthHatago001", "HyruleCastle")


def game_romfs(path: str) -> str:
    """The romfs folder: `path` itself, or `path`\\romfs (Savage Wind's extracted folder)."""
    for p in (path, os.path.join(path, "romfs")):
        if os.path.isdir(os.path.join(p, "Event")):
            return p
    raise EvflError(f"no Event folder in {path} or {os.path.join(path, 'romfs')}")


def pack_flows(pack_bytes: bytes) -> dict:
    """{flow name: bytes} of an event pack (.sbeventpack, Yaz0 or not)."""
    import wiiu_convert as wc
    raw = wc.yaz0_decompress(pack_bytes)[0] if pack_bytes[:4] == b"Yaz0" else pack_bytes
    out = {}
    for name, data in wc.sarc_files(raw):
        if name.startswith("EventFlow/") and name.endswith(".bfevfl"):
            out[name[len("EventFlow/"):-len(".bfevfl")]] = data
    return out


def round_trip(data: bytes) -> tuple[bool, bytes, str]:
    """(exact, rebuilt bytes, note) for one game flow. The note names the first differing offset or the error."""
    try:
        flow = load(data)
        out = dump(flow)
    except EvflError as e:
        return False, b"", str(e)
    if out == data:
        auto = all(reachable_subflows(flow, en.main) == en.subflows for en in flow.entries)
        return True, out, "" if auto else "exact; an entry point's subflow list differs from the reachable rule"
    first = next((i for i, (a, b) in enumerate(zip(out, data)) if a != b), min(len(out), len(data)))
    return False, out, f"differs at {first:#x} (rebuilt {len(out)} bytes, game {len(data)})"


def _inside(path: str, folder: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(folder)]) == os.path.abspath(folder)
    except ValueError:
        return False


def check(romfs: str, flows: list | None = None, every: bool = False, out: str | None = None) -> tuple[int, int, list]:
    """Rebuild game flows; returns (exact, checked, [(pack/flow, note)] for the ones that are not exact or have a
    note). flows: "Pack" (the pack's flow of the same name) or "Pack/Flow"."""
    root = game_romfs(romfs)
    repo = os.path.dirname(TOOLS)
    if out and _inside(out, repo) and not _inside(out, os.path.join(repo, "out")):
        raise EvflError("--out is inside the repo: game data goes outside it, or in its git-ignored out\\ folder")
    jobs: list[tuple[str, str | None]] = []
    if every:
        jobs = [(f[:-len(".sbeventpack")], None) for f in sorted(os.listdir(os.path.join(root, "Event")))
                if f.endswith(".sbeventpack")]
    else:
        for item in flows or DEFAULT_FLOWS:
            pack, _, name = item.replace("\\", "/").partition("/")
            jobs.append((pack, name or pack))
    exact = checked = 0
    notes = []
    for pack, name in jobs:
        path = os.path.join(root, "Event", pack + ".sbeventpack")
        try:
            with open(path, "rb") as f:
                found = pack_flows(f.read())
        except OSError as e:
            notes.append((pack, f"can't read the pack: {e.strerror or e}"))
            continue
        if name is not None and name not in found:
            notes.append((f"{pack}/{name}", "no such flow in the pack"))
            continue
        for fname, data in sorted(found.items()) if name is None else [(name, found[name])]:
            checked += 1
            ok, rebuilt, note = round_trip(data)
            exact += ok
            if note or not ok:
                notes.append((f"{pack}/{fname}", note))
            if out:
                os.makedirs(out, exist_ok=True)
                if rebuilt:
                    with open(os.path.join(out, f"{pack}__{fname}.rebuilt.bfevfl"), "wb") as f:
                        f.write(rebuilt)
                if not ok:
                    with open(os.path.join(out, f"{pack}__{fname}.game.bfevfl"), "wb") as f:
                        f.write(data)
    return exact, checked, notes


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evfl.py", description="Read, write and check BFEVFL event flows.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show", help="print a flow")
    s.add_argument("file", help=".bfevfl, or .sbeventpack")
    s.add_argument("flow", nargs="?", help="the flow's name in a pack (default: the pack's name)")
    c = sub.add_parser("check", help="rebuild game flows and compare byte for byte")
    c.add_argument("romfs", help="the game's extracted\\romfs (or Savage Wind's extracted folder); only read")
    c.add_argument("--flow", action="append", metavar="PACK[/FLOW]", help="a flow to check (repeatable)")
    c.add_argument("--all", action="store_true", help="every flow of every event pack")
    c.add_argument("--out", help="write the rebuilt flows here (and the game's copy of any that differ)")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "show":
            with open(a.file, "rb") as f:
                data = f.read()
            if data[:8] != MAGIC:
                flows = pack_flows(data)
                want = a.flow or os.path.splitext(os.path.basename(a.file))[0]
                if want not in flows:
                    raise EvflError(f"no flow {want} in the pack (it has: {', '.join(sorted(flows))})")
                data = flows[want]
            print(describe(load(data)))
            return 0
        if a.all and a.flow:
            raise EvflError("--all and --flow don't go together")
        exact, checked, notes = check(a.romfs, a.flow, a.all, a.out)
        for what, note in notes:
            print(f"{what}: {note}")
        print(f"{exact} of {checked} flow(s) rebuilt byte for byte.")
        return 0 if checked and exact == checked else 1
    except EvflError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
