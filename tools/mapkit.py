"""mapkit: the shared map, event and flow helpers for mods that place things in Hyrule (giruka, Breathalyzer).

    import mapkit as mk

    ids = mk.hash_ids("giruka.mymod", ["door", "spot_area", "spot_relay", "spot_event", "gate"], used)
    objs = [mk.map_object("door", "TwnObj_Village_Hatenodoor_A_01", [10.0, 120.0, -30.0], ids, yaw=90.0)]
    objs += mk.trigger_chain("spot", [12.0, 120.0, -30.0], ids, flow="MyMod", entry="Hello", half=[2, 2, 2])
    objs.append(mk.flag_gate("gate", [12.0, 120.0, -30.0], ids, "MyMod_Unlocked", creates=["door", "spot_area"]))

    f = mk.FlowSpec("MyMod")
    f.entry("Hello", f.act("hi", "MyNpc", "Demo_Talk", mk.talk_params("MyMod", "Hi", close=True)))
    data = mk.build_flow(f.spec)                       # needs tools/evfl.py: mk.evfl_ready(shared) first
    pack, size = mk.event_pack("MyMod", data, shared, mk.subflows_of(f.spec), source="Npc_X")

Full guide: docs/mapkit.md.

This module is the helpers' home: tools/castle_build.py and tools/dragonbreath_stage2.py both import it
(castle_build keeps its old names as aliases of these). The higher-level helpers (hash_id, FlowSpec, trigger_chain,
flag_gate, talk_params, info_entries, yaz0_align) make exactly the objects both mods wrote by hand before
(tests/test_mapkit.py checks that, object for object).
"""
from __future__ import annotations

import copy
import math
import os
import struct
import sys
import zlib

TOOLS = os.path.dirname(os.path.abspath(__file__))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import bcml_data as bd  # noqa: E402
import bcml_maps as bm  # noqa: E402
import bnp  # noqa: E402
import ukmm  # noqa: E402
import wiiu_convert as wc  # noqa: E402


class MapError(Exception):
    """What every helper raises: something the mod's author (or player) has to fix. (castle_build.CastleError is
    this same class.)"""


# ---------------------------------------------------------------------------------------------- packs

def unyaz0(data: bytes) -> bytes:
    """The bytes with Yaz0 undone when they are Yaz0 (else as they are)."""
    return wc.yaz0_decompress(data)[0] if data[:4] == b"Yaz0" else data


def read_pack(data: bytes) -> dict[str, tuple[bytes, int]]:
    """A pack's files (Yaz0 undone): name -> (bytes, alignment as stored)."""
    try:
        return {n: (d, a) for n, (d, a) in bnp.Blob(data).open().items()}
    except (wc.FormatError, struct.error, IndexError) as e:
        raise MapError(f"a pack can't be read ({e})") from None


# ---------------------------------------------------------------------------------------------- map objects

def srt_hash(key: str, pos, yaw) -> int:
    """The SRTHash map_object writes (from the text of key, pos and yaw: keep yaw a float)."""
    h = zlib.crc32(f"{key}:{pos}:{yaw}".encode())
    return h - (1 << 32) if h >= 1 << 31 else h


def map_object(key: str, actor: str, pos, ids: dict, *, yaw: float | None = None, params: dict | None = None,
               links: list | None = None, scale=None, extra: dict | None = None) -> dict:
    """A placed object: `key`'s HashId from `ids`, actor `actor` at `pos`, turned `yaw` degrees. params: its
    !Parameters; links: its LinksToObj (map_link); scale: its Scale; extra: other fields (e.g. {"OnlyOne": True})."""
    o: dict = {}
    if params is not None:
        o["!Parameters"] = params
    o["HashId"] = ids[key]
    if links:
        o["LinksToObj"] = links
    if extra:
        o.update(extra)
    if yaw is not None:
        o["Rotate"] = bm.f32(math.radians(yaw))
    o["SRTHash"] = srt_hash(key, pos, yaw)
    if scale is not None:
        o["Scale"] = [bm.f32(float(v)) for v in scale]
    o["Translate"] = [bm.f32(float(v)) for v in pos]
    o["UnitConfigName"] = actor
    return o


def map_link(kind: str, dest, params: dict | None) -> dict:
    """A LinksToObj entry: a `kind` link (BasicSig, Create, Delete, PlacementLOD...) to HashId `dest`."""
    out = {} if params is None else {"!Parameters": params}
    out.update({"DefinitionName": kind, "DestUnitHashId": dest})
    return out


def tag_params(flag: str | None = None) -> dict:
    """A LinkTag's parameters, reading save flag `flag` when given."""
    p = {"IncrementSave": False, "MakeSaveFlag": 0, "NoChangeSignal": False}
    if flag:
        p["SaveFlag"] = flag
    p["SaveFlagOnOffType"] = 0
    return p


# ---------------------------------------------------------------------------------------------- event flows

def check_flow(spec: dict) -> list[str]:
    """What's wrong with a flow description (an empty list: nothing)."""
    out = []
    ev = spec.get("events") or {}
    for entry, key in (spec.get("entry_points") or {}).items():
        if key not in ev:
            out.append(f"{spec['name']}<{entry}> starts at {key}, which isn't an event")
    for key, e in ev.items():
        nxt = [e.get("next")] if e.get("kind") != "switch" else list((e.get("cases") or {}).values())
        out += [f"{spec['name']}: {key} goes to {n}, which isn't an event" for n in nxt
                if n is not None and n not in ev]
        if e.get("kind") == "switch" and not e.get("cases"):
            out.append(f"{spec['name']}: {key} has no cases")
    return out


EVFL_API = ("Flow", "params", "dump", "load", "round_trip", "NONE", "EvflError")


def _evfl():
    """tools/evfl.py, or None while it isn't there (or lacks what mapkit uses)."""
    try:
        import evfl  # noqa: PLC0415 (S0's event flow writer)
    except ImportError:
        return None
    return evfl if all(hasattr(evfl, n) for n in EVFL_API) else None


def evfl_ready(shared: dict[str, tuple[bytes, int]], source: str = "the event pack") -> str | None:
    """None when tools/evfl.py can be trusted with our flows: it rebuilds every flow of the player's event pack
    `shared` (source: whose pack it is, for the message) byte for byte. Else why not."""
    evfl = _evfl()
    if evfl is None:
        return "tools/evfl.py isn't there yet" if not os.path.isfile(os.path.join(TOOLS, "evfl.py")) else \
            f"tools/evfl.py lacks {', '.join(EVFL_API)}"
    flows = {n: d for n, (d, _) in shared.items() if n.startswith("EventFlow/") and n.endswith(".bfevfl")}
    if not flows:
        return f"{source}'s event pack has no flows to check tools/evfl.py on"
    for name, data in sorted(flows.items()):
        try:
            ok, _, why = evfl.round_trip(data)
        except (struct.error, IndexError, ValueError, UnicodeDecodeError) as e:   # (a damaged file: not trusted)
            ok, why = False, f"{type(e).__name__}: {e}"
        if not ok:
            return f"tools/evfl.py doesn't rebuild your {name} byte for byte yet ({why})"
    return None


def build_flow(spec: dict) -> bytes:
    """The .bfevfl of a flow description, written by tools/evfl.py (evfl_ready first), checked by reading it back."""
    evfl = _evfl()
    if evfl is None:
        raise MapError("tools/evfl.py isn't there yet")
    try:
        f = evfl.Flow(spec["name"])
        at: dict[str, int] = {}
        for key, e in spec["events"].items():
            prm = evfl.params(**e["params"]) if e.get("params") else None
            if e["kind"] == "action":
                at[key] = f.act(e["actor"], e["action"], prm)
            elif e["kind"] == "switch":
                at[key] = f.switch(e["actor"], e["query"], None, prm)
            elif e["kind"] == "subflow":
                at[key] = f.subflow(e["flow"], e["entry"], prm)
            else:
                raise MapError(f"{spec['name']}: {key} is a {e['kind']!r} event, which mapkit doesn't write")
        for key, e in spec["events"].items():
            if e["kind"] == "switch":
                for value, nxt in e["cases"].items():
                    f.case(at[key], int(value), evfl.NONE if nxt is None else at[nxt])
            elif e.get("next") is not None:
                f.chain(at[key], at[e["next"]])
        for name, key in spec["entry_points"].items():
            f.entry(name, at[key])
        data = evfl.dump(f)
        if evfl.dump(evfl.load(data)) != data:
            raise MapError(f"tools/evfl.py doesn't read back its own {spec['name']} flow the same")
    except evfl.EvflError as e:
        raise MapError(f"tools/evfl.py can't write the {spec['name']} flow ({e})") from None
    return data


def event_pack(flow: str, data: bytes, shared: dict[str, tuple[bytes, int]], subflows: list[str],
               yaz0_align: int = 0, *, source: str, other_files: bool = False) -> tuple[bytes, int]:
    """Event/<flow>.sbeventpack: our flow plus the game's shared flows it calls, copied from the player's event pack
    `shared` (source: whose pack it is, for the message). other_files: also carry the pack's files that aren't flows
    (an NPC's talk sounds, named by its event info's slink_user), as a mod that takes over an NPC's talk needs.
    yaz0_align: the game pack's own (see yaz0_align). Returns (bytes, unpacked size)."""
    items = [(f"EventFlow/{flow}.bfevfl", data, ukmm.new_alignment("x.bfevfl", data, 8))]
    for sub in subflows:
        name = f"EventFlow/{sub}.bfevfl"
        if name not in shared:
            raise MapError(f"{source}'s event pack has no {name}")
        items.append((name, shared[name][0], shared[name][1]))
    if other_files:
        items += [(n, d, a) for n, (d, a) in sorted(shared.items()) if not n.startswith("EventFlow/")]
    raw = bnp.sarc_build(items)
    return ukmm.yaz0(raw, yaz0_align), len(raw)


def subflows_of(spec: dict) -> list[str]:
    """The other flows a flow description calls, sorted."""
    return sorted({e["flow"] for e in spec["events"].values() if e.get("kind") == "subflow" and e.get("flow")})


DOOR_INFO = {"is_timeline": False, "mode": "Seamless", "subfile": [{"file": "Common.bfevfl"}]}   # an event info
#                                                       entry for a map EventTag's event (Common only)
TALK_INFO = {"exist_enable_camera_input": True, "is_timeline": False, "mode": "Seamless",
             "subfile": [{"file": "InitTalk.bfevfl"}, {"file": "Common.bfevfl"}]}   # an NPC talk (InitTalk + Common)


def carried_flows(specs: dict[str, dict], infos: dict[str, dict]) -> dict[str, list[str]]:
    """The game's shared flows each event pack carries: the ones its flow calls and the ones its event info entries
    list (a villager's talk pack carries both InitTalk and Common, as its entry says)."""
    out = {}
    for name, spec in specs.items():
        have = set(subflows_of(spec))
        for key, info in infos.items():
            if key.split("<", 1)[0] == name:
                have |= {s["file"][:-len(".bfevfl")] for s in info.get("subfile") or []
                         if isinstance(s, dict) and isinstance(s.get("file"), str) and s["file"].endswith(".bfevfl")}
        out[name] = sorted(have - {name})
    return out


# ---------------------------------------------------------------------------------------------- constants

SIGNAL ={"NoAutoDemoMember": False}            # the BasicSig link parameters the game's own triggers use
AREA_BOX = {"AutoSave": False, "CameraPriority": 1, "CameraSet": "-", "ForceCalcInEvent": False, "Shape": "Box"}
APPEAR = {"AppearFade": False}                  # a Create link's parameters

__all__ = [
    "MapError", "map_object", "map_link", "tag_params", "srt_hash", "check_flow", "build_flow", "evfl_ready",
    "subflows_of", "event_pack", "carried_flows", "read_pack", "unyaz0", "TALK_INFO", "DOOR_INFO", "EVFL_API",
    "SIGNAL", "AREA_BOX", "APPEAR", "hash_id", "hash_ids", "trigger_chain", "flag_gate", "FlowSpec", "talk_params",
    "info_entries", "yaz0_align",
]


# ---------------------------------------------------------------------------------------------- mapkit's own

def hash_id(namespace: str, key: str, used: set[int]) -> bd.U32:
    """A HashId for one of a mod's objects, from "<namespace>/<key>" so a rebuild gives the same one; never 0 and never
    one in `used` (it is added there). Use one namespace per mod (e.g. "giruka.mymod"). castle_build's are
    namespace "giruka.castle-date", dragonbreath stage 2's "giruka.dragonbreath/stage2"."""
    h = zlib.crc32(f"{namespace}/{key}".encode())
    while h in used or h == 0:
        h = (h + 1) & 0xFFFFFFFF
    used.add(h)
    return bd.U32(h)


def hash_ids(namespace: str, keys, used: set[int]) -> dict[str, bd.U32]:
    """key -> HashId for each key, in order (the order matters only when two keys' hashes meet)."""
    out: dict[str, bd.U32] = {}
    for k in keys:
        if k in out:
            raise MapError(f"two objects called {k}")
        out[k] = hash_id(namespace, k, used)
    return out


def trigger_chain(prefix: str, pos, ids: dict, *, flow: str, entry: str, half, yaw: float = 0.0,
                  area_params: dict | None = None) -> list[dict]:
    """A walk-in trigger that plays flow<entry> once Link enters a box: <prefix>_area (an Area, half sizes `half`,
    turned `yaw` degrees) signals <prefix>_relay (a LinkTagAnd), which signals <prefix>_event (an EventTag). All three
    keys must be in `ids`. To make the trigger appear only later, leave _area out of the map's start (a flag_gate
    `creates` it); to remove it, a flag_gate `deletes` it."""
    area, relay, event = f"{prefix}_area", f"{prefix}_relay", f"{prefix}_event"
    for k in (area, relay, event):
        if k not in ids:
            raise MapError(f"trigger {prefix}: no HashId for {k}")
    return [
        map_object(area, "Area", pos, ids, yaw=float(yaw), scale=half,
                   params=copy.deepcopy(area_params or AREA_BOX), links=[map_link("BasicSig", ids[relay], dict(SIGNAL))]),
        map_object(relay, "LinkTagAnd", pos, ids, params=tag_params(),
                   links=[map_link("BasicSig", ids[event], dict(SIGNAL))]),
        map_object(event, "EventTag", pos, ids,
                   params={"EventFlowEntryName": entry, "EventFlowName": flow, "IsEndlessEvent": False,
                           "LaunchEventByOffSignal": False, "LaunchEventByOnSignal": True}),
    ]


def flag_gate(key: str, pos, ids: dict, flag: str | None, *, creates=(), deletes=()) -> dict:
    """A LinkTagAnd that only reads a save flag (no inputs): while `flag` is on it makes the `creates` objects appear
    (Create links) and removes the `deletes` ones (Delete links). Keys, all in `ids`."""
    if not creates and not deletes:
        raise MapError(f"gate {key} creates and deletes nothing")
    for k in (key, *creates, *deletes):
        if k not in ids:
            raise MapError(f"gate {key}: no HashId for {k}")
    links = [map_link("Create", ids[k], dict(APPEAR)) for k in creates] + \
        [map_link("Delete", ids[k], None) for k in deletes]
    return map_object(key, "LinkTagAnd", pos, ids, params=tag_params(flag), links=links)


class FlowSpec:
    """Builds the flow description build_flow and check_flow take, with short keys. Each method returns the key, so
    events chain by name: f.act("a", ..., nxt="b"). `spec` is the plain dict."""

    def __init__(self, name: str):
        self.spec: dict = {"name": name, "entry_points": {}, "events": {}}

    def ev(self, key: str, e: dict) -> str:
        if key in self.spec["events"]:
            raise MapError(f"two events called {key}")
        self.spec["events"][key] = e
        return key

    def act(self, key: str, actor: str, action: str, params: dict | None = None, nxt: str | None = None) -> str:
        return self.ev(key, {"kind": "action", "actor": actor, "action": action, "params": params or {}, "next": nxt})

    def sw(self, key: str, actor: str, query: str, params: dict | None, cases: dict) -> str:
        """A switch: cases maps each answer (an int) to the next key, or None to end there."""
        return self.ev(key, {"kind": "switch", "actor": actor, "query": query, "params": params or {}, "cases": cases})

    def sub(self, key: str, flow: str, entry: str, params: dict | None = None, nxt: str | None = None) -> str:
        return self.ev(key, {"kind": "subflow", "flow": flow, "entry": entry, "params": params or {}, "next": nxt})

    def entry(self, name: str, key: str) -> str:
        if name in self.spec["entry_points"]:
            raise MapError(f"two entry points called {name}")
        self.spec["entry_points"][name] = key
        return key

    def problems(self) -> list[str]:
        return check_flow(self.spec)


def talk_params(msg_file: str, label: str, close: bool, anim: str = "") -> dict:
    """A Demo_Talk action's parameters: the message EventFlowMsg/<msg_file>:<label>, the talk box closed after it
    when `close`, the speaker's animation `anim` ("" for none)."""
    return {"MessageId": f"EventFlowMsg/{msg_file}:{label}", "IsWaitFinish": True, "IsCloseMessageDialog": close,
            "ASName": anim, "IsBecomingSpeaker": True, "IsOverWriteLabelActorName": False}


def info_entries(spec: dict, default: dict | None = None, per_entry: dict[str, dict] | None = None) -> dict[str, dict]:
    """Event info entries ("Flow<Entry>" -> entry) for every entry point of a flow: per_entry[entry] when given, else
    `default` (TALK_INFO when None). Copies, so editing one never changes another."""
    per_entry = per_entry or {}
    base = default if isinstance(default, dict) else TALK_INFO
    return {f"{spec['name']}<{e}>": copy.deepcopy(per_entry.get(e, base)) for e in spec["entry_points"]}


def yaz0_align(raw: bytes) -> int:
    """The alignment a Yaz0 file states (0 when it isn't Yaz0): pass a game pack's to event_pack for a twin."""
    return struct.unpack_from(">I", raw, 8)[0] if raw[:4] == b"Yaz0" and len(raw) >= 12 else 0
