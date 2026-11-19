# mapkit: place things in Hyrule

`tools/mapkit.py` is the shared toolkit for mods that add objects to the map and events to the game: place an
object, link objects together, make a walk-in trigger, show or hide things with a save flag, describe an event flow
and pack it as the game wants. The castle date (`tools/castle_build.py`) and Dragonbreath's stage 2
(`tools/dragonbreath_stage2.py`) use these same helpers; a new map mod should `import mapkit` and nothing else.

Status: **offline only.** The helpers make exactly the objects and packs the castle date and Dragonbreath stage 2
write (`tests/test_mapkit.py` checks that, object for object); neither has been tried in the game yet. For now the
low-level helpers still live in `castle_build.py` and mapkit re-exports them; your code won't notice when they move.

## The ideas in 30 seconds

- A **map object** is one entry in a map unit (e.g. `E-4_Dynamic`): an actor name, a spot, a turn, parameters,
  links. Every object has a **HashId**, a number unique in the whole map. Links point at HashIds.
- Pick a **namespace** for your mod (e.g. `"giruka.mymod"`) and give every object a short **key** (`"door"`).
  `hash_ids(namespace, keys, used)` turns keys into HashIds that stay the same on every rebuild and never hit one in
  `used` (pass the HashIds already in the map, the game's and other mods').
- A **LinkTagAnd** that only reads a save flag is a **gate**: while the flag is on, it makes objects appear (Create)
  or go away (Delete). Objects a gate creates are not there until the flag is on.
- A **trigger** is three objects: an `Area` box that signals a `LinkTagAnd` relay that signals an `EventTag`, which
  plays one entry point of an event flow when Link walks in.
- An **event flow** (`.bfevfl`) is a graph of actions (an actor does something), switches (an actor answers a
  question) and subflows (calls another flow, like the game's shared `InitTalk` or `Common`). You describe it with
  `FlowSpec`; `build_flow` writes the file (through `tools/evfl.py`).
- An **event pack** (`Event/<Flow>.sbeventpack`) is your flow plus the game's shared flows it calls, copied from a
  game pack you name. Its **event info** entries (`"Flow<Entry>"`) tell the game how to play each entry point.

## The API

```python
import mapkit as mk
```

| Name | What it does |
| --- | --- |
| `hash_id(ns, key, used)` / `hash_ids(ns, keys, used)` | Stable HashIds from your keys; never 0, never one in `used` (added to it). |
| `map_object(key, actor, pos, ids, *, yaw=None, params=None, links=None, scale=None, extra=None)` | One placed object. `yaw` in degrees; `extra` adds fields such as `{"OnlyOne": True}`. |
| `map_link(kind, dest_hash_id, params)` | One link: `"Create"`, `"Delete"`, `"BasicSig"`, `"PlacementLOD"`... |
| `tag_params(flag=None)` | A LinkTag's parameters, reading `flag` when given. |
| `trigger_chain(prefix, pos, ids, *, flow, entry, half, yaw=0.0)` | The Area -> relay -> EventTag trigger: keys `<prefix>_area`, `_relay`, `_event`. |
| `flag_gate(key, pos, ids, flag, *, creates=(), deletes=())` | A gate on a save flag. |
| `FlowSpec(name)` | Flow builder: `.act`, `.sw`, `.sub`, `.entry`, `.problems()`; `.spec` is the plain dict. |
| `talk_params(msg_file, label, close, anim="")` | A `Demo_Talk` action's parameters (message `EventFlowMsg/<msg_file>:<label>`). |
| `check_flow(spec)` | What's wrong with a flow (empty list: nothing). |
| `subflows_of(spec)` | The shared flows a flow calls. |
| `evfl_ready(shared)` | `None` when `tools/evfl.py` rebuilds a game pack's flows byte for byte, else why not. Check it first. |
| `build_flow(spec)` | The `.bfevfl` bytes. |
| `event_pack(flow, data, shared, subflows, yaz0_align=0, *, source, other_files=False)` | `(pack bytes, unpacked size)`. `other_files`: also carry the source pack's non-flow files (an NPC's talk sounds). |
| `info_entries(spec, default=None, per_entry=None)` | Event info entries for every entry point (`TALK_INFO` by default). |
| `carried_flows(specs, infos)` | Which shared flows each pack must carry (calls plus event info subfiles). |
| `read_pack(data)`, `unyaz0(data)`, `yaz0_align(raw)` | Read a pack's files; undo Yaz0; a Yaz0 file's stated alignment. |
| `TALK_INFO`, `DOOR_INFO`, `AREA_BOX`, `SIGNAL`, `APPEAR` | The game's usual values (talk info, EventTag info, box area, signal and Create link parameters). |
| `MapError` | Raised for anything the author has to fix; the message says what. |

## A small example: a door and a greeting that appear after a flag

```python
import mapkit as mk

NS = "giruka.mymod"
game = cb.find_game()                      # (import castle_build as cb: the player's Savage Wind files)
used = game.hash_ids() | set(cb.other_hash_ids(lib, "mymod"))   # in use: the game's, other mods' (lib: modman.Library)
spot = [1530.0, 245.0, -1840.0]            # x, y, z in metres: use floats (the SRTHash is made from their text)
ids = mk.hash_ids(NS, ["door", "hi_area", "hi_relay", "hi_event", "gate"], used)

objects = [mk.map_object("door", "TwnObj_Village_Hatenodoor_A_01", spot, ids, yaw=90.0,
                          params={"DropTable": "Normal", "SharpWeaponJudgeType": 0})]
objects += mk.trigger_chain("hi", spot, ids, flow="MyMod", entry="Hello", half=[2.0, 2.0, 2.0], yaw=90.0)
objects.append(mk.flag_gate("gate", spot, ids, "MyMod_Unlocked", creates=["door", "hi_area"]))
objects.sort(key=lambda o: int(o["HashId"]))      # (maps keep objects in HashId order)

f = mk.FlowSpec("MyMod")
f.entry("Hello", f.act("hi", "MyNpc", "Demo_Talk", mk.talk_params("MyMod", "Hello", close=True)))
assert not f.problems()                   # (MyNpc: an NPC your mod places, like the castle's SwZelda)

raw = game.read("Event/Npc_HyruleDepthHatago001.sbeventpack")   # a game pack that has the shared flows you call
shared = mk.read_pack(raw)
why = mk.evfl_ready(shared)
if why:
    print("no event this time:", why)      # place the map without the trigger rather than ship a broken flow
else:
    data = mk.build_flow(f.spec)
    pack, size = mk.event_pack("MyMod", data, shared, mk.subflows_of(f.spec), mk.yaz0_align(raw),
                               source="Npc_HyruleDepthHatago001")
    infos = mk.info_entries(f.spec, mk.DOOR_INFO)    # an EventTag's event: Common only
```

Then write `Event/MyMod.sbeventpack`, put `objects` in your map log, `infos` in your event info log and
`MyMod_Unlocked` in your flags log (see `tools/dragonbreath_stage2.py` for a full mod that does all of it: a cave,
a chest, a gated NPC, a first-draw trigger and a quest).

## Things that bite

- **Floats.** `map_object` makes the SRTHash from the text of `pos` and `yaw`: `90` and `90.0` give different hashes.
  Always pass floats.
- **One namespace per mod**, and never rename a key once players have a save: the HashId comes from it.
- **Pass the map's HashIds as `used`**, including other mods' (`castle_build.other_hash_ids` reads a library's).
- **Created objects start hidden.** Anything a `flag_gate` creates isn't there until its flag is on; to make a
  trigger appear later, gate its `_area` (the relay and event can stay).
- **`evfl_ready` first.** If `tools/evfl.py` can't rebuild the game's own flows byte for byte, don't ship yours: place
  the objects without the trigger instead (both the castle and Dragonbreath do this).
- **`other_files=True`** when you take over an NPC's talk: its talk sounds live in its own event pack.
