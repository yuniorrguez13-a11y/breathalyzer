#!/usr/bin/env python3
"""Dragonbreath stage 2 (docs/dragonbreath.md, "Stage 2"): Rohan's tip, the diamond cave, the chest, Daruk's
first-draw scene, the tempers and the re-buy. A build step of tools/dragonbreath_build.py (`build` runs it, `check`
runs this check): it writes into the two package folders stage 1 made (the main switch and the item mod), from our
recipe (assets/dragonbreath/stage2/stage2.json) and the player's own game files (only read). Nothing from the game is
in this repo.

    python tools/dragonbreath_stage2.py check [--library DIR] [--game DIR]   offline checks on your files (reads only)
    python tools/dragonbreath_stage2.py plan                                  what it would make (no game files)

What it adds (BCML-style logs and files that `modman add` merges the normal way):

main switch (botw-mod-loader.dragonbreath):
  logs/map.yml        the cave (the game's Gorko Tunnel rock and its far model, G-1_Static), inside it (G-1_Dynamic)
                      Daruk's seal, rare ore deposits on the "Happy" drop table, plain and glowing deposits, two loose
                      diamonds, Goron lamps, Igneo Pebblits, Fire Keese, Fire Chuchus, two Fire-Breath Lizalfos, a
                      Meteo Wizzrobe and the stone chest that holds the blade; Daruk's spirit (made while the flag
                      Dragonbreath_DarukOn is on); the first-draw trigger (Area -> LinkTagAnd -> EventTag, made once
                      the blade is got, deleted once the scene has played)
  logs/quests.yml     the side quest "Forged for Daruk" (Dragonbreath): Ready -> Get -> Finish, Rohan's talk taken
                      over in each step (the game's own pattern), the cave marker
  logs/eventinfo.yml, Event/Dragonbreath.sbeventpack   our flow (Rohan's three step talks, the first draw) with the
                      game's InitTalk, Common and Rohan's own flow (his Boulder Breaker re-forge stays reachable)
  logs/texts.json     EventFlowMsg/Dragonbreath (Rohan's and Daruk's lines), QuestMsg/QL_Dragonbreath (the log)
item mod (giruka.dragonbreath-item, stays on so saves load):
  logs/gamedata.yml, savedata.yml   every flag a save keeps: the quest's, the chest's, the loose diamonds',
                      IsGet for the 4 tempered levels
  logs/texts.json     the names of the 4 tempered levels (Dragonbreath +1..+4)

The 4 tempered levels' actor packs, actor info and icons are stage 1's builder's job (level_recipes() says what
each one is; dragonbreath_build.build_package makes them with the base blade's model). Flows are described as mapkit's flow specs and
written by tools/evfl.py through mapkit.build_flow (docs/mapkit.md).
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import dataclasses
import json
import math
import os
import re
import struct
import sys
import textwrap
from dataclasses import dataclass, field

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import ak47_build as ak  # noqa: E402
import bcml_data as bd  # noqa: E402
import bcml_maps as bm  # noqa: E402
import bnp  # noqa: E402
import castle_build as cb  # noqa: E402  (check_logs, VILLAGER)
import mapkit as mk  # noqa: E402  (the map, event and flow helpers: docs/mapkit.md)
import ukmm  # noqa: E402

REPO = os.path.dirname(TOOLS)
STAGE2 = os.path.join(REPO, "assets", "dragonbreath", "stage2")
RECIPE_JSON = "stage2.json"
FLOW = "Dragonbreath"                    # our event flow and message file
DARUK = "Dm_Npc_Goron_Hero"
TEXT_WIDTH = 40                          # characters per line in the talk box
TEXT_LINES = 3                           # lines per talk box
QUEST_WIDTH = 44                         # characters per line in the quest log (the game's own texts are broken by
#                                          hand at about 44)
WEAPON_MSG = "ActorType/WeaponSmallSword"
QUEST_FLAGS = ("Activated", "Get", "Finish", "SmithSaw", "FirstDraw", "DarukOn")
STEPS = ("Ready", "Get", "Finish")       # quest steps; each takes over Rohan's talk
ITEM_RE = re.compile(r"(Item|Weapon)_[A-Za-z0-9_]{1,40}")
CHEST_FLAG_LIKE = "MainField_TBox_Field_Stone_"
FADE = {"IsWaitFinish": True, "Frame": 0, "Color": 1, "DispMode": "NoLogo"}   # Rohan's own forging fade
TALK_INFO = mk.TALK_INFO     # Rohan's talk when the game's event info has none (InitTalk + Common)
PLACED_PARAMS = {"AddParam": 0, "AddSpecialFlag": 0, "DropTable": "Normal", "EnemyOwnItem": False,
                 "IsCreateSharpWeapon": False, "IsEmitLandNoise": False, "IsFixedPlace": False, "IsPlayerPut": False,
                 "IsWeaponCreateByRawLife": False, "LevelSensorMode": 0, "Life": -1, "SharpWeaponJudgeType": 0}
DEMO_INFO = mk.DOOR_INFO     # the first-draw scene: a map EventTag's event (Common only)


class Stage2Error(Exception):
    """Something the player (or the recipe) has to fix; the message says what."""


def _log(log, msg: str) -> None:
    if log:
        log(msg)


# ------------------------------------------------------------------------------------------------ the recipe

@dataclass
class Stage2:
    raw: dict
    base_actor: str
    smith: dict
    quest: dict
    rebuy: dict
    levels: list
    ore_names: dict
    cave: dict
    texts: dict
    folder: str

    @property
    def smith_actor(self) -> str:
        return self.smith["actor"]

    def level_actor(self, i: int) -> str:
        return self.base_actor + self.levels[i]["suffix"]

    def level_actors(self) -> list[str]:
        return [self.level_actor(i) for i in range(len(self.levels))]


def _bad(msg: str) -> Stage2Error:
    return Stage2Error(f"assets/dragonbreath/stage2/{RECIPE_JSON}: {msg}")


def _num(v, what: str, lo: float, hi: float) -> float:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or not lo <= v <= hi:
        raise _bad(f"{what} must be a number from {lo:g} to {hi:g}")
    return float(v)


def _name(v, what: str) -> str:
    if not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,48}", v):
        raise _bad(f"{what} isn't an actor name")
    return v


def _cost(c, what: str) -> dict[str, int]:
    if not isinstance(c, dict) or not c:
        raise _bad(f"{what} isn't a list of items and counts")
    out = {}
    for k, v in c.items():
        if not ITEM_RE.fullmatch(k):
            raise _bad(f"{what}: {k} isn't an item name")
        out[k] = int(_num(v, f"{what} {k}", 1, 99))
    return out


def wrap(text: str, width: int = TEXT_WIDTH) -> str:
    """Our lines with the line breaks the talk box (or the quest log) needs: neither wraps by itself."""
    return "\n".join(textwrap.fill(p, width, break_long_words=False) for p in text.split("\n"))


def load_stage2(folder: str = STAGE2) -> Stage2:
    try:
        with open(os.path.join(folder, RECIPE_JSON), encoding="utf-8") as f:
            r = json.load(f)
    except (OSError, ValueError) as e:
        raise Stage2Error(f"The Dragonbreath stage 2 recipe in {folder} can't be read ({e}).") from None
    if not isinstance(r, dict) or r.get("format") != 1:
        raise _bad("isn't format 1")
    base = _name(r.get("base_actor"), "base_actor")
    smith = dict(r.get("smith") or {})
    _name(smith.get("actor"), "smith actor")
    smith["pos"] = [_num(v, "smith pos", -10000, 10000) for v in smith.get("pos") or []]
    if len(smith["pos"]) != 3:
        raise _bad("smith pos needs x, y, z")
    tl = smith.get("timeline_from")
    if not (isinstance(tl, list) and len(tl) == 2 and all(isinstance(x, str) and x for x in tl)):
        raise _bad("smith timeline_from needs [quest name, NPC name]")
    quest = dict(r.get("quest") or {})
    for k in ("name", "location", "prev_quest"):
        if not isinstance(quest.get(k), str) or not quest[k]:
            raise _bad(f"quest {k} is missing")
    quest["marker"] = [_num(v, "quest marker", -10000, 10000) for v in quest.get("marker") or []]
    if len(quest["marker"]) != 2:
        raise _bad("quest marker needs x, z")
    rebuy = _cost(r.get("rebuy"), "rebuy")
    levels = r.get("levels")
    if not isinstance(levels, list) or not 2 <= len(levels) <= 9:
        raise _bad("levels needs the base blade and 1 to 8 tempered levels")
    lv = []
    for i, x in enumerate(levels):
        if not isinstance(x, dict):
            raise _bad(f"level {i} isn't an object")
        suf = x.get("suffix")
        if suf != ("" if i == 0 else f"_{i}"):
            raise _bad(f"level {i}'s suffix must be {'empty' if i == 0 else f'_{i}'}")
        e = {"suffix": suf, "attack": int(_num(x.get("attack"), f"level {i} attack", 1, 999)),
             "guardian_ratio": _num(x.get("guardian_ratio"), f"level {i} guardian_ratio", 0.0, 10.0)}
        if not isinstance(x.get("name"), str) or not x["name"].strip():
            raise _bad(f"level {i} has no name")
        e["name"] = x["name"]
        if i:
            e["cost"] = _cost(x.get("cost"), f"level {i} cost")
            if e["attack"] < lv[-1]["attack"]:
                raise _bad(f"level {i} is weaker than level {i - 1}")
        lv.append(e)
    ores = r.get("ore_names") or {}
    used_items = set(rebuy) | {k for x in lv[1:] for k in x["cost"]}
    missing = sorted(used_items - set(ores))
    if missing:
        raise _bad(f"ore_names has no name for {', '.join(missing)}")
    cave = dict(r.get("cave") or {})
    for k in ("unit", "static_unit"):
        if not isinstance(cave.get(k), str) or not bm.UNIT_RE.fullmatch(cave[k]):
            raise _bad(f"cave {k} isn't a map unit like G-1_Dynamic")
    cave["origin"] = [_num(v, "cave origin", -10000, 10000) for v in cave.get("origin") or []]
    if len(cave["origin"]) != 3:
        raise _bad("cave origin needs x, y, z")
    cave["yaw"] = _num(cave.get("yaw"), "cave yaw", -360, 360)
    want = unit_of(cave["origin"][0], cave["origin"][2])
    for k in ("unit", "static_unit"):
        if cave[k].rsplit("_", 1)[0] != want:
            raise _bad(f"the cave is in map section {want}, not {cave[k]}")
    keys = set()
    objs = cave.get("objects")
    if not isinstance(objs, list) or not objs:
        raise _bad("cave objects is empty")
    for o in objs + [cave.get("chest"), cave.get("daruk"), cave.get("first_draw_area")]:
        if not isinstance(o, dict) or not isinstance(o.get("at"), list) or len(o["at"]) != 2:
            raise _bad("every cave object needs at: [along, across]")
        o["at"] = [_num(v, "cave object at", -200, 200) for v in o["at"]]
        if "actor" in o:
            _name(o["actor"], "cave object actor")
        if "key" in o:
            if o["key"] in keys or not re.fullmatch(r"[a-z0-9_]{1,24}", o["key"]):
                raise _bad(f"cave object key {o['key']!r} is used twice or isn't a short lowercase name")
            keys.add(o["key"])
    texts = r.get("texts") or {}
    for k in ("quest_name", "quest_desc", "quest_get", "quest_finish", "level_desc"):
        if not isinstance(texts.get(k), str) or not texts[k].strip():
            raise _bad(f"texts {k} is missing")
    need = ("tip1", "tip2", "tip3", "remind", "menu", "saw1", "saw2", "saw3", "offer", "short", "no", "done", "maxed",
            "away", "lost", "reforged")
    ro = texts.get("rohan") or {}
    gone = [k for k in need if not isinstance(ro.get(k), str) or not ro[k].strip()]
    if gone:
        raise _bad(f"texts rohan lacks {', '.join(gone)}")
    ch = texts.get("choices") or {}
    for k in ("menu", "yes_no"):
        if not isinstance(ch.get(k), list) or len(ch[k]) != 2 or not all(isinstance(x, str) and x for x in ch[k]):
            raise _bad(f"texts choices {k} needs 2 choices")
    if not isinstance(texts.get("daruk"), list) or not texts["daruk"] or \
            not all(isinstance(x, str) and x.strip() for x in texts["daruk"]):
        raise _bad("texts daruk needs Daruk's lines")
    return Stage2(raw=r, base_actor=base, smith=smith, quest=quest, rebuy=rebuy, levels=lv, ore_names=ores,
                  cave=cave, texts=texts, folder=folder)


unit_of = ak._unit_of          # the main field's map section ("G-1") of a point


# ------------------------------------------------------------------------------------------------ the levels

def level_recipes(st: Stage2, recipe):
    """Stage 1's Recipe for each tempered level (dataclasses.replace: actor, attack, Guardian ratio, name; every
    other number and tag as the base blade's). For stage 1's builder (the handoff says how)."""
    out = []
    for i in range(1, len(st.levels)):
        lv = st.levels[i]
        stats = dict(recipe.stats, attack=lv["attack"], guardian_ratio=lv["guardian_ratio"])
        texts = dict(recipe.texts, name=lv["name"], desc=st.texts["level_desc"])
        out.append(dataclasses.replace(recipe, actor=st.level_actor(i), stats=stats, texts=texts))
    return out


def cost_text(st: Stage2, cost: dict[str, int]) -> str:
    parts = [f"{n} {st.ore_names[k]}" for k, n in cost.items()]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


# ------------------------------------------------------------------------------------------------ texts

def _msg(text: str, speaker: str, choice: list[int] | None = None) -> dict:
    contents: list = [{"text": wrap(text)}]
    if choice:
        n = len(choice)
        contents.append({"control": {"kind": "choice", "choice_labels": list(choice), "selected_index": 0,
                                     "cancel_index": n - 1, "unknown": 2 * n + 2}})
    return {"contents": contents, "attributes": speaker}


MENU_CHOICES = [0, 1]
YES_NO = [2, 3]


def messages(st: Stage2) -> dict[str, dict]:
    """EventFlowMsg/Dragonbreath: label -> msyt entry."""
    ro, sm = st.texts["rohan"], st.smith_actor
    out = {k: _msg(ro[k], sm) for k in ("tip1", "tip2", "tip3", "remind", "saw1", "saw2", "saw3", "short", "no",
                                        "done", "maxed", "away", "reforged")}
    out["menu"] = _msg(ro["menu"], sm, MENU_CHOICES)
    for i in range(1, len(st.levels)):
        out[f"offer{i}"] = _msg(ro["offer"].format(level=st.levels[i]["name"],
                                                   cost=cost_text(st, st.levels[i]["cost"])), sm, YES_NO)
    out["lost"] = _msg(ro["lost"].format(cost=cost_text(st, st.rebuy)), sm, YES_NO)
    for n, c in zip(MENU_CHOICES + YES_NO, st.texts["choices"]["menu"] + st.texts["choices"]["yes_no"]):
        out[f"{n:04d}"] = {"contents": [{"text": c}], "attributes": ""}
    for i, line in enumerate(st.texts["daruk"], 1):
        out[f"daruk{i}"] = _msg(line, DARUK)
    return out


def text_problems(st: Stage2) -> list[str]:
    out = []
    for label, m in messages(st).items():
        t = m["contents"][0]["text"]
        if label.isdigit():
            if len(t) > 20:
                out.append(f"the choice {t!r} is longer than 20 characters")
            continue
        if t.count("\n") + 1 > TEXT_LINES:
            out.append(f"the line {label} needs {t.count(chr(10)) + 1} lines (at most {TEXT_LINES} fit a talk box)")
    return out


def texts_logs(st: Stage2) -> tuple[dict, dict]:
    """(main switch's texts.json, item mod's texts.json): English; the other languages get them too."""
    q = f"QL_{st.quest['name']}"
    t = st.texts
    main = {"USen": {
        f"EventFlowMsg/{FLOW}.msyt": messages(st),
        f"QuestMsg/{q}.msyt": {f"{q}_Name": {"contents": [{"text": t["quest_name"]}]},
                               f"{q}_Desc": {"contents": [{"text": wrap(t["quest_desc"], QUEST_WIDTH)}]},
                               f"{q}_Get": {"contents": [{"text": wrap(t["quest_get"], QUEST_WIDTH)}]},
                               f"{q}_Finish": {"contents": [{"text": wrap(t["quest_finish"], QUEST_WIDTH)}]}}}}
    item: dict = {}
    for i in range(1, len(st.levels)):
        a = st.level_actor(i)
        item[f"{a}_Name"] = {"contents": [{"text": st.levels[i]["name"]}]}
        item[f"{a}_Desc"] = {"contents": [{"text": t["level_desc"]}]}
        item[f"{a}_PictureBook"] = {"contents": [{"text": t["level_desc"]}]}
    return main, {"USen": {f"{WEAPON_MSG}.msyt": item}}


# ------------------------------------------------------------------------------------------------ event flows

def flag(name: str, st: Stage2 | None = None) -> str:
    return f"{st.quest['name'] if st else FLOW}_{name}"


class _Spec(mk.FlowSpec):
    """mapkit's FlowSpec (a mapkit flow spec with short keys), raising Stage2Error for a key used twice."""

    def ev(self, key: str, e: dict) -> str:
        try:
            return super().ev(key, e)
        except mk.MapError as err:
            raise Stage2Error(str(err)) from None


def _talk(st: Stage2, actor: str, label: str, close: bool) -> dict:
    return mk.talk_params(FLOW, label, close)


def _flag_on(name: str) -> dict:
    return {"IsWaitFinish": True, "FlagName": name}


def _has(item: str, count: int) -> dict:
    return {"PorchItemName": item, "Count": count}


def _forge(f: _Spec, st: Stage2, pre: str, take_blade: str | None, cost: dict[str, int], nxt: str) -> str:
    """Rohan's own forging steps (his Boulder Breaker re-forge, in the same order): music off, black fade, the
    blackout sound, the old blade taken, the ores taken, time passes, fade back, music on. Returns the first key."""
    keys = [f"{pre}_bgm_off", f"{pre}_out", f"{pre}_sound"] + ([f"{pre}_take"] if take_blade else []) + \
        [f"{pre}_ore{i}" for i in range(len(cost))] + [f"{pre}_time", f"{pre}_in", f"{pre}_bgm_on"]
    it = iter(keys[1:] + [nxt])
    f.act(keys[0], "SceneSoundCtrlTag", "Demo_Ctrl", {"BgmCtrlType": "StopWithFade", "SeCtrlType": "None",
                                                      "IsWaitFinish": True}, next(it))
    f.act(f"{pre}_out", "Fader", "Demo_FadeOut", dict(FADE), next(it))
    f.act(f"{pre}_sound", "SoundTriggerTag", "Demo_SoundTrigger",
          {"SoundDelay": 0, "SLinkInst": "", "IsWaitFinish": True, "Sound": "Demo_Cmn_Blackout"}, next(it))
    if take_blade:
        f.act(f"{pre}_take", "EventSystemActor", "Demo_DeletePorchItemInEquip",
              {"IsWaitFinish": True, "DeleteNum": 1, "PorchItemName": take_blade}, next(it))
    for i, (item, n) in enumerate(cost.items()):
        f.act(f"{pre}_ore{i}", "EventSystemActor", "Demo_IncreasePorchItem",
              {"IsWaitFinish": True, "Value": -n, "PorchItemName": item}, next(it))
    f.act(f"{pre}_time", "EventSystemActor", "Demo_AdvanceTime",
          {"IsWaitFinish": True, "DestTime": 0, "DirectTime": -1, "PassTime": -1, "ActReset": True}, next(it))
    f.act(f"{pre}_in", "Fader", "Demo_FadeIn", dict(FADE), next(it))
    f.act(f"{pre}_bgm_on", "SceneSoundCtrlTag", "Demo_Ctrl", {"SeCtrlType": "None", "BgmCtrlType": "Start",
                                                              "IsWaitFinish": True}, next(it))
    return keys[0]


def _give(f: _Spec, key: str, actor: str, nxt: str) -> str:
    """The game's own item-get scene (GetDemo<GetItemByName>, as Rohan hands over the Boulder Breaker)."""
    return f.sub(key, "GetDemo", "GetItemByName", {"IsInvalidOpenPouch": False, "CheckTargetActorName": actor}, nxt)


def _cost_checks(f: _Spec, pre: str, cost: dict[str, int], ok: str, short: str) -> str:
    keys = [f"{pre}_has{i}" for i in range(len(cost))]
    for i, (item, n) in enumerate(cost.items()):
        f.sw(keys[i], "EventSystemActor", "HasPorchItem", _has(item, n),
             {1: keys[i + 1] if i + 1 < len(keys) else ok, 0: short})
    return keys[0]


def _smith_menu(f: _Spec, st: Stage2, pre: str) -> str:
    """The smith's talk once the blade is found: the first look at it, then a temper for the level Link holds, or
    the re-buy when no level is anywhere (pouch, or lying in the world). Returns the first key."""
    sm, q = st.smith_actor, st.quest["name"]
    acts = st.level_actors()
    top = len(acts) - 1
    end_short = f.act(f"{pre}_short", sm, "Demo_Talk", _talk(st, sm, "short", True), None)
    end_no = f.act(f"{pre}_no", sm, "Demo_Talk", _talk(st, sm, "no", True), None)
    f.act(f"{pre}_maxed", sm, "Demo_Talk", _talk(st, sm, "maxed", True), None)
    f.act(f"{pre}_away", sm, "Demo_Talk", _talk(st, sm, "away", True), None)
    # a temper from level i-1 to i
    for i in range(1, len(acts)):
        p = f"{pre}_t{i}"
        save = f.act(f"{p}_save", "EventSystemActor", "Demo_AutoSave", {"IsWaitFinish": True}, None)
        flags_next = save
        if i == top:
            flags_next = f.act(f"{p}_finish", "EventSystemActor", "Demo_FlagON", _flag_on(flag("Finish", st)), save)
        flg = f.act(f"{p}_flag", "EventSystemActor", "Demo_FlagON", _flag_on(flag(f"Temper{i}", st)), flags_next)
        give = _give(f, f"{p}_give", acts[i], flg)
        done = f.act(f"{p}_done", sm, "Demo_Talk", _talk(st, sm, "done", True), give)
        forge = _forge(f, st, p, acts[i - 1], st.levels[i]["cost"], done)
        checks = _cost_checks(f, p, st.levels[i]["cost"], forge, end_short)
        f.sw(f"{p}_yes", "EventSystemActor", "GeneralChoice2", {}, {0: checks, 1: end_no})
        f.act(f"{p}_offer", sm, "Demo_Talk", _talk(st, sm, f"offer{i}", False), f"{p}_yes")
    # the re-buy: which level comes back is the highest temper done
    rb = f"{pre}_rb"
    save = f.act(f"{rb}_save", "EventSystemActor", "Demo_AutoSave", {"IsWaitFinish": True}, None)
    for i in range(len(acts)):
        _give(f, f"{rb}_give{i}", acts[i], save)
    for i in range(top, 0, -1):
        f.sw(f"{rb}_lvl{i}", "EventSystemActor", "CheckFlag", {"FlagName": flag(f"Temper{i}", st)},
             {1: f"{rb}_give{i}", 0: f"{rb}_lvl{i - 1}" if i > 1 else f"{rb}_give0"})
    first_lvl = f"{rb}_lvl{top}" if top else f"{rb}_give0"
    told = f.act(f"{rb}_done", sm, "Demo_Talk", _talk(st, sm, "reforged", True), first_lvl)
    forge = _forge(f, st, rb, None, st.rebuy, told)
    checks = _cost_checks(f, rb, st.rebuy, forge, end_short)
    f.sw(f"{rb}_yes", "EventSystemActor", "GeneralChoice2", {}, {0: checks, 1: end_no})
    f.act(f"{rb}_offer", sm, "Demo_Talk", _talk(st, sm, "lost", False), f"{rb}_yes")
    # lying in the world (dropped, or the chest's blade fell out with a full pouch)? then no re-buy
    for i in range(len(acts)):
        f.sw(f"{pre}_world{i}", "EventSystemActor", "CheckExistActor",
             {"ActorName": acts[i], "IsCheckEquipStand": True, "IsCheckLife": False},
             {1: f"{pre}_away", 0: f"{pre}_world{i + 1}" if i + 1 < len(acts) else f"{rb}_offer"})
    # which level is in the pouch (highest first)
    for i in range(top, -1, -1):
        hit = f"{pre}_maxed" if i == top else f"{pre}_t{i + 1}_offer"
        f.sw(f"{pre}_pouch{i}", "EventSystemActor", "HasPorchItem", _has(acts[i], 1),
             {1: hit, 0: f"{pre}_pouch{i - 1}" if i else f"{pre}_world0"})
    # the first look at the blade
    seen = f.act(f"{pre}_saw_flag", "EventSystemActor", "Demo_FlagON", _flag_on(flag("SmithSaw", st)),
                 f"{pre}_pouch{top}")
    f.act(f"{pre}_saw3", sm, "Demo_Talk", _talk(st, sm, "saw3", True), seen)
    f.act(f"{pre}_saw2", sm, "Demo_Talk", _talk(st, sm, "saw2", False), f"{pre}_saw3")
    f.act(f"{pre}_saw1", sm, "Demo_Talk", _talk(st, sm, "saw1", False), f"{pre}_saw2")
    return f.sw(f"{pre}_saw", "EventSystemActor", "CheckFlag", {"FlagName": flag("SmithSaw", st)},
                {1: f"{pre}_pouch{top}", 0: f"{pre}_saw1"})


def flow_spec(st: Stage2) -> dict:
    """Our flow Dragonbreath: <Step>_<smith>_Talk for each quest step (the game's quest pattern), FirstDraw."""
    f = _Spec(FLOW)
    sm = st.smith_actor
    bb_flag, bb_entry = st.smith["boulder_breaker_flag"], st.smith["boulder_breaker_entry"]
    menu_body = None
    for step in STEPS:
        p = step.lower()
        if step == "Ready":
            body = f.act(f"{p}_tip1", sm, "Demo_Talk", _talk(st, sm, "tip1", False), f"{p}_tip2")
            f.act(f"{p}_tip2", sm, "Demo_Talk", _talk(st, sm, "tip2", False), f"{p}_tip3")
            f.act(f"{p}_tip3", sm, "Demo_Talk", _talk(st, sm, "tip3", True), f"{p}_activate")
            f.act(f"{p}_activate", "EventSystemActor", "Demo_FlagON", _flag_on(flag("Activated", st)), None)
            f.act(f"{p}_remind", sm, "Demo_Talk", _talk(st, sm, "remind", True), None)
            body = f.sw(f"{p}_told", "EventSystemActor", "CheckFlag", {"FlagName": flag("Activated", st)},
                        {1: f"{p}_remind", 0: body})
        else:
            if menu_body is None:                 # (one smith menu, shared by the Get and Finish steps)
                menu_body = _smith_menu(f, st, "smith")
            body = menu_body
        # his own Boulder Breaker re-forge stays reachable (once he offers it in the game: the same flag his own
        # Talk checks)
        f.sub(f"{p}_bb", sm, bb_entry, {}, None)
        f.sw(f"{p}_pick", "EventSystemActor", "GeneralChoice2", {}, {0: body, 1: f"{p}_bb"})
        f.act(f"{p}_menu", sm, "Demo_Talk", _talk(st, sm, "menu", False), f"{p}_pick")
        f.sw(f"{p}_bbq", "EventSystemActor", "CheckFlag", {"FlagName": bb_flag}, {1: f"{p}_menu", 0: body})
        f.sub(f"{p}_init", "InitTalk", "InitTalk", {"Arg_Turn": 0, "Arg_Greeting": "FollowAISchedule"}, f"{p}_bbq")
        f.spec["entry_points"][f"{step}_{sm}_Talk"] = f"{p}_init"
    # the first draw: Daruk's spirit (made while DarukOn is on), text only. His look is the game's own spirit
    # pattern (the King's spirit on the plateau, FindDungeon<Npc_King001_Appear>): his "Wait" pose, then the "Ghost"
    # look, then the lines, then the "Demo_Disappear" effect. Daruk's AS list has Wait, Ghost and Act_Happy_Wait.
    look = st.raw.get("daruk_look") or {}
    n = len(st.texts["daruk"])
    p = "fd"
    keys = [f"{p}_out", f"{p}_on", f"{p}_still", f"{p}_settle"] + \
        ([f"{p}_wait_as", f"{p}_ghost"] if look.get("ghost") else []) + [f"{p}_speaker", f"{p}_in"] + \
        [f"{p}_daruk{i}" for i in range(1, n + 1)] + ([f"{p}_vanish"] if look.get("vanish_xlink") else []) + \
        [f"{p}_out2", f"{p}_off", f"{p}_done", f"{p}_quest", f"{p}_in2", f"{p}_save"]
    nxt = dict(zip(keys, keys[1:] + [None]))
    f.act(f"{p}_out", "Fader", "Demo_FadeOut", dict(FADE), nxt[f"{p}_out"])
    f.act(f"{p}_on", "EventSystemActor", "Demo_FlagON", _flag_on(flag("DarukOn", st)), nxt[f"{p}_on"])
    f.act(f"{p}_still", "GameROMPlayer", "Demo_Idling", {"IsWaitFinish": True, "DisablePhysics": False},
          nxt[f"{p}_still"])
    # (half a second for the flag's LinkTag to make him, as the game's own spirit scene waits before he acts)
    f.act(f"{p}_settle", "EventSystemActor", "Demo_WaitFrame", {"IsWaitFinish": True, "Frame": 30},
          nxt[f"{p}_settle"])
    if look.get("ghost"):
        f.act(f"{p}_wait_as", DARUK, "Demo_TrigNullASPlay", {"IsWaitFinish": False, "IsIgnoreSame": False,
                                                            "ASName": "Wait", "ASSlot": 0, "SequenceBank": 0},
              nxt[f"{p}_wait_as"])
        f.act(f"{p}_ghost", DARUK, "Demo_PlayASForDemo", {
            "IsWaitFinish": False, "ASName": "Ghost", "TargetIndex": -1, "SeqBank": 0, "IsIgnoreSame": False,
            "IsEnabledAnimeDriven": -1, "MorphingFrame": -1.0, "ClothWarpMode": -2}, nxt[f"{p}_ghost"])
    f.act(f"{p}_speaker", DARUK, "Demo_BecomeSpeaker", {"IsWaitFinish": True}, nxt[f"{p}_speaker"])
    f.act(f"{p}_in", "Fader", "Demo_FadeIn", dict(FADE), nxt[f"{p}_in"])
    for i in range(1, n + 1):
        prm = _talk(st, DARUK, f"daruk{i}", i == n)
        if i == 1 and look.get("laugh_as"):
            prm["ASName"] = look["laugh_as"]                 # (his first line is the laugh)
        f.act(f"{p}_daruk{i}", DARUK, "Demo_Talk", prm, nxt[f"{p}_daruk{i}"])
    if look.get("vanish_xlink"):
        k = look["vanish_xlink"]
        f.act(f"{p}_vanish", DARUK, "Demo_XLinkEventCreate", {"IsWaitFinish": False, "ELinkKey": k, "SLinkKey": k,
                                                               "IsTargetDemoSLinkUser": False}, nxt[f"{p}_vanish"])
    f.act(f"{p}_out2", "Fader", "Demo_FadeOut", dict(FADE), nxt[f"{p}_out2"])
    f.act(f"{p}_off", "EventSystemActor", "Demo_FlagOFF", _flag_on(flag("DarukOn", st)), nxt[f"{p}_off"])
    f.act(f"{p}_done", "EventSystemActor", "Demo_FlagON", _flag_on(flag("FirstDraw", st)), nxt[f"{p}_done"])
    # found the cave alone, without Rohan's tip: the quest starts here (Ready -> Get follows IsGet)
    f.act(f"{p}_quest", "EventSystemActor", "Demo_FlagON", _flag_on(flag("Activated", st)), nxt[f"{p}_quest"])
    f.act(f"{p}_in2", "Fader", "Demo_FadeIn", dict(FADE), nxt[f"{p}_in2"])
    f.act(f"{p}_save", "EventSystemActor", "Demo_AutoSave", {"IsWaitFinish": True}, None)
    f.sw(f"{p}_check", "EventSystemActor", "CheckFlag", {"FlagName": flag("FirstDraw", st)}, {1: None, 0: f"{p}_out"})
    f.spec["entry_points"]["FirstDraw"] = f"{p}_check"
    return f.spec


def flow_problems(st: Stage2, spec: dict, labels: set[str] | None = None, items: set[str] | None = None,
                  flags: set[str] | None = None) -> list[str]:
    """mapkit's flow checks plus ours: every message has a label, every item and flag exists, every event is
    reachable from an entry."""
    out = mk.check_flow(spec)
    labels = set(messages(st)) if labels is None else labels
    ev = spec["events"]
    seen: set[str] = set()
    todo = list(spec["entry_points"].values())
    while todo:
        k = todo.pop()
        if k in seen or k not in ev:
            continue
        seen.add(k)
        e = ev[k]
        todo += [n for n in ([e.get("next")] if e["kind"] != "switch" else list(e["cases"].values())) if n]
    out += [f"{spec['name']}: {k} is never reached" for k in ev if k not in seen]
    for k, e in ev.items():
        prm = e.get("params") or {}
        mid = prm.get("MessageId")
        if mid is not None:
            fl, _, lab = mid.partition(":")
            if fl != f"EventFlowMsg/{FLOW}" or lab not in labels:
                out.append(f"{k}: the message {mid} isn't one of ours")
        for pk in ("PorchItemName", "CheckTargetActorName", "ActorName"):
            if items is not None and pk in prm and prm[pk] not in items:
                out.append(f"{k}: {prm[pk]} isn't an item or actor")
        if flags is not None and "FlagName" in prm and prm["FlagName"] not in flags:
            out.append(f"{k}: the flag {prm['FlagName']} isn't defined")
    return out


# ------------------------------------------------------------------------------------------------ the quest

def quest_entry(st: Stage2, nodes: list) -> dict:
    """The quest list entry (the game's Mini quests' shape). nodes: Rohan's day schedule (copied from the game)."""
    q, sm = st.quest["name"], st.smith_actor
    sx, sy, sz = st.smith["pos"]

    def actor(step: str) -> dict:
        return {"DefaultTimeline": {"Nodes": copy.deepcopy(nodes), "TriggerEvents": [
            {"EvflEntry": f"{step}_{sm}_Talk", "IsPauseOtherActors": True, "Trigger": "Talk"}]},
            "DisableClipping": False, "DisplayDistanceMode": "None", "Name": sm, "UniqueName": ""}

    at_smith = [{"HasFar": False, "InstanceName": "", "Location": {"X": bd.f32(sx), "Y": bd.f32(0.0),
                                                                   "Z": bd.f32(sz)},
                 "Name": sm, "OffFlag": ""}]
    mx, mz = st.quest["marker"]
    return {"Location": st.quest["location"], "Name": q, "NotPostNote": False, "Orderer": sm,
            "StepDependencyFlags": [{"DependencyFlag": f"IsGet_{st.base_actor}", "DependencyFlagType": "bool",
                                     "NextFlag": flag("Get", st)}],
            "Steps": [
                {"Actors": [actor("Ready")], "AttentionOff": False,
                 "IndicatorLocations": [{"Location": {"X": bd.f32(mx), "Y": bd.f32(0.0), "Z": bd.f32(mz)},
                                         "OffFlag": flag("Get", st)}],
                 "MessageName": "Desc", "Name": "Ready", "NextFlag": flag("Activated", st), "UpdateStep": True},
                {"Actors": [actor("Get")], "AttentionOff": False, "IndicatorActors": copy.deepcopy(at_smith),
                 "MessageName": "Get", "Name": "Get", "NextFlag": flag("Get", st), "UpdateStep": True},
                {"Actors": [actor("Finish")], "AttentionOff": False, "MessageName": "Finish", "Name": "Finish",
                 "NextFlag": flag("Finish", st), "UpdateStep": True}],
            "Type": "Mini"}


def smith_timeline(quests: list, st: Stage2) -> list:
    """The day schedule nodes to give Rohan in our quest steps: the recipe's donor NPC's, from the game's quest
    list (its first step)."""
    qname, npc = st.smith["timeline_from"]
    for qd in quests or []:
        if isinstance(qd, dict) and qd.get("Name") == qname:
            for step in qd.get("Steps") or []:
                for a in step.get("Actors") or []:
                    if a.get("Name") == npc and isinstance(a.get("DefaultTimeline"), dict):
                        nodes = a["DefaultTimeline"].get("Nodes")
                        if isinstance(nodes, list) and nodes:
                            return copy.deepcopy(nodes)
    raise Stage2Error(f"your game's quest list has no day schedule for {npc} in {qname} (the recipe's "
                      "smith timeline_from)")


def quests_log(st: Stage2, nodes: list) -> dict:
    e = quest_entry(st, nodes)
    e[bm.PREV] = st.quest["prev_quest"]
    return {"add": [e], "mod": {}, "del": []}


def event_infos(spec: dict, game_talk: dict | None = None) -> dict:
    """The event info entries: FirstDraw as a map EventTag's event (DEMO_INFO), the rest as Rohan's own talk
    (TALK_INFO when the game's has none)."""
    return mk.info_entries(spec, game_talk, {"FirstDraw": DEMO_INFO})


# ------------------------------------------------------------------------------------------------ the map

def local_to_world(cave: dict, along: float, across: float) -> tuple[float, float]:
    """A cave spot (metres along the corridor from the rock's origin, metres across) on the map. The rock's yaw is a
    standard Y rotation (x' = x cos + z sin, z' = -x sin + z cos), as the cave study measured it."""
    cx, _, cz = cave["origin"]
    a = math.radians(cave["yaw"])
    c, s = math.cos(a), math.sin(a)
    return cx + along * c + across * s, cz - along * s + across * c


def hid(key: str, used: set[int]) -> bd.U32:
    """A HashId for one of our objects: from its name, so a rebuild gives the same one; never one in use."""
    return mk.hash_id("giruka.dragonbreath/stage2", key, used)


def _strip(params: dict | None) -> dict | None:
    if not isinstance(params, dict):
        return None
    return {k: v for k, v in params.items() if k not in ("TraversePos", "TraverseRadiusXZ")}


def template_params(units: dict[str, dict]) -> dict[str, dict]:
    """actor -> the !Parameters of the first copy the game places nearby (TraversePos left out: it is a spot's)."""
    out: dict[str, dict] = {}
    for _, unit in sorted(units.items()):
        for o in (unit.get("Objs") or []) if isinstance(unit, dict) else []:
            n = o.get("UnitConfigName") if isinstance(o, dict) else None
            if isinstance(n, str) and n not in out and isinstance(o.get("!Parameters"), dict):
                out[n] = _strip(o["!Parameters"])
    return out


@dataclass
class CaveMap:
    static: list = field(default_factory=list)        # the rock and its far model
    dynamic: list = field(default_factory=list)
    chest_hid: int = 0
    loose: dict = field(default_factory=dict)          # key -> (actor, HashId) of loose items (each needs a flag)
    spots: dict = field(default_factory=dict)          # key -> (x, y, z)


def cave_objects(st: Stage2, used: set[int], ground, params_like: dict[str, dict], *, chest_drop: str,
                 with_events: bool = True) -> CaveMap:
    """Everything stage 2 places. ground(x, z) -> terrain height; params_like: template_params(). with_events=False:
    no first-draw trigger (Area, its LinkTags, EventTag) and no Daruk (no event flow to start)."""
    cave = st.cave
    used = set(used)
    res = CaveMap()
    keys = ["rock", "rock_far"] + [o["key"] for o in cave["objects"]] + \
        ["chest", "daruk", "daruk_gate", "fd_gate", "fd_off", "fd_area", "fd_relay", "fd_event"]
    ids = {k: hid(k, used) for k in keys}
    oy = cave["origin"][1]
    yaw = cave["yaw"]

    def spot(key, at, lift=0.0):
        x, z = local_to_world(cave, at[0], at[1])
        y = ground(x, z) + lift
        res.spots[key] = (x, y, z)
        return [x, y, z]

    def params_for(actor, over=None):
        p = copy.deepcopy(params_like.get(actor) or (cave.get("default_params") or {}).get(actor))
        if over:
            p = dict(p or {}, **over)
        return p

    # the rock (the game's Gorko Tunnel pair: the near model links its far model as PlacementLOD)
    pos = [cave["origin"][0], oy, cave["origin"][2]]
    res.static.append(mk.map_object("rock_far", cave["rock_far"], pos, ids, yaw=yaw, extra={"OnlyOne": True}))
    res.static.append(mk.map_object("rock", cave["rock"], pos, ids, yaw=yaw, extra={"OnlyOne": True},
                              links=[mk.map_link("PlacementLOD", ids["rock_far"], None)]))
    for o in cave["objects"]:
        a = o["actor"]
        p = params_for(a, o.get("params"))
        if a.startswith("Item_"):
            p = dict(p or PLACED_PARAMS)      # (a loose item: the params stage 1's blade lies with)
            res.loose[o["key"]] = (a, int(ids[o["key"]]))
        res.dynamic.append(mk.map_object(o["key"], a, spot(o["key"], o["at"], float(o.get("lift", 0.0))), ids,
                                   yaw=yaw + float(o.get("yaw", 0.0)), params=p))
    ch = cave["chest"]
    res.dynamic.append(mk.map_object("chest", ch["actor"], spot("chest", ch["at"]), ids, yaw=yaw + float(ch.get("yaw", 0)),
                               params=params_for(ch["actor"], {"DropActor": chest_drop, "EnableRevival": False})))
    res.chest_hid = int(ids["chest"])
    if not with_events:
        res.static.sort(key=lambda o: int(o["HashId"]))
        res.dynamic.sort(key=lambda o: int(o["HashId"]))
        return res
    # Daruk's spirit: made while Dragonbreath_DarukOn is on (a LinkTagAnd that only reads the flag)
    dk = cave["daruk"]
    res.dynamic.append(mk.map_object("daruk", dk["actor"], spot("daruk", dk["at"]), ids, yaw=yaw + float(dk.get("yaw", 0)),
                               params=params_for(dk["actor"]), extra={"OnlyOne": True}))
    res.dynamic.append(mk.flag_gate("daruk_gate", spot("daruk_gate", dk["at"]), ids, flag("DarukOn", st),
                                    creates=["daruk"]))
    # the first-draw trigger: made once the blade is got (IsGet), deleted once the scene has played (FirstDraw)
    fa = cave["first_draw_area"]
    at = spot("fd_area", fa["at"])
    res.dynamic += mk.trigger_chain("fd", at, ids, flow=FLOW, entry="FirstDraw", half=fa.get("half", [3.0, 3.0, 3.0]),
                                    yaw=yaw)
    res.dynamic.append(mk.flag_gate("fd_gate", at, ids, f"IsGet_{st.base_actor}", creates=["fd_area"]))
    res.dynamic.append(mk.flag_gate("fd_off", at, ids, flag("FirstDraw", st), deletes=["fd_area"]))
    res.static.sort(key=lambda o: int(o["HashId"]))
    res.dynamic.sort(key=lambda o: int(o["HashId"]))
    return res


def map_log(st: Stage2, cm: CaveMap) -> dict:
    return {st.cave["static_unit"]: {"Objs": {"add": cm.static}}, st.cave["unit"]: {"Objs": {"add": cm.dynamic}}}


def placement_problems(st: Stage2, min_gap: float = 1.5) -> list[str]:
    """Recipe-only checks on the cave layout: inside the corridor, not on top of each other."""
    cave, out = st.cave, []
    (x0, x1), (z0, z1) = cave["corridor"]["x"], cave["corridor"]["z"]
    pts = [(o["key"], o["at"]) for o in cave["objects"]] + [("chest", cave["chest"]["at"]),
                                                             ("daruk", cave["daruk"]["at"])]
    for k, (a, c) in pts:
        if not (x0 <= a <= x1 and z0 <= c <= z1):
            out.append(f"{k} at {a:g}, {c:g} is outside the corridor")
    for i, (k1, p1) in enumerate(pts):
        for k2, p2 in pts[i + 1:]:
            if math.hypot(p1[0] - p2[0], p1[1] - p2[1]) < min_gap:
                out.append(f"{k1} and {k2} are closer than {min_gap:g} m")
    return out


# ------------------------------------------------------------------------------------------------ flags

def flags_logs(st: Stage2, cm: CaveMap, game_flags: dict[str, dict]) -> tuple[dict, dict]:
    """(gamedata.yml, savedata.yml) for the item mod: every flag a save keeps, shaped like the game's own of the
    same kind (a quest's, IsGet, a chest's, a loose item's)."""
    def like(prefix: str) -> dict | None:
        return next((v for k, v in sorted(game_flags.items()) if k.startswith(prefix)), None)

    entry = ak.flag_entry          # (the game's entry of the same kind, renamed; the defaults when there is none)

    quest_like = game_flags.get(f"{st.quest['prev_quest']}_Activated")
    names = [flag(k, st) for k in QUEST_FLAGS] + [flag(f"Temper{i}", st) for i in range(1, len(st.levels))]
    es = [entry(n, quest_like, **({"IsOneTrigger": False} if n == flag("DarukOn", st) else {})) for n in names]
    isget_like = game_flags.get(f"IsGet_{st.raw.get('donor_like', 'Weapon_Sword_047')}")
    es += [entry(f"IsGet_{a}", isget_like, **({} if isget_like else {"IsOneTrigger": True}))
           for a in st.level_actors()[1:]]
    es.append(entry(f"{CHEST_FLAG_LIKE}{cm.chest_hid}", like(CHEST_FLAG_LIKE)))
    for _, (actor, h) in sorted(cm.loose.items()):
        lk = like(f"MainField_{actor}_")
        es.append(entry(f"MainField_{actor}_{h}", lk, **({} if lk else {"ResetType": 1})))
    gd = {"bool_data": {"add": {e["DataName"]: e for e in es}, "del": []}}
    sd = {"add": [{"DataName": e["DataName"], "HashValue": e["HashValue"]} for e in es], "del": []}
    return gd, sd


def all_flags(st: Stage2, cm: CaveMap) -> set[str]:
    gd, _ = flags_logs(st, cm, {})
    return set(gd["bool_data"]["add"]) | {f"IsGet_{st.base_actor}", st.smith["boulder_breaker_flag"]}


# ------------------------------------------------------------------------------------------------ writing

def _merge_text_log(old: str | None, new: dict) -> str:
    base = json.loads(old) if old else {}
    for lang, files in new.items():
        for name, msgs in files.items():
            base.setdefault(lang, {}).setdefault(name, {}).update(msgs)
    return json.dumps(base, indent=1, ensure_ascii=False) + "\n"


def _merge_yaml_log(name: str, old: str | None, new) -> str:
    if not old:
        return bd.to_text(new)
    cur = bd.from_text(old)
    if name == "map.yml":
        for unit, d in new.items():
            add = cur.setdefault(unit, {}).setdefault("Objs", {}).setdefault("add", [])
            add.extend(d["Objs"]["add"])
        return bd.to_text(cur)
    if name == "savedata.yml":
        cur.setdefault("add", []).extend(new["add"])
        return bd.to_text(cur)
    if name == "quests.yml":
        cur.setdefault("add", []).extend(new["add"])
        return bd.to_text(cur)
    if name in ("gamedata.yml", "eventinfo.yml"):
        return bd.to_text(bd.dict_merge(cur, new))
    raise Stage2Error(f"stage 2 can't merge into logs/{name}")


def merge_logs(folder: str, logs: dict[str, object]) -> None:
    """Add our log entries to a package folder's logs (stage 1's, or a fresh folder)."""
    os.makedirs(os.path.join(folder, "logs"), exist_ok=True)
    for name, new in logs.items():
        path = os.path.join(folder, "logs", name)
        old = None
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                old = f.read()
        text = _merge_text_log(old, new) if name == "texts.json" else _merge_yaml_log(name, old, new)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


def check_logs(logs: dict[str, str]) -> None:
    try:
        cb.check_logs(logs)
    except mk.MapError as e:
        raise Stage2Error(str(e).replace("the castle's", "Dragonbreath stage 2's")) from None


@dataclass
class Built2:
    flow: dict = field(default_factory=dict)
    events: bool = False
    cave: CaveMap | None = None
    quest: bool = False
    notes: list[str] = field(default_factory=list)


def game_quests(game) -> list:
    tb = bnp.Blob(game.read("Pack/TitleBG.pack"))
    return bd.byml_load(_unyaz0(tb.child("Quest/QuestProduct.sbquestpack").raw))


def game_eventinfo(game) -> dict:
    return bd.byml_load(_unyaz0(game.bootup().child("Event/EventInfo.product.sbyml").raw))


_unyaz0 = mk.unyaz0


def smith_pack(game, st: Stage2) -> dict[str, tuple[bytes, int]]:
    return dict(bnp.Blob(game.read(f"Event/{st.smith_actor}.sbeventpack")).open().items())


def build_stage2(game, st: Stage2, item_dir: str, main_dir: str, *, used: set[int] | None = None, log=None,
                 events: bool = True) -> Built2:
    """Write stage 2 into stage 1's two package folders (item_dir, main_dir: their romfs and logs). game: an
    ak47_build.Game (read only). used: HashIds to keep clear of. events=False: no event flows (the cave and chest
    are still placed; no quest, no first-draw trigger)."""
    res = Built2()
    bad = text_problems(st) + placement_problems(st)
    if bad:
        raise Stage2Error("the stage 2 recipe is wrong: " + "; ".join(bad))
    ai = game.actorinfo()
    need = {o["actor"] for o in st.cave["objects"]} | {st.cave["rock"], st.cave["rock_far"], st.cave["chest"]["actor"],
                                                       DARUK, st.smith_actor} | set(st.rebuy) | \
        {k for x in st.levels[1:] for k in x["cost"]}
    gone = sorted(n for n in need if n not in ai)
    if gone:
        raise Stage2Error(f"your game's actor info has no {', '.join(gone)}")
    # the map
    _log(log, "Placing the diamond cave, its monsters and the chest...")
    units = {}
    for u in st.cave.get("template_units") or []:
        with contextlib.suppress(Exception):
            units[u] = game.unit(u)
    terrain = game.terrain()
    taken = set(game.hash_ids(log)) | set(used or ())
    cm = cave_objects(st, taken, lambda x, z: terrain.height(x, z)[0], template_params(units),
                      chest_drop=st.base_actor, with_events=events)
    res.cave = cm
    main_logs: dict[str, object] = {"map.yml": map_log(st, cm)}
    gd, sd = flags_logs(st, cm, game.flags())
    main_texts, item_texts = texts_logs(st)
    item_logs: dict[str, object] = {"gamedata.yml": gd, "savedata.yml": sd, "texts.json": item_texts}
    # the event flows and the quest
    spec = flow_spec(st)
    res.flow = spec
    probs = flow_problems(st, spec, flags=all_flags(st, cm))
    if probs:
        raise Stage2Error("stage 2's event flow is wrong: " + "; ".join(probs[:5]))
    shared = smith_pack(game, st)
    why = None if events else "turned off"
    if why is None:
        why = mk.evfl_ready(shared, source=st.smith_actor)
    if why:
        res.notes.append(f"event flows not built ({why}): the cave and chest are placed, but no quest, talk or "
                         "first-draw scene")
        if events:      # (evfl.py not ready: place the cave again without the trigger and Daruk)
            cm = cave_objects(st, taken, lambda x, z: terrain.height(x, z)[0], template_params(units),
                              chest_drop=st.base_actor, with_events=False)
            res.cave = cm
            main_logs["map.yml"] = map_log(st, cm)
            gd, sd = flags_logs(st, cm, game.flags())
            item_logs.update({"gamedata.yml": gd, "savedata.yml": sd})
    else:
        _log(log, "Writing the event flow (tools/evfl.py)...")
        try:
            data = mk.build_flow(spec)
        except mk.MapError as e:
            raise Stage2Error(str(e)) from None
        infos = event_infos(spec, (game_eventinfo(game) or {}).get(f"{st.smith_actor}<Talk>"))
        raw = game.read(f"Event/{st.smith_actor}.sbeventpack")
        align = mk.yaz0_align(raw)
        # the game's InitTalk and Common, Rohan's own flow (his Boulder Breaker re-forge) and his pack's other files
        # (the forging sounds his talk's event info names as slink_user)
        try:
            pdata, plen = mk.event_pack(FLOW, data, shared, sorted({"InitTalk", "Common", st.smith_actor}), align,
                                        source=st.smith_actor, other_files=True)
        except mk.MapError as e:
            raise Stage2Error(str(e)) from None
        ak._write(os.path.join(main_dir, ak.BASE_TID, "romfs", "Event", f"{FLOW}.sbeventpack"), pdata)
        main_logs["eventinfo.yml"] = infos
        main_logs["quests.yml"] = quests_log(st, smith_timeline(game_quests(game), st))
        res.events = res.quest = True
        rstb = game.rstb()
        g = rstb.get(f"Event/{st.smith_actor}.beventpack") if rstb else None
        if g:
            main_logs["rstb.json"] = {f"Event/{FLOW}.beventpack":
                                      ukmm.estimate_entry(len(_unyaz0(raw)), g, plen, True)}
    main_logs["texts.json"] = main_texts
    # read back with modman's mergers before anything is written
    check_logs({n: (json.dumps(v) if n.endswith(".json") else bd.to_text(v)) for n, v in
                {**main_logs, **item_logs}.items()})
    rst = main_logs.pop("rstb.json", None)
    merge_logs(main_dir, main_logs)
    merge_logs(item_dir, item_logs)
    if rst:
        path = os.path.join(main_dir, "logs", "rstb.json")
        cur = {}
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                cur = json.load(f)
        cur.update(rst)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(dict(sorted(cur.items())), indent=1) + "\n")
    return res


# ------------------------------------------------------------------------------------------------ check

def check(library: str | None = None, *, install: str | None = None, folder: str = STAGE2, log=None) -> list[str]:
    """Offline checks on the player's files (reads only): every actor, item and flag stage 2 names exists or is
    ours, tools/evfl.py rebuilds Rohan's event pack byte for byte, our flow writes and reads back, every action and
    query our flow sends a game actor is one the game's own flows send it, the quest's donor schedule is there, the
    cave spots are on your terrain."""
    import modman as mm  # noqa: PLC0415
    st = load_stage2(folder)
    out = text_problems(st) + placement_problems(st)
    lib = mm.Library.load(mm.resolve_library(library))
    install = install or (lib.install if lib else None) or mm.default_install()
    game = ak.Game(os.path.abspath(install), "Weapon_Sword_047")
    if not os.path.isdir(game.romfs):
        return out + [f"Savage Wind's game files aren't in {game.romfs}"]
    ai = game.actorinfo()
    for n in sorted({o["actor"] for o in st.cave["objects"]} | {st.cave["rock"], st.cave["rock_far"], DARUK,
                                                                 st.cave["chest"]["actor"], st.smith_actor}):
        if n not in ai:
            out.append(f"actor {n} isn't in your game")
    items = set(st.rebuy) | {k for x in st.levels[1:] for k in x["cost"]}
    for n in sorted(items):
        if ai.get(n, {}).get("profile") != "Item":
            out.append(f"{n} isn't an item in your game")
    for a in st.level_actors():
        if a in ai and a != st.base_actor:
            out.append(f"your game already has {a}")
    flags = game.flags()
    for k in QUEST_FLAGS:
        if flag(k, st) in flags:
            out.append(f"your game already has the flag {flag(k, st)}")
    if st.smith["boulder_breaker_flag"] not in flags:
        out.append(f"your game has no flag {st.smith['boulder_breaker_flag']} (Rohan's Boulder Breaker check)")
    try:
        quests = game_quests(game)
        if any(isinstance(q, dict) and q.get("Name") == st.quest["name"] for q in quests):
            out.append(f"your game already has a quest {st.quest['name']}")
        if not any(isinstance(q, dict) and q.get("Name") == st.quest["prev_quest"] for q in quests):
            out.append(f"your game has no quest {st.quest['prev_quest']} to put ours after")
        smith_timeline(quests, st)
    except Stage2Error as e:
        out.append(str(e))
    except Exception as e:  # noqa: BLE001 (a damaged file: say so, don't stop the other checks)
        out.append(f"your quest list can't be read ({type(e).__name__}: {e})")
    # the flow: evfl rebuilds Rohan's pack, ours writes and reads back, every action/query is a known one
    try:
        shared = smith_pack(game, st)
        why = mk.evfl_ready(shared, source=st.smith_actor)
        if why:
            out.append(why)
        else:
            spec = flow_spec(st)
            cm = cave_objects(st, set(), lambda x, z: 0.0, {}, chest_drop=st.base_actor)
            out += flow_problems(st, spec, items=items | set(st.level_actors()), flags=all_flags(st, cm))
            mk.build_flow(spec)
            out += unknown_calls(spec, known_calls(game, st, log))
    except (Stage2Error, mk.MapError) as e:
        out.append(str(e))
    # Daruk's poses the scene asks for are in his AS list
    try:
        have = as_names(game, DARUK)
        look = st.raw.get("daruk_look") or {}
        want = (["Wait", "Ghost"] if look.get("ghost") else []) + ([look["laugh_as"]] if look.get("laugh_as") else [])
        out += [f"{DARUK}'s AS list has no {a}" for a in want if a not in have]
    except Exception as e:  # noqa: BLE001 (a damaged file: say so)
        out.append(f"{DARUK}'s AS list can't be read ({type(e).__name__}: {e})")
    # the cave spots on your terrain (the study's corridor fit: floor within -6..+9 m of the rock's origin)
    try:
        t = game.terrain()
        oy = st.cave["origin"][1]
        for o in st.cave["objects"] + [st.cave["chest"], st.cave["daruk"]]:
            x, z = local_to_world(st.cave, *o["at"])
            h = t.height(x, z)[0]
            if not -6.5 <= h - oy <= 9.5:
                out.append(f"{o.get('key', o['actor'])}: your terrain there is {h - oy:+.1f} m from the cave's "
                           "origin (the corridor floor is -6..+9 m)")
    except ak.AkError as e:
        out.append(str(e))
    return out


def as_names(game, actor: str) -> set[str]:
    """The AS names in an actor's AS list (its pack's .baslist)."""
    import aamp  # noqa: PLC0415
    files = dict(bnp.Blob(game.read(f"Actor/Pack/{actor}.sbactorpack")).open().items())
    out: set[str] = set()
    for name, (data, _) in files.items():
        if name.endswith(".baslist"):
            defs = aamp.load(data).list("ASDefines")
            out |= {o.get_value("Name") for o in (defs.objects.values() if defs else [])}
    return out


def known_calls(game, st: Stage2, log=None) -> dict[str, set[str]]:
    """actor -> the actions and queries the game's own flows send it, from a few packs that use the same kinds of
    steps (Rohan's, the Goron quests', the castle's villager, the King's spirit on the plateau: the game's only
    flow where a spirit NPC appears, takes its ghost look and talks)."""
    import evfl  # noqa: PLC0415
    out: dict[str, set[str]] = {}
    packs = [f"Event/{st.smith_actor}.sbeventpack", "Event/GoronMini_WallCrackTBox.sbeventpack",
             "Event/GoronCityMini_BeatGolem.sbeventpack", f"Event/{cb.VILLAGER}.sbeventpack",
             "Event/FindDungeon.sbeventpack"]
    packs += st.raw.get("check_packs") or []
    for rel in packs:
        if not os.path.isfile(game.path(rel)):
            continue
        for name, (data, _) in bnp.Blob(game.read(rel)).open().items():
            if not name.endswith(".bfevfl"):
                continue
            try:
                fl = evfl.load(data)
            except (evfl.EvflError, struct.error, IndexError, ValueError, UnicodeDecodeError):
                continue
            for a in fl.actors:
                s = out.setdefault(a.name, set())
                s.update(a.actions)
                s.update(a.queries)
    return out


def unknown_calls(spec: dict, known: dict[str, set[str]]) -> list[str]:
    """Our calls no game flow in `known` makes on that actor. No game flow sends Daruk (Dm_Npc_Goron_Hero) anything
    (his scenes are timelines), so a Dm_Npc_ actor is held to what the game's flows send its spirit kin (the King's
    spirit Dm_Npc_Hyrule_KingSoul: same NPC AI program, also a Ghost AS) and talking NPCs; an Npc_ to talking NPCs."""
    npc = set().union(*(v for k, v in known.items() if k.startswith("Npc_"))) if known else set()
    spirit = npc.union(*(v for k, v in known.items() if k.startswith("Dm_Npc_"))) if known else set()
    out = []
    for k, e in spec["events"].items():
        if e["kind"] == "subflow":
            continue
        call = e["action"] if e["kind"] == "action" else e["query"]
        have = known.get(e["actor"]) or (spirit if e["actor"].startswith("Dm_Npc_") else
                                         npc if e["actor"].startswith("Npc_") else set())
        if call not in have:
            out.append(f"{k}: no game flow read sends {e['actor']} {call} [needs a game check]")
    return sorted(set(out))


# ------------------------------------------------------------------------------------------------ command line

def plan_text(st: Stage2) -> str:
    spec = flow_spec(st)
    cm = cave_objects(st, set(), lambda x, z: st.cave["origin"][1], {}, chest_drop=st.base_actor)
    lines = [f"Dragonbreath stage 2 ({len(spec['events'])} flow events, entries: {', '.join(spec['entry_points'])})",
             f"  cave: {len(cm.static)} rock objects in {st.cave['static_unit']}, {len(cm.dynamic)} objects in "
             f"{st.cave['unit']}",
             f"  quest {st.quest['name']} ({st.texts['quest_name']}): {', '.join(STEPS)}; Rohan = {st.smith_actor}",
             f"  re-buy: {cost_text(st, st.rebuy)}"]
    for i, lv in enumerate(st.levels):
        lines.append(f"  {st.level_actor(i)}: attack {lv['attack']}, Guardians x{lv['guardian_ratio']:g}"
                     + (f", costs {cost_text(st, lv['cost'])}" if i else ""))
    for p in text_problems(st) + placement_problems(st) + flow_problems(st, spec):
        lines.append(f"  problem: {p}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    with contextlib.suppress(AttributeError, ValueError, OSError):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(prog="dragonbreath_stage2.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="offline checks on your files (reads only)")
    c.add_argument("--library")
    c.add_argument("--game")
    sub.add_parser("plan", help="what stage 2 makes, from the recipe alone")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "plan":
            print(plan_text(load_stage2()))
            return 0
        ak._low_priority()
        problems = check(args.library, install=args.game, log=print)
        print("Dragonbreath stage 2 check: all good." if not problems else
              f"Dragonbreath stage 2 check: {len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1 if problems else 0
    except Stage2Error as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
