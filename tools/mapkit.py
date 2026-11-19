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

FOR NOW this module is the new HOME, not yet the code's home: the low-level helpers still live in
tools/castle_build.py (which tools/dragonbreath_stage2.py also imports), and mapkit re-exports them, so a new mod can
`import mapkit` today and keep working when the code moves here. The steps for that move are in
out/research/mapkit-handoff.md. The new helpers below (hash_id, FlowSpec, trigger_chain, flag_gate, talk_params,
info_entries, yaz0_align) are mapkit's own and make exactly the objects both castle_build and dragonbreath_stage2
write by hand today (tests/test_mapkit.py checks that, object for object).
"""
from __future__ import annotations

import copy
import os
import struct
import sys
import zlib

TOOLS = os.path.dirname(os.path.abspath(__file__))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import bcml_data as bd  # noqa: E402
import castle_build as _cb  # noqa: E402  (the helpers' current home; see the docstring)

# ---------------------------------------------------------------------------------------------- re-exported (castle_build)

MapError = _cb.CastleError      # what every helper raises: something the mod's author (or player) has to fix

map_object = _cb.map_object     # (key, actor, pos, ids, *, yaw=None, params=None, links=None, scale=None, extra=None)
map_link = _cb.map_link         # (kind, dest_hash_id, params_or_None)
tag_params = _cb.tag_params     # (flag=None): a LinkTag's parameters, reading `flag` when given
srt_hash = _cb._srt_hash        # (key, pos, yaw): the SRTHash map_object writes

check_flow = _cb.check_flow     # (spec) -> list of problems (empty: fine)
build_flow = _cb.build_flow     # (spec) -> .bfevfl bytes, via tools/evfl.py
evfl_ready = _cb.evfl_ready     # (shared) -> None when tools/evfl.py rebuilds that pack's flows byte for byte
subflows_of = _cb.subflows_of   # (spec) -> the other flows it calls, sorted
event_pack = _cb.event_pack     # (flow, data, shared, subflows, yaz0_align=0, *, source=..., other_files=False)
carried_flows = _cb.carried_flows   # (specs, infos) -> flow -> the shared flows its pack must carry

read_pack = _cb._sarc_files     # (pack bytes, Yaz0 or not) -> name -> (bytes, alignment)
unyaz0 = _cb._unyaz0            # (bytes) -> bytes, Yaz0 undone when it is Yaz0

TALK_INFO = _cb.TALK_INFO       # an event info entry for an NPC talk (InitTalk + Common)
DOOR_INFO = _cb.DOOR_INFO       # an event info entry for a map EventTag's event (Common only)
EVFL_API = _cb.EVFL_API

SIGNAL = {"NoAutoDemoMember": False}            # the BasicSig link parameters the game's own triggers use
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
