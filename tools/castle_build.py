#!/usr/bin/env python3
"""Castle date (a Breathalyzer showcase mod, by giruka): builds the mod on your PC from your own game files and
adds it to your mod library with modman.

    python tools/castle_build.py build [--library DIR] [--unit static|dynamic] [--cloth] [--no-events]
                                       [--no-add --out DIR]
    python tools/castle_build.py check [--library DIR]      offline checks on your files (reads only)
    python tools/castle_build.py where [--library DIR]      the game files it reads

Stage 1 (docs/castle-date.md): after the game is beaten (the game's own `GameClear` flag), three doors stand in
Princess Zelda's Room. The right one takes you to her Study, where Zelda (silent) greets you with our text and one
choice; a door there takes you back. Nothing from the game is in this repo or in what we ship: every pack, text,
map change and size entry is made here from the player's `extracted\\romfs` (only read; never the dump's own
folders). Our own parts are in castle/: mod.toml, layout.json (where things go) and texts.json (Zelda's lines).

What `build` makes (a BCML-style package folder that `modman add` installs, so the map, actor info, event info and
text changes are merged by tools/bcml_maps.py, bcml_data.py and bcml_texts.py the normal way):

  Actor/Pack/SwZelda.sbactorpack   Zelda from Dm_Npc_Zelda_Sibyl: talk attention (NpcStandard and its 5 files from a
                                   villager's pack), light physics without cloth (cloth costs 3-8 fps; --cloth keeps
                                   it), her actor link renamed
  logs/actorinfo.yml               SwZelda's actor info (Dm_Npc_Zelda_Sibyl's, renamed)
  logs/map.yml                     E-4_Static (in TitleBG.pack) or E-4_Dynamic: a LinkTagAnd that reads GameClear and
                                   has no inputs (so it never writes it) creates 3 doors in the Room, Zelda, a door in
                                   the Study and the two door triggers (Area -> LinkTagAnd -> EventTag)
  logs/texts.json                  Zelda's name and lines (EventFlowMsg/SwZelda), no voice
  logs/eventinfo.yml, Event/...    SwZelda<Talk>, SwCastleDoor<ToStudy>/<ToRoom> and their event packs (with the
                                   game's InitTalk and Common flows they call or their event info lists, copied
                                   from a villager's event pack): only when tools/evfl.py rebuilds that pack's flows
                                   byte for byte; without it (or with --no-events) the doors and Zelda are placed,
                                   but no trigger or talk event is (a missing event could stop the game)
  logs/rstb.json                   size entries for the new files, from the game's entries for the files they come from

With --unit static the castle's map change lands in Pack/TitleBG.pack (modman add merges logs/map.yml into your
game's copy). Another mod that ships TitleBG.pack too (Linkle's body, say) still works next to it, in any load order:
modman deploy merges two mods' packs file by file and applies the castle's map log again on the merged copy
(bnp.merge_packages), so nothing here depends on the other mod and nothing needs rebuilding when it changes.

The event flows are described as plain data (flow_specs): {"name", "entry_points": {entry: event key}, "events":
{key: event}}, an event being {"kind": "action", "actor", "action", "params", "next"}, {"kind": "switch", "actor",
"query", "params", "cases": {value: key}} or {"kind": "subflow", "flow", "entry", "params", "next"}; "next" None ends
the flow, params map names to bool, int, float or str values. build_flow writes them with tools/evfl.py's builder.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import textwrap
import tomllib
import zlib
from dataclasses import dataclass, field

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import aamp  # noqa: E402
import bcml_data as bd  # noqa: E402
import bcml_maps as bm  # noqa: E402
import bcml_texts as bt  # noqa: E402
import bnp  # noqa: E402
import mapkit as mk  # noqa: E402  (the map, event and flow helpers: docs/mapkit.md)
import modman as mm  # noqa: E402
import ukmm  # noqa: E402
import wiiu_convert as wc  # noqa: E402

REPO = os.path.dirname(TOOLS)
RECIPE = os.path.join(REPO, "castle")
BASE_TID = "01007EF00011E000"
ACTOR = "SwZelda"                       # our talkable Zelda (actor name, event flow and message file)
DOOR_FLOW = "SwCastleDoor"              # the door warps (event flow)
ZELDA_SRC = "Dm_Npc_Zelda_Sibyl"        # the player's Zelda in the white dress: the only Zelda with a pack
VILLAGER = "Npc_HyruleDepthHatago001"   # a talking villager: her attention files and event pack's shared flows
GATE_FLAG = "GameClear"                 # set by the game's clear autosave, never reset
LIGHT_PHYSICS = "SwZelda_Light"
ATTENTION = ["Actor/AttClientList/NpcStandard.batcllist", "Actor/AttClient/NpcStandard_Talk.batcl",
             "Actor/AttClient/NpcStandard_LockOn.batcl", "Actor/AttClient/NpcStandard_Awake.batcl",
             "Actor/AttClient/NpcStandard_AwakeLockOn.batcl"]
TITLEBG = "Pack/TitleBG.pack"
FILES = {   # what build and check read, in extracted\romfs
    "zelda": f"Actor/Pack/{ZELDA_SRC}.sbactorpack",
    "villager": f"Actor/Pack/{VILLAGER}.sbactorpack",
    "villager_events": f"Event/{VILLAGER}.sbeventpack",
    "actorinfo": "Actor/ActorInfo.product.sbyml",
    "bootup": "Pack/Bootup.pack",
    "titlebg": TITLEBG,
    "rstb": "System/Resource/ResourceSizeTable.product.srsizetable",
}
MESSAGES = "Pack/Bootup_USen.pack"      # check only: the English texts
ROMFS_PREFIXES = ("romfs/", f"{BASE_TID}/romfs/", "")
# Actor kinds a placed object must never be (each would need its own save flag, or is an enemy)
BAD_PROFILES = {"Item", "PlayerItem", "Enemy", "GiantEnemy", "Guardian", "Prey", "Swarm", "Horse", "Dragon",
                "Sandworm", "SiteBoss", "LastBoss", "GelEnemy", "CapturedActor", "ArmorHead", "ArmorUpper",
                "ArmorLower", "WeaponSmallSword", "WeaponLargeSword", "WeaponSpear", "WeaponBow", "WeaponShield"}
TEXT_WIDTH = 40                         # characters per line in the talk box (the game's own lines: 99% <= 45)
OUT_FILE = "BUILT.md"


CastleError = mk.MapError     # (the same class: `except CastleError` catches mapkit's errors)


def _log(log, msg: str) -> None:
    if log:
        log(msg)


# ---------------------------------------------------------------------------------------------------- the recipe

@dataclass
class Recipe:
    manifest: dict
    layout: dict
    texts: dict
    toml_text: str


def _f3(v, what: str) -> list[float]:
    if not (isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                         and math.isfinite(x) for x in v)):
        raise CastleError(f"castle/layout.json: {what} isn't three numbers")
    return [float(x) for x in v]


def load_recipe(folder: str = RECIPE) -> Recipe:
    """castle/mod.toml, layout.json and texts.json, checked."""
    try:
        with open(os.path.join(folder, "mod.toml"), encoding="utf-8") as f:
            toml_text = f.read()
        manifest = tomllib.loads(toml_text)
        with open(os.path.join(folder, "layout.json"), encoding="utf-8") as f:
            layout = json.load(f)
        with open(os.path.join(folder, "texts.json"), encoding="utf-8") as f:
            texts = json.load(f)
    except (OSError, ValueError) as e:
        raise CastleError(f"The castle recipe in {folder} can't be read ({e}).") from None
    mod = manifest.get("mod") or {}
    if not isinstance(mod.get("id"), str) or not isinstance(mod.get("version"), str):
        raise CastleError("castle/mod.toml has no [mod] id and version")
    doors = layout.get("doors")
    if not isinstance(doors, list) or not doors or not all(isinstance(d, dict) and isinstance(d.get("key"), str)
                                                           for d in doors):
        raise CastleError("castle/layout.json has no doors")
    for d in doors:
        _f3(d.get("pos"), d["key"])
        if d.get("warp"):
            if d["warp"] not in (layout.get("warps") or {}):
                raise CastleError(f"castle/layout.json: {d['key']} warps to {d['warp']}, which isn't in warps")
            _f3((d.get("trigger") or {}).get("pos"), f"{d['key']} trigger pos")
            _f3((d.get("trigger") or {}).get("half"), f"{d['key']} trigger half")
    for name, w in (layout.get("warps") or {}).items():
        _f3(w.get("pos"), f"warp {name}")
    _f3((layout.get("zelda") or {}).get("pos"), "zelda pos")
    for k in ("room", "study"):
        _f3((layout.get("tags") or {}).get(k), f"tags {k}")
    talk = texts.get("talk")
    if not isinstance(talk, dict) or not isinstance(talk.get("text"), str):
        raise CastleError("castle/texts.json has no talk text")
    choices = talk.get("choices") or []
    if choices and not 2 <= len(choices) <= 4:
        raise CastleError("castle/texts.json: a talk has 2 to 4 choices")
    if not all(isinstance(c, dict) and isinstance(c.get("text"), str) for c in choices):
        raise CastleError("castle/texts.json: a choice has no text")
    if texts.get("actor") != ACTOR:
        raise CastleError(f"castle/texts.json is for {texts.get('actor')!r}, not {ACTOR}")
    return Recipe(mod, layout, texts, toml_text)


# ---------------------------------------------------------------------------------------------------- the game

_unyaz0, _sarc_files = mk.unyaz0, mk.read_pack     # (mapkit's; old names kept)


class Game:
    """The player's game files (extracted\\romfs, only read), each read once."""

    def __init__(self, install: str):
        self.install = install
        self.extracted = os.path.join(install, "extracted")
        self.romfs = os.path.join(self.extracted, "romfs")
        self._cache: dict[str, object] = {}

    def path(self, rel: str) -> str:
        return os.path.join(self.romfs, *rel.split("/"))

    def missing(self, keys=None) -> list[str]:
        return [FILES[k] for k in (keys or FILES) if not os.path.isfile(self.path(FILES[k]))]

    def read(self, rel: str) -> bytes:
        try:
            with open(self.path(rel), "rb") as f:
                return f.read()
        except OSError as e:
            raise CastleError(f"Your game's {rel} can't be read ({e.strerror or e}).") from None

    def _get(self, key: str, make):
        if key not in self._cache:
            self._cache[key] = make()
        return self._cache[key]

    def pack(self, key: str) -> dict[str, tuple[bytes, int]]:
        return self._get("pack:" + key, lambda: _sarc_files(self.read(FILES[key])))

    def pack_raw_len(self, key: str) -> int:
        return ukmm.unpacked_size(self.read(FILES[key]))

    def actorinfo(self) -> dict[str, dict]:
        def make():
            root = bd.byml_load(_unyaz0(self.read(FILES["actorinfo"])))
            return {a["name"]: a for a in root.get("Actors", []) if isinstance(a, dict) and isinstance(a.get("name"),
                                                                                                     str)}
        return self._get("actorinfo", make)

    def bootup(self) -> bnp.Blob:
        return self._get("bootup", lambda: bnp.Blob(self.read(FILES["bootup"])))

    def eventinfo(self) -> dict:
        def make():
            b = self.bootup().child("Event/EventInfo.product.sbyml")
            if b is None:
                raise CastleError("Your game's Bootup.pack has no Event/EventInfo.product.sbyml")
            return bd.byml_load(b.raw)
        return self._get("eventinfo", make)

    def bool_flags(self) -> set[str]:
        def make():
            gd = self.bootup().child("GameData/gamedata.ssarc")
            if gd is None:
                raise CastleError("Your game's Bootup.pack has no GameData/gamedata.ssarc")
            out = set()
            for name in gd.open():
                if "bool_data" not in name or "array" in name:
                    continue
                root = bd.byml_load(gd.child(name).raw)
                for rows in (root.values() if isinstance(root, dict) else []):
                    out |= {r.get("DataName") for r in rows if isinstance(r, dict) and isinstance(r.get("DataName"),
                                                                                               str)}
            return out
        return self._get("flags", make)

    def rstb(self) -> bnp.Rstb | None:
        return self._get("rstb", lambda: bnp.load_game_rstb(self.extracted))

    def titlebg(self):
        """(raw TitleBG.pack, its SARC index)."""
        def make():
            raw = _unyaz0(self.read(TITLEBG))
            try:
                return raw, wc.sarc_parse(raw)
            except wc.FormatError as e:
                raise CastleError(f"Your game's {TITLEBG} can't be read ({e})") from None
        return self._get("titlebg", make)

    def unit(self, unit: str):
        """A map unit ("E-4_Static", "E-4_Dynamic") as typed values."""
        def make():
            target = bm.map_target(unit)
            if target.startswith(TITLEBG + "//"):
                raw, s = self.titlebg()
                inner = target.split("//", 1)[1]
                node = next((n for n in s.nodes if n.name == inner), None)
                if node is None:
                    raise CastleError(f"Your game's {TITLEBG} has no {inner}")
                data = raw[s.data_offset + node.start:s.data_offset + node.end]
            else:
                data = self.read(target)
            return bm.byml_load(data)
        return self._get("unit:" + unit, make)

    def hash_ids(self, log=None) -> set[int]:
        """Every HashId in the main field's map units (statics in TitleBG.pack and the loose dynamics)."""
        def make():
            _log(log, "Reading every main field map unit for the HashIds in use (about 10 s)...")
            ids: set[int] = set()

            def add(root):
                for o in (root.get("Objs") or []) + (root.get("Rails") or []) if isinstance(root, dict) else []:
                    h = o.get("HashId") if isinstance(o, dict) else None
                    if isinstance(h, int):
                        ids.add(h & 0xFFFFFFFF)
            raw, s = self.titlebg()
            for n in s.nodes:
                if n.name.startswith("Map/MainField/") and n.name.endswith("_Static.smubin"):
                    add(bm.byml_load(raw[s.data_offset + n.start:s.data_offset + n.end]))
            base = self.path("Map/MainField")
            for u in sorted(os.listdir(base)) if os.path.isdir(base) else []:
                p = os.path.join(base, u, f"{u}_Dynamic.smubin")
                if os.path.isfile(p):
                    with open(p, "rb") as f:
                        add(bm.byml_load(f.read()))
            return ids
        return self._get("hash_ids", make)


def find_game(install: str | None = None, lib: mm.Library | None = None) -> Game:
    """The player's game: --game, else the library's Savage Wind folder, else the default one. Only its
    extracted\\romfs is read."""
    install = install or (lib.install if lib else None) or mm.default_install()
    game = Game(os.path.abspath(install))
    if not os.path.isdir(game.romfs):
        raise CastleError(f"Savage Wind's game files aren't in {game.romfs}. Set them up in Savage Wind first, or "
                          "pass --game <Savage Wind folder>.")
    gone = game.missing()
    if gone:
        raise CastleError("Your game files lack what the castle is built from: " + ", ".join(gone))
    return game


# ---------------------------------------------------------------------------------------------------- Zelda's pack

def _aamp_edit(data: bytes, edit) -> bytes:
    root = aamp.load(data)
    edit(root)
    return aamp.dump(root, aamp.type_name(data) or "xml", struct.unpack_from("<I", data, 0x10)[0])


def _set_text(obj: aamp.PObject, name: str, value: str) -> None:
    old = obj.get(aamp.name_hash(name))
    obj[aamp.name_hash(name)] = aamp.Param(old.type if old else aamp.STRING_REF, value)


@dataclass
class Pack:
    data: bytes                    # as stored (Yaz0)
    raw_len: int                   # unpacked size
    inner: dict[str, bytes]        # the files we made, by name (for size entries)
    refs: dict[str, str]           # each of them -> the game file it was made from


def zelda_pack(zelda: dict[str, tuple[bytes, int]], villager: dict[str, tuple[bytes, int]], *,
               cloth: bool = False, yaz0_align: int = 0) -> Pack:
    """SwZelda's actor pack from the player's Zelda and villager packs (name -> (bytes, alignment))."""
    link_name = f"Actor/ActorLink/{ZELDA_SRC}.bxml"
    if link_name not in zelda:
        raise CastleError(f"{ZELDA_SRC}'s pack has no {link_name}")
    gone = [a for a in ATTENTION if a not in villager]
    if gone:
        raise CastleError(f"{VILLAGER}'s pack lacks {', '.join(gone)}")
    link = aamp.load(zelda[link_name][0])
    target = link.object("LinkTarget")
    if target is None:
        raise CastleError(f"{ZELDA_SRC}'s actor link has no LinkTarget")
    physics_user = target.get_value("PhysicsUser")
    old_physics = f"Actor/Physics/{physics_user}.bphysics"
    made: dict[str, bytes] = {}
    refs = {f"Actor/ActorLink/{ACTOR}.bxml": link_name}
    items = []
    drop = set()
    if not cloth:
        if old_physics not in zelda:
            raise CastleError(f"{ZELDA_SRC}'s pack has no {old_physics}")

        def no_cloth(root: aamp.PList) -> None:
            ps = root.list("ParamSet")
            if ps is None:
                raise CastleError(f"{old_physics} has no ParamSet")
            ps.lists.pop(aamp.name_hash("Cloth"), None)
            for obj in ps.objects.values():
                if aamp.name_hash("use_cloth") in obj:
                    obj[aamp.name_hash("use_cloth")] = aamp.Param(aamp.BOOL, False)
        made[f"Actor/Physics/{LIGHT_PHYSICS}.bphysics"] = _aamp_edit(zelda[old_physics][0], no_cloth)
        refs[f"Actor/Physics/{LIGHT_PHYSICS}.bphysics"] = old_physics
        drop.add(old_physics)
        drop |= {n for n in zelda if n.startswith("Physics/Cloth/")}

    def relink(root: aamp.PList) -> None:
        t = root.object("LinkTarget")
        _set_text(t, "AttentionUser", "NpcStandard")
        if not cloth:
            _set_text(t, "PhysicsUser", LIGHT_PHYSICS)
    made[f"Actor/ActorLink/{ACTOR}.bxml"] = _aamp_edit(zelda[link_name][0], relink)
    drop.add(link_name)
    for name, (data, align) in zelda.items():
        if name not in drop:
            items.append((name, data, align))
    for name in ATTENTION:
        if name not in zelda:
            data, align = villager[name]
            items.append((name, data, max(align, 4)))
    for name, data in made.items():
        items.append((name, data, ukmm.new_alignment(name, data, 8)))
    raw = bnp.sarc_build(items)
    return Pack(ukmm.yaz0(raw, yaz0_align), len(raw), made, refs)


# ---------------------------------------------------------------------------------------------------- texts

def _wrap(text: str) -> str:
    """Our lines with the line breaks the game's message box needs (it doesn't wrap by itself)."""
    return "\n".join(textwrap.fill(p, TEXT_WIDTH, break_long_words=False) for p in text.split("\n"))


def talk_messages(texts: dict) -> tuple[dict[str, dict], dict[str, str]]:
    """(the msyt labels of EventFlowMsg/SwZelda: label -> {"contents", "attributes"}, the choice labels: the reply's
    label -> its choice label)."""
    talk = texts["talk"]
    msgs: dict[str, dict] = {}
    choices = talk.get("choices") or []
    contents = [{"text": _wrap(talk["text"])}]
    if choices:
        n = len(choices)
        contents.append({"control": {"kind": "choice", "choice_labels": list(range(n)), "selected_index": 0,
                                     "cancel_index": n - 1, "unknown": 2 * n + 2}})
    msgs[talk["label"]] = {"contents": contents, "attributes": ACTOR}
    by_reply = {}
    for i, c in enumerate(choices):
        msgs[f"{i:04d}"] = {"contents": [{"text": c["text"]}], "attributes": ""}
        r = c.get("reply")
        if r:
            msgs[r["label"]] = {"contents": [{"text": _wrap(r["text"])}], "attributes": ACTOR}
            by_reply[r["label"]] = f"{i:04d}"
    return msgs, by_reply


def texts_log(texts: dict) -> dict:
    """BCML's texts.json: Zelda's name (ActorType/NPC) and lines (EventFlowMsg/SwZelda), English only (the game's
    other languages get them too, as BCML does)."""
    msgs, _ = talk_messages(texts)
    return {"USen": {f"EventFlowMsg/{ACTOR}.msyt": msgs,
                     "ActorType/NPC.msyt": {f"{ACTOR}_Name": {"contents": [{"text": texts.get("name", "Zelda")}]}}}}


# ---------------------------------------------------------------------------------------------------- event flows

def _demo_talk(label: str, close: bool, anim: str = "") -> dict:
    return mk.talk_params(ACTOR, label, close, anim)


def flow_specs(layout: dict, texts: dict) -> dict[str, dict]:
    """The two event flows, described for tools/evfl.py (see the module's docstring)."""
    talk = texts["talk"]
    choices = talk.get("choices") or []
    ev: dict[str, dict] = {
        "init": {"kind": "subflow", "flow": "InitTalk", "entry": "InitTalk",
                 "params": {"Arg_Turn": 0, "Arg_Greeting": "FollowAISchedule"}, "next": "greet"},
        "greet": {"kind": "action", "actor": ACTOR, "action": "Demo_Talk",
                  "params": _demo_talk(talk["label"], not choices, talk.get("anim", "")),
                  "next": "choice" if choices else None},
    }
    if choices:
        ev["choice"] = {"kind": "switch", "actor": "EventSystemActor", "query": f"GeneralChoice{len(choices)}",
                        "params": {}, "cases": {}}
        for i, c in enumerate(choices):
            r = c.get("reply")
            if r:
                key = f"reply{i}"
                ev[key] = {"kind": "action", "actor": ACTOR, "action": "Demo_Talk",
                           "params": _demo_talk(r["label"], True, r.get("anim", "")), "next": None}
                ev["choice"]["cases"][i] = key
    zelda = {"name": ACTOR, "entry_points": {"Talk": "init"}, "events": ev}
    door: dict = {"name": DOOR_FLOW, "entry_points": {}, "events": {}}
    # a door: fade out, saving and warping blocked (the game's Common flow; not saved), Link still, moved, fade in,
    # unblocked (the same steps and values as the game's own Common<ResetPlayerPosition>)
    fade = {"IsWaitFinish": True, "Frame": 0, "Color": 1, "DispMode": ""}
    for name, w in sorted((layout.get("warps") or {}).items()):
        x, y, z = (float(v) for v in w["pos"])
        k = name.lower()
        door["entry_points"][name] = f"{k}_out"
        door["events"].update({
            f"{k}_out": {"kind": "action", "actor": "Fader", "action": "Demo_FadeOut", "params": dict(fade),
                         "next": f"{k}_lock"},
            f"{k}_lock": {"kind": "subflow", "flow": "Common", "entry": "DisableSaveAndWarp", "params": {},
                          "next": f"{k}_still"},
            f"{k}_still": {"kind": "action", "actor": "GameROMPlayer", "action": "Demo_Idling",
                           "params": {"IsWaitFinish": True, "DisablePhysics": False}, "next": f"{k}_warp"},
            f"{k}_warp": {"kind": "action", "actor": "EventSystemActor", "action": "Demo_WarpPlayerToDestination",
                          "params": {"IsWaitFinish": True, "DestinationX": x, "DestinationY": y, "DestinationZ": z,
                                     "DirectionY": float(w.get("dir", 0.0))}, "next": f"{k}_in"},
            f"{k}_in": {"kind": "action", "actor": "Fader", "action": "Demo_FadeIn", "params": dict(fade),
                        "next": f"{k}_unlock"},
            f"{k}_unlock": {"kind": "subflow", "flow": "Common", "entry": "EnableSaveAndWarp", "params": {},
                            "next": None},
        })
    return {ACTOR: zelda, DOOR_FLOW: door}


# The flow helpers are mapkit's (docs/mapkit.md); the old names stay for code that uses castle_build's.
check_flow, subflows_of, carried_flows, build_flow = mk.check_flow, mk.subflows_of, mk.carried_flows, mk.build_flow
EVFL_API, TALK_INFO, DOOR_INFO = mk.EVFL_API, mk.TALK_INFO, mk.DOOR_INFO


def evfl_ready(shared: dict[str, tuple[bytes, int]]) -> str | None:
    """mapkit.evfl_ready on the villager's event pack (the shared InitTalk and Common our packs carry)."""
    return mk.evfl_ready(shared, source=VILLAGER)


def event_pack(flow: str, data: bytes, shared: dict[str, tuple[bytes, int]], subflows: list[str],
               yaz0_align: int = 0, *, source: str = VILLAGER, other_files: bool = False) -> tuple[bytes, int]:
    """mapkit.event_pack, from the villager's event pack unless `source` says whose."""
    return mk.event_pack(flow, data, shared, subflows, yaz0_align, source=source, other_files=other_files)


def event_infos(specs: dict[str, dict], villager_talk: dict | None) -> dict[str, dict]:
    """The event info entries ("Flow<Entry>" -> entry): Zelda's talk as the villager's own (TALK_INFO when the
    game's has none), each door warp as the castle's own EventTag events."""
    out = {f"{ACTOR}<Talk>": copy.deepcopy(villager_talk if isinstance(villager_talk, dict) else TALK_INFO)}
    for entry_name in specs[DOOR_FLOW]["entry_points"]:
        out[f"{DOOR_FLOW}<{entry_name}>"] = copy.deepcopy(DOOR_INFO)
    return out


# ---------------------------------------------------------------------------------------------------- the map

def _hid(key: str, used: set[int]) -> bd.U32:
    """A HashId for one of our objects: from its name, so a rebuild gives the same one; never one in use."""
    return mk.hash_id("giruka.castle-date", key, used)


# The map helpers are mapkit's (docs/mapkit.md); the old names stay for code that uses castle_build's.
_srt_hash, _obj, _link, _tag_params = mk.srt_hash, mk.map_object, mk.map_link, mk.tag_params
map_object, map_link, tag_params = mk.map_object, mk.map_link, mk.tag_params


def object_keys(layout: dict, with_events: bool) -> list[str]:
    keys = ["gate", "zelda"] + [d["key"] for d in layout["doors"]]
    if with_events:
        for d in layout["doors"]:
            if d.get("warp"):
                keys += [f"{d['key']}_area", f"{d['key']}_relay", f"{d['key']}_event"]
    return keys


def map_objects(layout: dict, used: set[int], with_events: bool) -> list[dict]:
    """Our objects for the castle's map unit. The gate is a LinkTagAnd on GameClear with no inputs (it only reads
    the flag); its Create links make the doors, Zelda and the door triggers appear once the game is beaten."""
    used = set(used)
    ids = {k: _hid(k, used) for k in object_keys(layout, with_events)}
    objs = []
    created = []
    door_actor = layout.get("door_actor", "TwnObj_Village_Hatenodoor_A_01")
    for d in layout["doors"]:
        objs.append(_obj(d["key"], door_actor, d["pos"], ids, yaw=float(d.get("yaw", 0)),
                         params={"DropTable": "Normal", "SharpWeaponJudgeType": 0}))
        created.append(d["key"])
        if with_events and d.get("warp"):
            t = d["trigger"]
            objs += mk.trigger_chain(d["key"], t["pos"], ids, flow=DOOR_FLOW, entry=d["warp"], half=t["half"],
                                     yaw=float(t.get("yaw", 0)))       # (float: SRTHash hashes the yaw's text)
            created.append(f"{d['key']}_area")
    z = layout["zelda"]
    objs.append(_obj("zelda", ACTOR, z["pos"], ids, yaw=float(z.get("yaw", 0)), params={"TerritoryArea": 0.0},
                     extra={"OnlyOne": True}))
    created.append("zelda")
    objs.append(mk.flag_gate("gate", layout["tags"]["room"], ids, GATE_FLAG, creates=created))
    return sorted(objs, key=lambda o: int(o["HashId"]))


def unit_name(layout: dict, unit: str) -> str:
    return f"{layout.get('unit', 'E-4')}_{'Static' if unit == 'static' else 'Dynamic'}"


# ---------------------------------------------------------------------------------------------------- sizes

def _estimate(rstb: bnp.Rstb | None, ref_name: str, ref_len: int, new_len: int, pack: bool,
              notes: list[str], what: str) -> int | None:
    g = rstb.get(ref_name) if rstb else None
    if g is None:
        notes.append(f"no size entry for {what}: the game's table has none for {ref_name}")
        return None
    return ukmm.estimate_entry(ref_len, g, new_len, pack)


# ---------------------------------------------------------------------------------------------------- building

@dataclass
class Built:
    folder: str
    unit: str
    events: bool
    notes: list[str] = field(default_factory=list)
    objects: list[dict] = field(default_factory=list)
    flows: dict = field(default_factory=dict)


def mod_file(root: str, rel: str) -> str | None:
    """A mod folder's copy of a RomFS file (at its root, under romfs\\ or <title id>\\romfs\\), else None."""
    for pre in ROMFS_PREFIXES:
        p = os.path.join(root, *(pre + rel).split("/"))
        if os.path.isfile(p):
            return p
    return None


def titlebg_mods(lib: mm.Library | None, own_id: str) -> list[str]:
    """The names of the enabled mods (not the castle) that ship their own TitleBG.pack: deploy merges theirs with
    the castle's file by file."""
    if lib is None:
        return []
    return [m["name"] for m in lib.enabled() if m["id"] != own_id
            and mod_file(os.path.join(lib.mods_dir, m["id"]), TITLEBG)]


def map_log_hash_ids(lib: mm.Library | None, mods: list[dict]) -> dict[int, str]:
    """The HashIds these library mods' map changes add (a BCML package's .bnp/logs/map.yml and its options'):
    HashId -> the mod's name. A log that can't be read adds none (deploy names it)."""
    out: dict[int, str] = {}
    for m in (mods if lib is not None else []):
        root = os.path.join(lib.mods_dir, m["id"])
        info = bnp.stored(root)
        if not info:
            continue
        try:
            logs = bnp.package_logs(root, info, {"map.yml": bm})[0]
        except Exception:  # noqa: BLE001 (another mod's damaged logs never stop the castle)
            continue
        for diff in logs.get("map.yml", []):
            for unit in (diff.values() if isinstance(diff, dict) else []):
                for sec in (unit.values() if isinstance(unit, dict) else []):
                    for o in ((sec.get("add") or []) if isinstance(sec, dict) else []):
                        h = o.get("HashId") if isinstance(o, dict) else None
                        if isinstance(h, int) and not isinstance(h, bool):
                            out.setdefault(int(h) & 0xFFFFFFFF, m.get("name") or m["id"])
    return out


def other_hash_ids(lib: mm.Library | None, own_id: str) -> dict[int, str]:
    """map_log_hash_ids of every library mod but the castle (on or off: one turned on later counts too)."""
    return map_log_hash_ids(lib, [m for m in lib.mods if m["id"] != own_id]) if lib is not None else {}


def _write(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(data, (bytes, bytearray)) else "w"
    with open(path, mode, **({} if mode == "wb" else {"encoding": "utf-8", "newline": "\n"})) as f:
        f.write(data)


def build_package(game: Game, recipe: Recipe, out: str, *, unit: str = "static", cloth: bool = False,
                  events: bool = True, lib: mm.Library | None = None, log=None) -> Built:
    """Write the castle mod (a BCML-style package folder: info.json, mod.toml, logs/, the new packs) into `out`
    (a new or empty folder). Reads only the player's game files and library."""
    if unit not in ("static", "dynamic"):
        raise CastleError("--unit is static or dynamic")
    if os.path.isdir(out) and os.listdir(out):
        raise CastleError(f"{out} isn't empty")
    layout, texts = recipe.layout, recipe.texts
    romfs = os.path.join(out, BASE_TID, "romfs")
    res = Built(out, unit_name(layout, unit), False)
    rstb_json: dict[str, int] = {}
    rstb = game.rstb()
    if rstb is None:
        res.notes.append("the game's size table isn't there: no size entries for the new files")

    # Zelda
    _log(log, f"Building {ACTOR} from your {ZELDA_SRC} ({'cloth on' if cloth else 'light physics, no cloth'})...")
    zraw = game.read(FILES["zelda"])
    zp = zelda_pack(game.pack("zelda"), game.pack("villager"), cloth=cloth,
                    yaz0_align=struct.unpack_from(">I", zraw, 8)[0] if zraw[:4] == b"Yaz0" else 0)
    _write(os.path.join(romfs, "Actor", "Pack", f"{ACTOR}.sbactorpack"), zp.data)
    z = game.pack("zelda")
    sizes = [(f"Actor/Pack/{ACTOR}.bactorpack", f"Actor/Pack/{ZELDA_SRC}.bactorpack", game.pack_raw_len("zelda"),
              zp.raw_len, True)]
    sizes += [(n, zp.refs[n], len(z[zp.refs[n]][0]), len(d), False) for n, d in zp.inner.items()]
    for name, ref, ref_len, new_len, pack in sizes:
        size = _estimate(rstb, ref, ref_len, new_len, pack, res.notes, name)
        if size:
            rstb_json[name] = size

    # actor info
    src = game.actorinfo().get(ZELDA_SRC)
    if src is None:
        raise CastleError(f"Your game's actor info has no {ZELDA_SRC}")
    entry = copy.deepcopy(src)
    entry["name"] = ACTOR
    logs = {"actorinfo.yml": bd.to_text({str(zlib.crc32(ACTOR.encode())): entry})}

    # event flows (when tools/evfl.py can write them)
    specs = flow_specs(layout, texts)
    bad = [x for s in specs.values() for x in check_flow(s)]
    if bad:
        raise CastleError("The castle's event flows are wrong: " + "; ".join(bad))
    res.flows = specs
    flows: dict[str, bytes] = {}
    shared = game.pack("villager_events")
    why = "turned off (--no-events)" if not events else evfl_ready(shared)
    if why:
        res.notes.append(f"event flows not built ({why}): the doors and Zelda are placed, but the door warps and "
                         "her talk event aren't (no trigger, no event info)")
    else:
        _log(log, "Writing the event flows (tools/evfl.py)...")
        flows = {name: build_flow(spec) for name, spec in specs.items()}
    if flows:
        res.events = True
        infos = event_infos(specs, game.eventinfo().get(f"{VILLAGER}<Talk>"))
        carry = carried_flows(specs, infos)
        epraw = game.read(FILES["villager_events"])
        align = struct.unpack_from(">I", epraw, 8)[0] if epraw[:4] == b"Yaz0" else 0
        ref_flow = f"EventFlow/{VILLAGER}.bfevfl"
        for name, data in flows.items():
            pdata, plen = event_pack(name, data, shared, carry[name], align)
            _write(os.path.join(romfs, "Event", f"{name}.sbeventpack"), pdata)
            for ename, ref, rl, nl, pk in [(f"Event/{name}.beventpack", f"Event/{VILLAGER}.beventpack",
                                            ukmm.unpacked_size(epraw), plen, True),
                                           (f"EventFlow/{name}.bfevfl", ref_flow, len(shared[ref_flow][0])
                                            if ref_flow in shared else 0, len(data), False)]:
                size = _estimate(rstb, ref, rl, nl, pk, res.notes, ename)
                if size:
                    rstb_json[ename] = size
        logs["eventinfo.yml"] = bd.to_text(infos)

    # the map: the gate, doors, Zelda, triggers
    _log(log, f"Placing the doors and Zelda in {res.unit}...")
    used = game.hash_ids(log) | set(other_hash_ids(lib, recipe.manifest["id"]))   # (the game's and other mods')
    res.objects = map_objects(layout, used, res.events)
    logs["map.yml"] = bd.to_text({res.unit: {"Objs": {"add": res.objects}}})
    if unit == "static":
        others = titlebg_mods(lib, recipe.manifest["id"])
        if others:
            res.notes.append(f"{', '.join(others)} also ship{'s' if len(others) == 1 else ''} {TITLEBG}: modman "
                             "deploy merges it with the castle's file by file (any load order)")

    # texts and sizes
    logs["texts.json"] = json.dumps(texts_log(texts), indent=1, ensure_ascii=False) + "\n"
    logs["rstb.json"] = json.dumps(dict(sorted(rstb_json.items())), indent=1) + "\n"
    check_logs(logs)
    for name, text in logs.items():
        _write(os.path.join(out, "logs", name), text)
    _write(os.path.join(out, "mod.toml"), recipe.toml_text)
    _write(os.path.join(out, "info.json"), json.dumps({
        "name": recipe.manifest.get("name", "Castle date"), "version": recipe.manifest["version"],
        "desc": recipe.manifest.get("description", ""), "platform": "switch", "options": {}}, indent=1) + "\n")
    _write(os.path.join(out, OUT_FILE), "\n".join([
        "# Castle date: built on this PC by tools/castle_build.py from the player's own game files.",
        "", f"- map unit: {res.unit}", f"- {ACTOR}: {'cloth on' if cloth else 'light physics, no cloth'}",
        f"- event flows: {'built' if res.events else 'not built'}",
        f"- layout measured in game: {'yes' if layout.get('measured') else 'no (guessed)'}", ""]))
    if not layout.get("measured"):
        res.notes.append("the door, trigger and Zelda spots are guessed from the rooms' bounds (castle/layout.json): "
                         "walk them in the smoke run")
    return res


def check_logs(logs: dict[str, str]) -> None:
    """Read our logs back with the mergers modman uses: one they'd refuse or cut is refused here, before it ships."""
    for name, text in logs.items():
        data = text.encode("utf-8")
        try:
            if name == "texts.json":
                got = bt.read_log(name, data, "logs/")
                if got.notes:
                    raise CastleError("; ".join(got.notes))
            elif name in bm.LOGS:
                bm.read_log(name, data)
            elif name in bd.LOGS:
                bd.read_log(name, data)
            elif name == "rstb.json":
                bnp.read_rstb_json(data, f"logs/{name}")
        except (bt.LogError, bm.LogError, bd.LogError, bnp.BnpError) as e:
            raise CastleError(f"the castle's {name} would be refused by modman ({e})") from None


def add_to_library(lib: mm.Library, folder: str, mod_id: str, log=None) -> tuple[dict, list[str]]:
    """The built folder into the library through modman's normal add. A rebuild removes the castle's old copy
    first and keeps its on/off and its place in the load order; a first build goes last, as any add does."""
    idx = next((i for i, m in enumerate(lib.mods) if m["id"] == mod_id), None)
    was_on = lib.mods[idx]["enabled"] if idx is not None else True
    if idx is not None:
        mm.remove_mod(lib, mod_id)
    elif os.path.isdir(os.path.join(lib.mods_dir, mod_id)):
        raise CastleError(f"{os.path.join(lib.mods_dir, mod_id)} is there but the library doesn't list it: move "
                          "that folder away and build again.")
    _log(log, "Adding it to your mod library (modman add: the logs are merged into your game's files)...")
    try:
        entry, _scan, notes = mm.add_mod(lib, folder)
    except mm.ModmanError as e:
        raise CastleError(str(e)) from None
    i = next(i for i, m in enumerate(lib.mods) if m["id"] == entry["id"])
    m = lib.mods.pop(i)
    m["enabled"] = was_on
    lib.mods.insert(len(lib.mods) if idx is None else min(idx, len(lib.mods)), m)
    lib.save()
    return m, notes


def build(library: str | None = None, *, install: str | None = None, unit: str = "static", cloth: bool = False,
          events: bool = True, out: str | None = None, add: bool = True, recipe_dir: str = RECIPE,
          log=None) -> dict:
    """Build the castle mod and (add=True) put it in the library. Returns {"built": Built, "entry", "notes"}."""
    recipe = load_recipe(recipe_dir)
    root = mm.resolve_library(library)
    lib = mm.Library.load(root)
    if add and lib is None:
        raise CastleError(f"No mod library in {root} yet. Run: python tools/modman.py init")
    game = find_game(install, lib)
    tmp = None
    if out is None:
        tmp = tempfile.mkdtemp(prefix="castle-build-")
        out = os.path.join(tmp, recipe.manifest["id"])
    out = os.path.abspath(out)
    try:
        built = build_package(game, recipe, out, unit=unit, cloth=cloth, events=events, lib=lib, log=log)
        entry, notes = None, []
        if add:
            entry, notes = add_to_library(lib, out, recipe.manifest["id"], log)
        return {"built": built, "entry": entry, "notes": notes}
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------------------------------- check

def check(library: str | None = None, *, install: str | None = None, recipe_dir: str = RECIPE,
          log=None) -> list[str]:
    """Offline checks on the player's files (reads only): every name the castle uses exists, the gate flag is a
    game flag, no placed object is a pick-up, chest or enemy, the castle in the library (if any) uses no HashId the
    game's map or another library mod's map changes use, and the texts the flows name are there. Returns the
    problems (empty: all good)."""
    recipe = load_recipe(recipe_dir)
    lib = mm.Library.load(mm.resolve_library(library))
    game = find_game(install, lib)
    out: list[str] = []
    ai = game.actorinfo()
    for name in (ZELDA_SRC, VILLAGER, recipe.layout.get("door_actor", "TwnObj_Village_Hatenodoor_A_01"),
                 "LinkTagAnd", "EventTag", "Area"):
        if name not in ai:
            out.append(f"actor {name} isn't in your game's actor info")
    flags = game.bool_flags()
    if GATE_FLAG not in flags:
        out.append(f"the flag {GATE_FLAG} isn't in your game's game data")
    used = game.hash_ids(log)
    others = other_hash_ids(lib, recipe.manifest["id"])
    objs = map_objects(recipe.layout, used | set(others), True)
    for o in objs:
        actor = o["UnitConfigName"]
        prof = (ai.get(actor) or ai.get(ZELDA_SRC if actor == ACTOR else actor) or {}).get("profile")
        if prof in BAD_PROFILES or actor.startswith(("TBox_", "Item_", "Enemy_", "Weapon_", "Armor_")):
            out.append(f"{actor} is a pick-up, chest or enemy ({prof}): it can't be placed")
        if actor.startswith("LinkTag"):
            if o["!Parameters"].get("MakeSaveFlag") != 0 or o["!Parameters"].get("IncrementSave"):
                out.append(f"a {actor} of ours would save its own flag (MakeSaveFlag/IncrementSave)")
        elif actor not in ("EventTag", "Area") and any(f.startswith(f"MainField_{actor}_") for f in flags):
            out.append(f"the game saves placed {actor}s one by one (MainField_{actor}_* flags): it can't be placed")
    if len({int(o["HashId"]) for o in objs}) != len(objs):
        out.append("two of the castle's objects share a HashId")
    # the castle already in the library: its objects' HashIds against the game's map and the other mods' changes
    own = [m for m in (lib.mods if lib is not None else []) if m["id"] == recipe.manifest["id"]]
    for h in sorted(map_log_hash_ids(lib, own)):
        if h in used:
            out.append(f"HashId {h} of the castle in your library is in your game's map too: build it again")
        elif h in others:
            out.append(f"HashId {h} of the castle in your library is added by {others[h]} too: build it again")
    gate = next(o for o in objs if o["!Parameters"].get("SaveFlag") == GATE_FLAG)
    if any(int(gate["HashId"]) == int(lk["DestUnitHashId"]) for o in objs for lk in o.get("LinksToObj") or []):
        out.append("the GameClear gate has an input: it would write the flag")
    msgs, _ = talk_messages(recipe.texts)
    specs = flow_specs(recipe.layout, recipe.texts)
    for s in specs.values():
        out += check_flow(s)
        for e in s["events"].values():
            mid = (e.get("params") or {}).get("MessageId")
            if mid and mid.split(":", 1)[1] not in msgs:
                out.append(f"{s['name']} says {mid}, which castle/texts.json doesn't have")
    asl = game.pack("zelda").get(f"Actor/ASList/{ZELDA_SRC}.baslist")
    if asl:
        names = set()
        root = aamp.load(asl[0])
        defs = root.list("ASDefines")
        for obj in (defs.objects.values() if defs else []):
            v = obj.get_value("Name")
            if isinstance(v, str):
                names.add(v)
        for s in specs.values():
            for e in s["events"].values():
                a = (e.get("params") or {}).get("ASName")
                if a and a not in names:
                    out.append(f"{ZELDA_SRC} has no animation {a!r} in her list")
    try:
        files = bnp.Blob(game.read(MESSAGES)).child("Message/Msg_USen.product.ssarc").open()
        if "ActorType/NPC.msbt" not in files:
            out.append("your English texts have no ActorType/NPC.msbt for Zelda's name")
        if f"EventFlowMsg/{ACTOR}.msbt" in files:
            out.append(f"your English texts already have EventFlowMsg/{ACTOR}.msbt")
    except (CastleError, AttributeError, wc.FormatError) as e:
        out.append(f"your English texts can't be read ({e})")
    ev = game.eventinfo()
    for key in (f"{ACTOR}<Talk>", *(f"{DOOR_FLOW}<{k}>" for k in specs[DOOR_FLOW]["entry_points"])):
        if key in ev:
            out.append(f"your game's event info already has {key}")
    return out


# ---------------------------------------------------------------------------------------------------- command line

def _low_priority() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)   # below normal: keeps the PC (and a game) smooth
        except (OSError, AttributeError):
            pass


def main(argv: list[str] | None = None) -> int:
    with contextlib.suppress(AttributeError, ValueError, OSError):
        sys.stdout.reconfigure(errors="replace")
    lib_help = "the mod library (default: SWMOD_LIBRARY, else modman's)"
    game_help = "the Savage Wind folder (default: the library's)"
    common = argparse.ArgumentParser(add_help=False)   # (before or after the command: the launcher puts --library last)
    common.add_argument("--library", help=lib_help, default=argparse.SUPPRESS)
    common.add_argument("--game", help=game_help, default=argparse.SUPPRESS)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--library", help=lib_help)
    ap.add_argument("--game", help=game_help)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", parents=[common], help="build the castle mod and add it to the library")
    b.add_argument("--unit", choices=("static", "dynamic"), default="static",
                   help="where the doors go: E-4_Static in TitleBG.pack (default) or the loose E-4_Dynamic")
    b.add_argument("--cloth", action="store_true", help="keep Zelda's cloth (costs 3-8 fps)")
    b.add_argument("--no-events", action="store_true",
                   help="leave out the event flows (door warps, Zelda's talk): doors and Zelda only")
    b.add_argument("--no-add", action="store_true", help="only write the mod folder (--out)")
    b.add_argument("--out", help="where to write the mod folder (needed with --no-add; a new or empty folder)")
    sub.add_parser("check", parents=[common], help="offline checks on your files (reads only)")
    sub.add_parser("where", parents=[common], help="the game files the castle is built from")
    args = ap.parse_args(argv)
    _low_priority()
    try:
        if args.cmd == "where":
            lib = mm.Library.load(mm.resolve_library(args.library))
            game = Game(os.path.abspath(args.game or (lib.install if lib else None) or mm.default_install()))
            for k, rel in FILES.items():
                print(f"{'ok  ' if os.path.isfile(game.path(rel)) else 'MISSING'} {rel}")
            return 0 if not game.missing() else 1
        if args.cmd == "check":
            problems = check(args.library, install=args.game, log=print)
            print("Castle check: all good." if not problems else f"Castle check: {len(problems)} problem(s):")
            for p in problems:
                print(f"  - {p}")
            return 1 if problems else 0
        if args.no_add and not args.out:
            raise CastleError("--no-add needs --out <folder>")
        r = build(args.library, install=args.game, unit=args.unit, cloth=args.cloth, events=not args.no_events,
                  out=args.out, add=not args.no_add, log=print)
        made = r["built"]
        where = f"in your library as {r['entry']['name']}" if r["entry"] else made.folder
        print(f"Castle date built ({made.unit}, {len(made.objects)} map objects, event flows "
              f"{'built' if made.events else 'not built'}): {where}.")
        for n in made.notes:
            print(f"  note: {n}")
        for n in r["notes"]:
            print(f"  {n}")
        if r["entry"]:
            print("Next: python tools/modman.py deploy")
        return 0
    except (CastleError, mm.ModmanError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
