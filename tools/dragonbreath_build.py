#!/usr/bin/env python3
"""Dragonbreath (a Breathalyzer weapon mod by giruka, docs/dragonbreath.md), stage 1: the blade itself. Builds two mods
on your PC from your own game files and a model made in code, and adds them to your mod library with modman.

    python tools/dragonbreath_build.py build [--on] [--library DIR] [--game DIR] [--no-add --out DIR] [--optional]
                                             [--quiet] [--test-spot] [--no-events]
    python tools/dragonbreath_build.py check [--library DIR] [--game DIR]   offline checks on your files (reads only;
                                                                            stage 2's too)
    python tools/dragonbreath_build.py preview --out FILE.png [--scale N]   a picture of the blade (no game files)
    python tools/dragonbreath_build.py where [--library DIR] [--game DIR]   the game files it reads
    python tools/dragonbreath_build.py clean [--item] [--library DIR]       take it out of the library

A new one-handed sword, Weapon_Sword_Dragonbreath ("Dragonbreath"): the Royal Guard's Sword (Weapon_Sword_047, a plain
metal sword: no glow, no fire) as the base, with our own model (tools/dragonbreath_model.py: an extremely slim curved
blade with an engraved dragon, made from formulas), our own stats file and texts. Our own parts are in
assets/dragonbreath/: the recipe (dragonbreath.json) and the two mod.toml files. Nothing from the game is in this repo
or in what we ship: every pack, model, icon, map change and size entry is made here from the player's
`extracted\\romfs` (only read).

What `build` makes: two BCML-style package folders that `modman add` installs (the map, actor info, game data, save
data and text changes are merged by tools/bcml_maps.py, bcml_data.py and bcml_texts.py the normal way).

giruka.dragonbreath-item (item.toml: secret, no part_of, so it stays on and a save that holds the blade loads):
  Actor/Pack/Weapon_Sword_Dragonbreath.sbactorpack   the Royal Guard's Sword's pack: actor link and model list renamed,
                                                     our stats file (GParamUser renamed: the donor's is left out of
                                                     the pack), tags changed (dropped: Hyrule Compendium, Blood Moon
                                                     revival, amiibo; added: CannotSell, RevivalNone, NotRandomSet,
                                                     ApplyHighGradeGetDemo)
  Actor/GeneralParamList/Weapon_Sword_Dragonbreath.bgparamlist (inside the pack): attack 40, life 1000 with
                                                     IsLifeInfinite (never breaks, the One-Hit Obliterator's way),
                                                     not throwable, no random "+" bonuses, sub-type "Dragonbreath",
                                                     the ancient weapons' Guardian lists with a ratio of 0.25 (weak
                                                     against Guardians)
  Model/Weapon_Sword_Dragonbreath.sbfres, .Tex.sbfres  the donor's model file (its Root bone, its material) with our
                                                     mesh; our textures in the donor's formats and sizes
  UI/StockItem/Weapon_Sword_Dragonbreath.sbitemico   the inventory icon, drawn from our model
  Actor/Pack/Weapon_Sword_Dragonbreath_1.._4.sbactorpack, UI/StockItem/..._1.._4.sbitemico   the 4 tempered levels
                                                     (stage 2's temper table: their own stats; one model for all
                                                     five, the base blade's: actor_pack(model=))
  logs/actorinfo.yml, texts.json, gamedata.yml, savedata.yml, rstb.json (stage 2 adds its flags and texts)

botw-mod-loader.dragonbreath (mod.toml: the main switch, off by default): stage 2 (tools/dragonbreath_stage2.py,
run by build_package): the diamond cave and its chest, Rohan's quest, Daruk's first draw (logs/map.yml, quests.yml,
eventinfo.yml, texts.json, Event/Dragonbreath.sbeventpack). --test-spot (developers only): also one blade lying near
the test save's start; off for players, as picking it up moves the quest on.

Pure Python (no third-party packages); the writers and helpers are tools/ak47_build.py's and tools/wiiu_model's.
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
import tomllib
import traceback
import zlib
from dataclasses import dataclass, field

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import aamp  # noqa: E402
import ak47_build as ak  # noqa: E402
import bcml_data as bd  # noqa: E402
import bcml_maps as bm  # noqa: E402
import bcml_texts as bt  # noqa: E402
import bnp  # noqa: E402
import dragonbreath_model as dm  # noqa: E402
import dragonbreath_stage2 as s2  # noqa: E402
import menu_bg  # noqa: E402
import modman as mm  # noqa: E402
import ukmm  # noqa: E402
import wiiu_convert as wc  # noqa: E402
from wiiu_model import checks, formats, nx_bfres, read_model  # noqa: E402
from wiiu_model.errors import ModelError  # noqa: E402

REPO = os.path.dirname(TOOLS)
RECIPE = os.path.join(REPO, "assets", "dragonbreath")
RECIPE_JSON = "dragonbreath.json"
ITEM_TOML = "item.toml"
BASE_TID = ak.BASE_TID
MSG_FILE = "ActorType/WeaponSmallSword"
NEAR_RADIUS = 2.0            # m: nothing the game places may stand this near the test spot
GROUND_TOLERANCE = 2.0
MAX_SLOPE_SPREAD = 0.6
MAX_TEXT_LINE = 40           # characters per description line (the inventory's box)
MAX_TEXT_LINES = 4
SHARP_KEYS = ("SharpWeaponAddAtkMin", "SharpWeaponAddAtkMax", "SharpWeaponAddLifeMin", "SharpWeaponAddLifeMax",
              "SharpWeaponAddGuardMin", "SharpWeaponAddGuardMax", "PoweredSharpAddAtkMin", "PoweredSharpAddAtkMax",
              "PoweredSharpAddLifeMin", "PoweredSharpAddLifeMax", "PoweredSharpWeaponAddGuardMin",
              "PoweredSharpWeaponAddGuardMax")
DONOR_NAME = "the donor's"    # how ak47_build's shared model/icon steps name the donor and the weapon in messages
WEAPON_NAME = "the Dragonbreath"
POWER_TWIN = 0x2C76A0C2      # an Attack parameter (name unknown) that holds the same number as Power in the donors


# The Royal Guard's Sword keeps its UVs as 16_16 unorm (0x1201): ak47_build's model writer handles that format
# itself (ENCODERS), so any mod built on such a donor works. Our UVs are all inside 0..1, which is what unorm can hold.


class DragonError(Exception):
    """Something the player has to fix; the message says what."""


def _log(log, msg: str) -> None:
    if log:
        log(msg)


# ------------------------------------------------------------------------------------------------ the recipe

@dataclass
class Recipe:
    manifest: dict                 # mod.toml's [mod]: the main switch
    toml_text: str
    item_manifest: dict            # item.toml's [mod]
    item_toml_text: str
    actor: str
    donor: str
    stats: dict
    tags: dict
    texts: dict
    blade: dict
    spot: dict
    folder: str

    def shape(self, hilt: float | None = None, top: float | None = None) -> dm.Shape:
        b = self.blade
        return dm.Shape(hilt=b["preview_hilt"] if hilt is None else hilt, top=b["preview_top"] if top is None else top,
                        base_width=b["base_width"], tip_width=b["tip_width"], thickness=b["thickness"],
                        sori=b["sori"], tip_len=b["tip_len"], guard_r=b["guard_r"], edge_side=int(b["edge_side"]))


def _num(v, what: str, lo: float, hi: float) -> float:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or not lo <= v <= hi:
        raise DragonError(f"assets/dragonbreath/{RECIPE_JSON}: {what} must be a number from {lo:g} to {hi:g}")
    return float(v)


def _actor_name(v, what: str) -> str:
    if not isinstance(v, str) or not v.replace("_", "").isalnum() or not 0 < len(v) <= 48:
        raise DragonError(f"assets/dragonbreath/{RECIPE_JSON}: {what} isn't an actor name")
    return v


def load_recipe(folder: str = RECIPE) -> Recipe:
    """assets/dragonbreath: mod.toml (the main switch), item.toml (the item) and dragonbreath.json, checked."""
    try:
        with open(os.path.join(folder, "mod.toml"), encoding="utf-8") as f:
            toml_text = f.read()
        with open(os.path.join(folder, ITEM_TOML), encoding="utf-8") as f:
            item_text = f.read()
        with open(os.path.join(folder, RECIPE_JSON), encoding="utf-8") as f:
            r = json.load(f)
        man, item = tomllib.loads(toml_text).get("mod") or {}, tomllib.loads(item_text).get("mod") or {}
    except (OSError, ValueError) as e:
        raise DragonError(f"The Dragonbreath recipe in {folder} can't be read ({e}).") from None
    for name, m in (("mod.toml", man), (ITEM_TOML, item)):
        if not isinstance(m.get("id"), str) or not isinstance(m.get("version"), str):
            raise DragonError(f"assets/dragonbreath/{name} has no [mod] id and version")
        if m.get("part_of"):
            raise DragonError(f"assets/dragonbreath/{name} has a part_of: neither mod may have one (the switch is the "
                              "main mod; the item stays on so saves that hold it load)")
    if man["id"] == item["id"]:
        raise DragonError("assets/dragonbreath: mod.toml and item.toml have the same id")
    if item.get("secret") is not True:
        raise DragonError(f"assets/dragonbreath/{ITEM_TOML} must be secret (the item data is never listed)")
    if not isinstance(r, dict) or r.get("format") != 1:
        raise DragonError(f"assets/dragonbreath/{RECIPE_JSON} isn't format 1")
    actor, donor = _actor_name(r.get("actor"), "actor"), _actor_name(r.get("donor"), "donor")
    if not actor.startswith("Weapon_Sword_") or not donor.startswith("Weapon_Sword_"):
        raise DragonError(f"assets/dragonbreath/{RECIPE_JSON}: actor and donor must be one-handed swords "
                          "(Weapon_Sword_)")
    st = dict(r.get("stats") or {})
    st["attack"] = int(_num(st.get("attack"), "stats attack", 1, 999))
    st["life"] = int(_num(st.get("life"), "stats life", 1, 100000))
    st["guardian_ratio"] = _num(st.get("guardian_ratio"), "stats guardian_ratio", 0.0, 10.0)
    for k in ("life_infinite", "throwable"):
        if not isinstance(st.get(k), bool):
            raise DragonError(f"assets/dragonbreath/{RECIPE_JSON}: stats {k} must be true or false")
    if not isinstance(st.get("subtype"), str) or not 0 < len(st["subtype"].encode()) < 32 \
            or not st["subtype"].isalnum():
        raise DragonError(f"assets/dragonbreath/{RECIPE_JSON}: stats subtype must be a short word")
    _actor_name(st.get("guardians_from"), "stats guardians_from")
    lo = st.get("guardians_leave_out") or []
    if not isinstance(lo, list) or not all(isinstance(x, str) and x for x in lo):
        raise DragonError(f"assets/dragonbreath/{RECIPE_JSON}: stats guardians_leave_out isn't a list of names")
    tags = r.get("tags") or {}
    for k in ("drop", "add"):
        if not isinstance(tags.get(k), list) or not all(isinstance(t, str) and t.isalnum() for t in tags[k]):
            raise DragonError(f"assets/dragonbreath/{RECIPE_JSON}: tags {k} isn't a list of tag names")
    if set(tags["drop"]) & set(tags["add"]):
        raise DragonError(f"assets/dragonbreath/{RECIPE_JSON}: a tag is both dropped and added")
    texts = r.get("texts") or {}
    for k in ("name", "desc"):
        if not isinstance(texts.get(k), str) or not texts[k].strip():
            raise DragonError(f"assets/dragonbreath/{RECIPE_JSON}: texts {k} is missing")
    lines = texts["desc"].split("\n")
    if len(lines) > MAX_TEXT_LINES or any(len(x) > MAX_TEXT_LINE for x in lines):
        raise DragonError(f"assets/dragonbreath/{RECIPE_JSON}: the description must fit {MAX_TEXT_LINES} lines of "
                          f"{MAX_TEXT_LINE} characters")
    b = dict(r.get("blade") or {})
    for k, lo_, hi_ in (("base_width", 0.008, 0.06), ("tip_width", 0.005, 0.06), ("thickness", 0.002, 0.02),
                        ("sori", 0.0, 0.06), ("tip_len", 0.01, 0.15), ("guard_r", 0.015, 0.08),
                        ("preview_hilt", -0.5, -0.05), ("preview_top", 0.3, 2.0)):
        b[k] = _num(b.get(k), f"blade {k}", lo_, hi_)
    if b.get("edge_side") not in (1, -1):
        raise DragonError(f"assets/dragonbreath/{RECIPE_JSON}: blade edge_side must be 1 or -1")
    spot = dict(r.get("test_spot") or {})
    if not isinstance(spot.get("unit"), str) or not bm.UNIT_RE.fullmatch(spot["unit"]):
        raise DragonError(f"assets/dragonbreath/{RECIPE_JSON}: test_spot unit isn't a map unit like D-6_Dynamic")
    for k in ("x", "z", "ground", "lift", "yaw"):
        spot[k] = _num(spot.get(k), f"test_spot {k}", -10000, 10000)
    want = ak._unit_of(spot["x"], spot["z"])
    if spot["unit"].rsplit("_", 1)[0] != want:
        raise DragonError(f"assets/dragonbreath/{RECIPE_JSON}: the test spot is in map section {want}, not "
                          f"{spot['unit']}")
    return Recipe(manifest=man, toml_text=toml_text, item_manifest=item, item_toml_text=item_text, actor=actor,
                  donor=donor, stats=st, tags=tags, texts=texts, blade=b, spot=spot, folder=folder)


# ------------------------------------------------------------------------------------------------ the game

def find_game(install: str | None, lib, donor: str) -> ak.Game:
    install = install or (lib.install if lib else None) or mm.default_install()
    game = ak.Game(os.path.abspath(install), donor)
    if not os.path.isdir(game.romfs):
        raise DragonError(f"Savage Wind's game files aren't in {game.romfs}. Set them up in Savage Wind first, or "
                          "pass --game <Savage Wind folder>.")
    gone = game.missing()
    if gone:
        raise DragonError("Your game files lack what Dragonbreath is built from: " + ", ".join(gone))
    return game


def pack_of(game: ak.Game, actor: str) -> dict[str, tuple[bytes, int]]:
    """Another actor's pack (name -> (bytes, alignment)), read only."""
    rel = f"Actor/Pack/{actor}.sbactorpack"
    try:
        return dict(bnp.Blob(game.read(rel)).open().items())
    except ak.AkError as e:
        raise DragonError(str(e)) from None
    except (wc.FormatError, struct.error, IndexError) as e:
        raise DragonError(f"{rel} can't be read ({e})") from None


def link_of(files: dict, actor: str) -> aamp.PList:
    name = f"Actor/ActorLink/{actor}.bxml"
    if name not in files:
        raise DragonError(f"{actor}'s pack has no {name}")
    return aamp.load(files[name][0])


def gparam_name(files: dict, actor: str) -> str:
    target = link_of(files, actor).object("LinkTarget")
    user = target.get_value("GParamUser") if target is not None else None
    name = f"Actor/GeneralParamList/{user}.bgparamlist"
    if not user or name not in files:
        raise DragonError(f"{actor}'s pack has no stats file ({name})")
    return name


def _left_out(recipe: Recipe, actor: str) -> bool:
    return any(actor.startswith(x) for x in recipe.stats["guardians_leave_out"])


def tag_carriers(ai: dict[str, dict], tag: str) -> list[str]:
    """The actors whose actor info carries `tag` (its "tags" map holds the tag's crc32)."""
    h = zlib.crc32(tag.encode())
    out = []
    for name, e in ai.items():
        tags = e.get("tags") if isinstance(e, dict) else None
        if isinstance(tags, dict) and any(isinstance(v, int) and int(v) & 0xFFFFFFFF == h for v in tags.values()):
            out.append(name)
    return sorted(out)


def guardian_lists(game: ak.Game, recipe: Recipe) -> tuple[str, str]:
    """(SpHitActor, SpHitTag): the ancient weapon's own lists (stats guardians_from), without the names that start
    with guardians_leave_out (the plan: Ganon and the Blights don't count as Guardians). A tag that a left-out actor
    carries in your actor info is dropped too (IsEffectiveAncientWeapon: the castle's Blight refights
    Enemy_SiteBoss_*_R carry it), and the other actors that carry it (the Guardian Scouts' DetachLineBeam ones) are
    named in SpHitActor instead, so they stay weak to the blade."""
    src = recipe.stats["guardians_from"]
    files = pack_of(game, src)
    root = aamp.load(files[gparam_name(files, src)][0])
    at = root.object("Attack")
    actors = at.get_value("SpHitActor") if at is not None else None
    tag = at.get_value("SpHitTag") if at is not None else None
    if not isinstance(actors, str) or not actors or not isinstance(tag, str) or not tag:
        raise DragonError(f"{src}'s stats have no Guardian lists (Attack SpHitActor / SpHitTag)")
    keep = [a for a in actors.split(",") if a and not _left_out(recipe, a)]
    ai = game.actorinfo()
    tags = []
    for t in (x for x in tag.split(",") if x):
        who = tag_carriers(ai, t)
        if any(_left_out(recipe, a) for a in who):
            keep += [a for a in who if not _left_out(recipe, a) and a not in keep]
        else:
            tags.append(t)
    if not keep:
        raise DragonError(f"no Guardian is left in {src}'s list")
    return ",".join(keep), ",".join(tags)


def left_out_hit(game: ak.Game, recipe: Recipe, sp_actor: str, sp_tag: str) -> list[str]:
    """The left-out actors (Ganon, the Blights) the Guardian lists would still weaken: named, or carrying a tag."""
    ai = game.actorinfo()
    hit = {a for a in sp_actor.split(",") if a and _left_out(recipe, a)}
    for t in (x for x in sp_tag.split(",") if x):
        hit.update(a for a in tag_carriers(ai, t) if _left_out(recipe, a))
    return sorted(hit)


# ------------------------------------------------------------------------------------------------ the stats file

def _set(obj: aamp.PObject | None, name, value, what: str) -> None:
    key = aamp.name_hash(name)
    if obj is None or key not in obj:
        raise DragonError(f"the donor's stats file has no {what}")
    old = obj[key]
    obj[key] = aamp.Param(old.type, type(old.value)(value) if isinstance(old.value, (bool, int, float, str))
                          and not isinstance(value, type(old.value)) else value)


def stats_file(data: bytes, recipe: Recipe, sp_actor: str, sp_tag: str) -> bytes:
    """Our stats file from the donor's: attack, life (and never breaking), not throwable, no random "+" bonuses,
    the sub-type and the Guardian weakness. Every other value stays the donor's."""
    st = recipe.stats

    def edit(root: aamp.PList) -> None:
        gen, att, com, ss = (root.object(n) for n in ("General", "Attack", "WeaponCommon", "SmallSword"))
        _set(gen, "Life", st["life"], "General Life")
        _set(gen, "IsLifeInfinite", st["life_infinite"], "General IsLifeInfinite")
        old_power = att.get_value("Power") if att is not None else None
        _set(att, "Power", st["attack"], "Attack Power")
        if att is not None and POWER_TWIN in att and att[POWER_TWIN].value == old_power:
            att[POWER_TWIN] = aamp.Param(att[POWER_TWIN].type, st["attack"])
        _set(att, "SpHitActor", sp_actor, "Attack SpHitActor")
        _set(att, "SpHitTag", sp_tag, "Attack SpHitTag")
        _set(att, "SpHitRatio", float(st["guardian_ratio"]), "Attack SpHitRatio")
        _set(com, "IsThrowingWeapon", st["throwable"], "WeaponCommon IsThrowingWeapon")
        _set(com, "SharpWeaponPer", 0.0, "WeaponCommon SharpWeaponPer")
        _set(com, "SharpWeaponAddCrit", False, "WeaponCommon SharpWeaponAddCrit")
        for k in SHARP_KEYS:
            _set(com, k, 0, f"WeaponCommon {k}")
        _set(ss, "WeaponSubType", st["subtype"], "SmallSword WeaponSubType")

    try:
        return ak._aamp_edit(data, edit)
    except aamp.AampError as e:
        raise DragonError(f"the donor's stats file can't be read ({e})") from None


def read_stats(data: bytes) -> dict:
    """The values our tests and `check` look at, read back from a stats file."""
    root = aamp.load(data)
    o = {n: root.object(n) or aamp.PObject() for n in ("General", "Attack", "WeaponCommon", "SmallSword")}
    out = {"life": o["General"].get_value("Life"), "life_infinite": o["General"].get_value("IsLifeInfinite"),
           "attack": o["Attack"].get_value("Power"), "power_twin": o["Attack"].get(POWER_TWIN),
           "sp_actor": o["Attack"].get_value("SpHitActor"), "sp_tag": o["Attack"].get_value("SpHitTag"),
           "sp_ratio": o["Attack"].get_value("SpHitRatio"),
           "throwable": o["WeaponCommon"].get_value("IsThrowingWeapon"),
           "sharp_per": o["WeaponCommon"].get_value("SharpWeaponPer"),
           "sharp": {k: o["WeaponCommon"].get_value(k) for k in SHARP_KEYS},
           "subtype": o["SmallSword"].get_value("WeaponSubType"), "pod": o["SmallSword"].get_value("PodName")}
    if out["power_twin"] is not None:
        out["power_twin"] = out["power_twin"].value
    return out


# ------------------------------------------------------------------------------------------------ the actor pack

def actor_pack(files: dict[str, tuple[bytes, int]], recipe: Recipe, stats: bytes, yaz0_align: int = 0,
               model: str | None = None) -> ak.Pack:
    """Our pack from the donor's: actor link (ModelUser, GParamUser, name, tags) and model list renamed, our stats
    file in place of the donor's (which is left out, so no two packs hold one path with different contents). Every
    other file stays as it is under its own name. model: the model the model list names (its Folder and UnitName;
    default the actor's own): the tempered levels share the base blade's. The link's ModelUser stays the actor's
    own name (its own model list file)."""
    actor, donor = recipe.actor, recipe.donor
    model = model or actor
    link_name = f"Actor/ActorLink/{donor}.bxml"
    link = link_of(files, donor)
    target = link.object("LinkTarget")
    if target is None:
        raise DragonError(f"{donor}'s actor link has no LinkTarget")
    ml_name = f"Actor/ModelList/{target.get_value('ModelUser')}.bmodellist"
    if ml_name not in files:
        raise DragonError(f"{donor}'s pack has no {ml_name}")
    gp_name = gparam_name(files, donor)
    kept: list[str] = []

    def relink(root: aamp.PList) -> None:
        t = root.object("LinkTarget")
        ak._set_text(t, "ModelUser", actor)
        ak._set_text(t, "GParamUser", actor)
        ak._set_text(t, "ActorNameJpn", recipe.texts["name"])
        tags = root.object("Tags")
        if tags is None:
            raise DragonError(f"{donor}'s actor link has no Tags")
        names = [p.value for p in tags.values() if isinstance(p.value, str)]
        keep = [x for x in names if x not in recipe.tags["drop"]]
        keep += [x for x in recipe.tags["add"] if x not in keep]
        tags.clear()
        for i, x in enumerate(keep):
            tags[aamp.name_hash(f"Tag{i}")] = aamp.Param(aamp.STRING_REF, x)
        kept.extend(keep)

    def remodel(root: aamp.PList) -> None:
        md = root.list("ModelData")
        n = 0
        for lst in (md.lists.values() if md else []):
            base = lst.object("Base")
            if base is not None and aamp.name_hash("Folder") in base:
                ak._set_text(base, "Folder", model)
                n += 1
            units = lst.list("Unit")
            for u in (units.objects.values() if units else []):
                if aamp.name_hash("UnitName") in u:
                    ak._set_text(u, "UnitName", model)
        if not n:
            raise DragonError(f"{ml_name} names no model folder")

    ours = {f"Actor/ActorLink/{actor}.bxml": ak._aamp_edit(files[link_name][0], relink),
            f"Actor/ModelList/{actor}.bmodellist": ak._aamp_edit(files[ml_name][0], remodel),
            f"Actor/GeneralParamList/{actor}.bgparamlist": stats}
    refs = {f"Actor/ActorLink/{actor}.bxml": link_name, f"Actor/ModelList/{actor}.bmodellist": ml_name,
            f"Actor/GeneralParamList/{actor}.bgparamlist": gp_name}
    items = [(n, d, a) for n, (d, a) in files.items() if n not in (link_name, ml_name, gp_name)]
    for name, data in ours.items():
        items.append((name, data, max(files[refs[name]][1], ukmm.new_alignment(name, data, 4))))
    raw = bnp.sarc_build(items)
    return ak.Pack(ukmm.yaz0(raw, yaz0_align), len(raw), ours, refs, kept)


def actorinfo_entry(src: dict, recipe: Recipe, lo: tuple, hi: tuple, tags: list[str], used_sort: set[int]) -> dict:
    """The donor's actor info, renamed (ak47_build's), with our stats repeated (attack, life, no "+" bonuses) and no
    Hyrule Compendium spots."""
    e = ak.actorinfo_entry(src, recipe.actor, lo, hi, tags, used_sort)
    st = recipe.stats
    if "attackPower" in e:
        e["attackPower"] = st["attack"]
    if "generalLife" in e:
        e["generalLife"] = st["life"]
    for k in SHARP_KEYS:
        key = "weaponCommon" + k
        if key in e:
            e[key] = 0
    if "weaponCommonSharpWeaponPer" in e:
        e["weaponCommonSharpWeaponPer"] = bd.f32(0.0)
    if "weaponCommonSharpWeaponAddCrit" in e:
        e["weaponCommonSharpWeaponAddCrit"] = False
    for k in ("pictureBookLiveSpot1", "pictureBookLiveSpot2", "pictureBookSpecialDrop"):
        if k in e:
            e[k] = -1
    e.pop("homeArea", None)
    return e


# ------------------------------------------------------------------------------------------------ textures

def _rg_levels(rows: list, count: int) -> list[tuple[bytes, int, int]]:
    h, w = len(rows), len(rows[0])
    px = bytearray(w * h * 4)
    for y, row in enumerate(rows):
        for x, (r, g) in enumerate(row):
            px[(y * w + x) * 4:(y * w + x) * 4 + 4] = bytes((r, g, 0, 255))
    return ak.mip_levels(bytes(px), w, h, count)


def _encode_rg(px: bytes, w: int, h: int, fmt: int, what: str) -> bytes:
    kind = ak._block_kind(fmt)
    if kind == "bc5":
        return ak.bc5_encode(w, h, lambda x, y: (px[(y * w + x) * 4], px[(y * w + x) * 4 + 1]))
    if kind == "bc1":
        rgba = bytearray(px)
        rgba[2::4] = bytes([255]) * (w * h)
        return menu_bg.encode_bc(bytes(rgba), w, h, False)
    raise DragonError(f"the donor's {what} texture is {formats.format_name(fmt)}, which this tool can't write")


def make_textures(shape: dm.Shape, donor_ts, donor_names: dict, actor: str, log=None) -> tuple:
    """Our painted textures in the formats, sizes and mip counts of the donor's: (TextureSet, {sampler: name}, the
    colour picture). The donor's material must have colour (_a0), normal (_n0) and shine (_s0) samplers only."""
    roles = {"_a0": "Alb", "_n0": "Nrm", "_s0": "Spm"}
    if set(donor_names) != set(roles):
        raise DragonError(f"the donor's material has samplers {sorted(donor_names)}: this tool fills only "
                          f"{sorted(roles)} (pick a donor without glow masks)")
    by_name = {t.name: t for t in donor_ts.textures}
    d = {r: by_name.get(donor_names[s]) for s, r in roles.items()}
    for r, t in d.items():
        if t is None:
            raise DragonError(f"the donor's textures have no {r} picture")
        if t.dim != "2d" or t.depth != 1 or t.array != 1:
            raise DragonError(f"the donor's {t.name} isn't a flat 2D texture")
    _log(log, "Painting the blade's textures (about 10 s)...")
    painted = dm.paint_textures(d["Alb"].width, d["Alb"].height, d["Nrm"].width, d["Nrm"].height,
                                d["Spm"].width, d["Spm"].height, shape)
    out, names = [], {}
    for sampler, role in roles.items():
        t0 = d[role]
        w, h, count = t0.width, t0.height, len(t0.levels)
        if role == "Alb":
            levels = [ak._encode_color(px, lw, lh, t0.format) for px, lw, lh in
                      ak.mip_levels(painted.albedo.px, w, h, count)]
        elif role == "Nrm":
            levels = [_encode_rg(px, lw, lh, t0.format, "normal") for px, lw, lh in _rg_levels(painted.normal, count)]
        else:
            ar, ag = ak._spm_average(t0)
            rows = [[(min(255, round(ar * 1.25 * k)), ag) for k in row] for row in painted.shine]
            levels = [_encode_rg(px, lw, lh, t0.format, "shine") for px, lw, lh in _rg_levels(rows, count)]
        for k, lv in enumerate(levels):
            want = formats.level_size(t0.format, w, h, k)
            if len(lv) != want:
                raise DragonError(f"internal: {actor}_{role} level {k} is {len(lv)} bytes, not {want}")
        t = copy.deepcopy(t0)
        t.name, t.levels = f"{actor}_{role}", levels
        out.append(t)
        names[sampler] = t.name
    ts = copy.deepcopy(donor_ts)
    ts.name, ts.textures = actor, out
    return ts, names, painted.albedo


# ------------------------------------------------------------------------------------------------ logs

def _hid(key: str, used: set[int]) -> bd.U32:
    """A HashId for our object: from its name, so a rebuild gives the same one; never one in use."""
    h = zlib.crc32(f"giruka.dragonbreath/{key}".encode())
    while h in used or h == 0:
        h = (h + 1) & 0xFFFFFFFF
    used.add(h)
    return bd.U32(h)


def placed_object(actor: str, spot: dict, ground: float, used: set[int]) -> dict:
    hid = _hid("test-spot", set(used))
    pos = [bm.f32(spot["x"]), bm.f32(ground + spot["lift"]), bm.f32(spot["z"])]
    yaw = bm.f32(math.radians(spot["yaw"]))
    return {"!Parameters": dict(ak.PLACED_PARAMS), "HashId": hid, "Rotate": [bm.f32(0.0), yaw, bm.f32(0.0)],
            "SRTHash": ak._signed(zlib.crc32(f"{actor}:{pos}:{yaw}".encode())), "Translate": pos,
            "UnitConfigName": actor}


def texts_log(actor: str, texts: dict) -> dict:
    """BCML's texts.json: the name and description (English; the other languages get them too)."""
    desc = texts["desc"]
    return {"USen": {f"{MSG_FILE}.msyt": {
        f"{actor}_Name": {"contents": [{"text": texts["name"]}]},
        f"{actor}_Desc": {"contents": [{"text": desc}]},
        f"{actor}_PictureBook": {"contents": [{"text": texts.get("picture_book", desc)}]}}}}


def message_labels(game: ak.Game) -> set[str] | None:
    try:
        files = bnp.Blob(game.read(ak.MESSAGES)).child("Message/Msg_USen.product.ssarc").open()
        return set(bt.msbt_read(files[f"{MSG_FILE}.msbt"][0]).labels)
    except (ak.AkError, AttributeError, KeyError, wc.FormatError, bt.TextError, struct.error, ValueError):
        return None


def check_logs(logs: dict[str, str]) -> None:
    try:
        ak.check_logs(logs, weapon="Dragonbreath's")
    except ak.AkError as e:
        raise DragonError(str(e)) from None


# ------------------------------------------------------------------------------------------------ building

@dataclass
class Built:
    folder: str                        # the item mod's folder
    main_folder: str = ""              # the main switch's folder (the test spot)
    spot: tuple = ()
    ground: float = 0.0
    vertices: int = 0
    triangles: int = 0
    hash_id: int = 0
    notes: list[str] = field(default_factory=list)
    levels: list[str] = field(default_factory=list)   # the tempered levels' actors (stage 2's recipe)
    stage2: object = None                              # dragonbreath_stage2.Built2, when stage 2 was built


def make_mesh(game: ak.Game, recipe: Recipe):
    """(our mesh in the donor's space, the donor's model bytes, the shape used). The shape's hilt and tip are the
    donor's lowest and highest points, so Link's hands and the attack reach stay where the donor's are."""
    donor_raw = ak._unyaz0(game.raw("model"))
    try:
        res = read_model(donor_raw)
    except ModelError as e:
        raise DragonError(f"the donor's model can't be read ({e})") from None
    hilt = ak.donor_hilt(res)[0]
    top = hilt + ak.donor_length(res)
    shape = recipe.shape(hilt, top)
    try:
        return dm.make_mesh(shape), donor_raw, shape
    except ValueError as e:
        raise DragonError(str(e)) from None


def load_levels(recipe: Recipe, stage2_dir: str = s2.STAGE2):
    """(stage 2's recipe, stage 1 Recipes for the tempered levels Weapon_Sword_Dragonbreath_1..4)."""
    try:
        st2 = s2.load_stage2(stage2_dir)
    except s2.Stage2Error as e:
        raise DragonError(str(e)) from None
    if st2.base_actor != recipe.actor:
        raise DragonError(f"assets/dragonbreath/stage2/{s2.RECIPE_JSON}: base_actor is {st2.base_actor}, not "
                          f"{recipe.actor}")
    if st2.levels[0]["attack"] != recipe.stats["attack"]:
        raise DragonError(f"assets/dragonbreath/stage2/{s2.RECIPE_JSON}: level 0's attack isn't the blade's "
                          f"({recipe.stats['attack']})")
    return st2, s2.level_recipes(st2, recipe)


def build_package(game: ak.Game, recipe: Recipe, out: str, *, log=None, used: set[int] | None = None,
                  test_spot: bool = False, stage2: bool = True, events: bool = True,
                  stage2_dir: str = s2.STAGE2) -> Built:
    """Write Dragonbreath's two mods (BCML-style package folders) into `out` (a new or empty folder). used: more
    HashIds to keep clear of (other library mods' map changes). Reads only the player's game files.
    test_spot: also place the developer's test blade near the test save's start (off for players: picking it up
    would move the quest on). stage2: Rohan's quest, the cave, the chest and the first draw
    (tools/dragonbreath_stage2.py); events=False: the cave and chest only (no quest, talk or scene). The tempered
    levels (actor packs, actor info, icons) are always built: they are item data, so a save that holds one loads."""
    if os.path.isdir(out) and os.listdir(out):
        raise DragonError(f"{out} isn't empty")
    actor, donor = recipe.actor, recipe.donor
    ai = game.actorinfo()
    if donor not in ai:
        raise DragonError(f"Your game's actor info has no {donor}")
    if actor in ai:
        raise DragonError(f"Your game already has an actor called {actor}")
    st2, levels = load_levels(recipe, stage2_dir)
    for lr in levels:
        if lr.actor in ai:
            raise DragonError(f"Your game already has an actor called {lr.actor}")
    item_dir = os.path.join(out, recipe.item_manifest["id"])
    main_dir = os.path.join(out, recipe.manifest["id"])
    romfs = os.path.join(item_dir, BASE_TID, "romfs")
    res = Built(item_dir, main_dir)
    rstb = game.rstb()
    if rstb is None:
        res.notes.append("the game's size table isn't there: no size entries for the new files")
    rstb_json: dict[str, int] = {}

    def size(name, ref, ref_len, new_len, pack):
        v = ak._estimate(rstb, ref, ref_len, new_len, pack, res.notes, name)
        if v:
            rstb_json[name] = v

    # the model and its textures
    _log(log, "Making the blade's model...")
    m, donor_raw, shape = make_mesh(game, recipe)
    res.vertices, res.triangles = len(m.pos), len(m.idx) // 3
    tex_raw = ak._unyaz0(game.raw("tex"))
    try:
        donor_ts = nx_bfres.read_textures(tex_raw)
    except ModelError as e:
        raise DragonError(f"the donor's textures can't be read ({e})") from None
    ts, tex_names, alb = make_textures(shape, donor_ts, ak.donor_sampler_textures(donor_raw), actor, log)
    _log(log, f"Writing the model ({res.vertices} vertices, {res.triangles} triangles)...")
    try:
        model_bytes, _ = ak.build_model(donor_raw, m, actor, tex_names, donor=DONOR_NAME, weapon=WEAPON_NAME)
    except ak.AkError as e:
        raise DragonError(str(e)) from None
    tex_bytes = nx_bfres.write_textures(ts)
    problems = checks.switch_tex_problems(tex_bytes)
    if problems:
        raise DragonError(f"the Dragonbreath textures fail their self-check: {'; '.join(map(str, problems[:3]))}")
    ak._write(os.path.join(romfs, "Model", f"{actor}.sbfres"),
              ukmm.yaz0(model_bytes, ak._yaz0_align(game.raw("model"))))
    ak._write(os.path.join(romfs, "Model", f"{actor}.Tex.sbfres"),
              ukmm.yaz0(tex_bytes, ak._yaz0_align(game.raw("tex"))))
    size(f"Model/{actor}.bfres", f"Model/{donor}.bfres", len(donor_raw), len(model_bytes), False)
    size(f"Model/{actor}.Tex.bfres", f"Model/{donor}.Tex.bfres", len(tex_raw), len(tex_bytes), False)

    # the icon
    _log(log, "Drawing the inventory icon...")
    icon_raw = ak._unyaz0(game.raw("icon"))
    try:
        icon_bytes, _pic = ak.build_icon(icon_raw, m, alb, actor, donor=DONOR_NAME)
    except ak.AkError as e:
        raise DragonError(str(e)) from None
    ak._write(os.path.join(romfs, "UI", "StockItem", f"{actor}.sbitemico"),
              ukmm.yaz0(icon_bytes, ak._yaz0_align(game.raw("icon"))))
    size(f"UI/StockItem/{actor}.bitemico", f"UI/StockItem/{donor}.bitemico", len(icon_raw), len(icon_bytes), False)

    # the stats file and the actor pack
    _log(log, f"Building {actor}'s actor pack from {donor}'s...")
    files = game.pack()
    sp_actor, sp_tag = guardian_lists(game, recipe)
    stats = stats_file(files[gparam_name(files, donor)][0], recipe, sp_actor, sp_tag)
    pk = actor_pack(files, recipe, stats, ak._yaz0_align(game.raw("pack")))
    ak._write(os.path.join(romfs, "Actor", "Pack", f"{actor}.sbactorpack"), pk.data)
    size(f"Actor/Pack/{actor}.bactorpack", f"Actor/Pack/{donor}.bactorpack", ukmm.unpacked_size(game.raw("pack")),
         pk.raw_len, True)
    for name, data in pk.inner.items():
        size(name, pk.refs[name], len(files[pk.refs[name]][0]), len(data), False)

    # actor info
    lo, hi = m.bounds()
    used_sort = {a.get("sortKey") for a in ai.values() if isinstance(a.get("sortKey"), int)}
    entry = actorinfo_entry(ai[donor], recipe, lo, hi, pk.tags, used_sort)
    used_sort.add(entry.get("sortKey"))
    ai_log = {str(zlib.crc32(actor.encode())): entry}

    # the tempered levels (stage 2's recipe): the base blade's pack with their own stats, one model for all five
    # (the model list names the base blade's), their own actor info and icon. Their IsGet flags and names are in
    # stage 2's item logs.
    if levels:
        _log(log, f"Building the {len(levels)} tempered levels ({levels[0].actor}..)...")
    pack_len = ukmm.unpacked_size(game.raw("pack"))
    for lr in levels:
        lpk = actor_pack(files, lr, stats_file(files[gparam_name(files, donor)][0], lr, sp_actor, sp_tag),
                         ak._yaz0_align(game.raw("pack")), model=actor)
        ak._write(os.path.join(romfs, "Actor", "Pack", f"{lr.actor}.sbactorpack"), lpk.data)
        size(f"Actor/Pack/{lr.actor}.bactorpack", f"Actor/Pack/{donor}.bactorpack", pack_len, lpk.raw_len, True)
        for name, data in lpk.inner.items():
            size(name, lpk.refs[name], len(files[lpk.refs[name]][0]), len(data), False)
        le = actorinfo_entry(ai[donor], lr, lo, hi, lpk.tags, used_sort)
        le["bfres"] = le["mainModel"] = actor
        used_sort.add(le.get("sortKey"))
        ai_log[str(zlib.crc32(lr.actor.encode()))] = le
        try:
            licon, _ = ak.build_icon(icon_raw, m, alb, lr.actor, donor=DONOR_NAME)
        except ak.AkError as e:
            raise DragonError(str(e)) from None
        ak._write(os.path.join(romfs, "UI", "StockItem", f"{lr.actor}.sbitemico"),
                  ukmm.yaz0(licon, ak._yaz0_align(game.raw("icon"))))
        size(f"UI/StockItem/{lr.actor}.bitemico", f"UI/StockItem/{donor}.bitemico", len(icon_raw), len(licon), False)
        res.levels.append(lr.actor)
    logs = {"actorinfo.yml": bd.to_text(ai_log)}

    # the test spot (developers only: the main switch)
    spot = recipe.spot
    taken = set(game.hash_ids(log)) | set(used or ())
    main_logs = {}
    area = 0.0
    if test_spot:
        _log(log, f"Reading your terrain at the test spot (x={spot['x']:.0f}, z={spot['z']:.0f})...")
        try:
            ground, area = game.terrain().height(spot["x"], spot["z"])
        except ak.AkError as e:
            raise DragonError(str(e)) from None
        if abs(ground - spot["ground"]) > GROUND_TOLERANCE:
            raise DragonError(f"your terrain at the test spot is {ground:.1f} m high, not about {spot['ground']:.1f} m "
                              "as the recipe says")
        spread = ak.ground_spread(game.terrain(), spot["x"], spot["z"])
        if spread > MAX_SLOPE_SPREAD:
            res.notes.append(f"the ground at the test spot rises {spread:.2f} m within 1.2 m: the blade may slide")
        obj = placed_object(actor, spot, ground, taken)
        res.hash_id, res.ground = int(obj["HashId"]), ground
        res.spot = tuple(float(v) for v in obj["Translate"])
        taken.add(res.hash_id)
        main_logs["map.yml"] = bd.to_text({spot["unit"]: {"Objs": {"add": [obj]}}})

    # flags (the test spot's pick-up flag too, when there is one: in the item mod, so a save keeps it), texts, sizes
    gd, sd = ak.flags_logs(actor, donor, res.hash_id, game.flags())
    if not test_spot:
        placed = f"MainField_{actor}_{res.hash_id}"
        gd["bool_data"]["add"].pop(placed, None)
        sd["add"] = [e for e in sd["add"] if e["DataName"] != placed]
    logs["gamedata.yml"] = bd.to_text(gd)
    logs["savedata.yml"] = bd.to_text(sd)
    logs["texts.json"] = json.dumps(texts_log(actor, recipe.texts), indent=1, ensure_ascii=False) + "\n"
    logs["rstb.json"] = json.dumps(dict(sorted(rstb_json.items())), indent=1) + "\n"
    check_logs(logs)
    check_logs(main_logs)
    head = "# Dragonbreath: built on this PC by tools/dragonbreath_build.py from the player's own game files."
    ak._package_files(item_dir, recipe.item_toml_text, recipe.item_manifest, logs, [
        head, "", f"- actor: {actor} (from {donor})", f"- model: {res.vertices} vertices, {res.triangles} triangles",
        f"- stats: attack {recipe.stats['attack']}, life {recipe.stats['life']}"
        f"{' (never breaks)' if recipe.stats['life_infinite'] else ''}, Guardians x{recipe.stats['guardian_ratio']:g}",
        f"- tempered levels: {', '.join(res.levels) or 'none'} (the base blade's model)",
        f"- the map (cave, quest{', test spot' if test_spot else ''}): the mod {recipe.manifest['id']}"])
    ak._package_files(main_dir, recipe.toml_text, recipe.manifest, main_logs, [
        head, "", (f"- test spot (developer build) in {spot['unit']} at x={res.spot[0]:.2f} y={res.spot[1]:.2f} "
                   f"z={res.spot[2]:.2f} (ground {res.ground:.2f} m, terrain area {area:.1f} m), HashId {res.hash_id}"
                   if test_spot else "- no test spot (a developer build adds one: build --test-spot)"),
        f"- stage 2: {'built (see logs/)' if stage2 else 'not built'}"])

    # stage 2: Rohan's quest, the diamond cave, the chest, Daruk's first draw (docs/dragonbreath.md, stage 2). It
    # reads stage 1's logs back and adds its entries.
    if stage2:
        try:
            r2 = s2.build_stage2(game, st2, item_dir, main_dir, used=taken, log=log, events=events)
        except s2.Stage2Error as e:
            raise DragonError(str(e)) from None
        res.notes += r2.notes
        res.stage2 = r2
    return res


def other_hash_ids(lib, recipe: Recipe) -> dict[int, str]:
    ours = {recipe.manifest["id"], recipe.item_manifest["id"]}
    return ak.map_hash_ids(lib, [m for m in lib.mods if m["id"] not in ours]) if lib is not None else {}


def add_to_library(lib, folder: str, mod_id: str, log=None, new_on: bool = True) -> tuple[dict, list[str]]:
    """modman's normal add (ak47_build's: an old copy is replaced, its on/off and place kept). A new mod is on when
    new_on, else off."""
    try:
        return ak.add_to_library(lib, folder, mod_id, log, new_on=new_on)
    except ak.AkError as e:
        raise DragonError(str(e)) from None


def build(library: str | None = None, *, install: str | None = None, out: str | None = None, add: bool = True,
          on: bool = False, recipe_dir: str = RECIPE, log=None, test_spot: bool = False, events: bool = True,
          stage2: bool = True) -> dict:
    """Build the two mods and (add=True) put them in the library: the item always on, the main switch new as `on`
    (off unless asked: it is off by default). test_spot: the developer's test blade (build_package). Returns
    {"built", "entry", "main_entry", "notes"}."""
    recipe = load_recipe(recipe_dir)
    root = mm.resolve_library(library)
    lib = mm.Library.load(root)
    if add and lib is None:
        raise DragonError(f"No mod library in {root} yet. Run: python tools/modman.py init")
    game = find_game(install, lib, recipe.donor)
    tmp = None
    if out is None:
        tmp = tempfile.mkdtemp(prefix="dragonbreath-build-")
        out = os.path.join(tmp, "out")
    out = os.path.abspath(out)
    try:
        built = build_package(game, recipe, out, log=log, used=set(other_hash_ids(lib, recipe)),
                              test_spot=test_spot, stage2=stage2, events=events)
        entry = main_entry = moves = moves_entry = None
        notes: list[str] = []
        if add:
            entry, n1 = add_to_library(lib, built.folder, recipe.item_manifest["id"], log)
            main_entry, n2 = add_to_library(lib, built.main_folder, recipe.manifest["id"], log, new_on=on)
            if on and not main_entry.get("enabled"):
                main_entry["enabled"] = True
                lib.save()
            notes = n1 + n2
        # stage 3 on its own: a moves build that fails is a note, the blade (built and added above) stays
        try:
            moves = build_moves(game.install, out, log, os.path.join(recipe_dir, "clips"))
        except DragonError as e:
            old = add and any(m["id"] == moves_id() for m in lib.mods)
            notes.append(f"{e} (the blade is built without them"
                         f"{'; the old moves stay in your library' if old else ''})")
        if add and moves is not None:
            moves_entry, n3 = add_to_library(lib, moves.folder, moves_id(), log)
            notes += n3
        return {"built": built, "entry": entry, "main_entry": main_entry, "moves": moves, "moves_entry": moves_entry,
                "notes": notes}
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def moves_id() -> str:
    import dragonbreath_clips as dc
    return dc.PKG_ID


def build_moves(install: str, out: str, log=None, folder: str | None = None):
    """Stage 3: our own attack clips (tools/dragonbreath_clips.py, the recipe's clips folder) as the hidden package
    giruka.dragonbreath-moves (part of the Dragonbreath switch). None when the recipe has no clip files; a failure
    says why and stops the build."""
    import dragonbreath_clips as dc
    import emote_build as eb
    folder = folder or dc.CLIPS
    try:
        clips = dc.load_clips(folder) if os.path.isdir(folder) else []
        if not clips:
            return None
        return dc.build_package(eb.find_game(install), clips, out, log=log)
    except (dc.ClipError, eb.EmoteError) as e:
        raise DragonError(f"the fast moves (stage 3) can't be built: {e}") from None


# ------------------------------------------------------------------------------------------------ check

def check(library: str | None = None, *, install: str | None = None, recipe_dir: str = RECIPE, log=None,
          stage2: bool = True, stage2_dir: str = s2.STAGE2) -> list[str]:
    """Offline checks on the player's files (reads only): the new names are free, the donor is a plain one-handed
    sword whose files can be read, the model and textures build and pass the writers' self-checks, the stats file
    reads back with our numbers (the tempered levels' too), the test spot is open ground; stage2: stage 2's own
    checks (tools/dragonbreath_stage2.py check). Returns the problems (empty: all good)."""
    recipe = load_recipe(recipe_dir)
    _st2, levels = load_levels(recipe, stage2_dir)
    lib = mm.Library.load(mm.resolve_library(library))
    game = find_game(install, lib, recipe.donor)
    out: list[str] = []
    actor, donor = recipe.actor, recipe.donor
    ai = game.actorinfo()
    if donor not in ai:
        out.append(f"actor {donor} isn't in your game's actor info")
    elif ai[donor].get("profile") != "WeaponSmallSword":
        out.append(f"{donor} isn't a one-handed sword ({ai[donor].get('profile')})")
    rstb = game.rstb()
    for a in [actor] + [lr.actor for lr in levels]:
        if a in ai:
            out.append(f"your game's actor info already has {a}")
        if os.path.isfile(game.path(f"Actor/Pack/{a}.sbactorpack")):
            out.append(f"your game already has Actor/Pack/{a}.sbactorpack")
        for name in (f"Actor/Pack/{a}.bactorpack", f"UI/StockItem/{a}.bitemico"):
            if rstb is not None and rstb.get(name) is not None:
                out.append(f"your game's size table already has {name}")
    if rstb is not None and rstb.get(f"Model/{actor}.bfres") is not None:
        out.append(f"your game's size table already has Model/{actor}.bfres")
    if f"IsGet_{actor}" in game.flags():
        out.append(f"your game already has the flag IsGet_{actor}")
    labels = message_labels(game)
    if labels is None:
        out.append("your English weapon texts can't be read")
    elif f"{actor}_Name" in labels:
        out.append(f"your English texts already have {actor}_Name")
    try:
        files = game.pack()
        link = link_of(files, donor).object("LinkTarget")
        if link.get_value("ChemicalUser") != "Weapon_Chemical_Iron":
            out.append(f"the donor {donor} isn't plain metal (chemical {link.get_value('ChemicalUser')}): "
                       "Dragonbreath has no fire of its own")
        m, donor_raw, shape = make_mesh(game, recipe)
        ts, names, alb = make_textures(shape, nx_bfres.read_textures(ak._unyaz0(game.raw("tex"))),
                                       ak.donor_sampler_textures(donor_raw), actor, log)
        ak.build_model(donor_raw, m, actor, names, donor=DONOR_NAME, weapon=WEAPON_NAME)
        tex = nx_bfres.write_textures(ts)
        out += [f"textures: {p}" for p in checks.switch_tex_problems(tex)[:3]]
        ak.build_icon(ak._unyaz0(game.raw("icon")), m, alb, actor, donor=DONOR_NAME)
        sp_actor, sp_tag = guardian_lists(game, recipe)
        weak = left_out_hit(game, recipe, sp_actor, sp_tag)
        if weak:
            out.append(f"the Guardian lists would weaken left-out actors too: {', '.join(weak[:6])}")
        stats = stats_file(files[gparam_name(files, donor)][0], recipe, sp_actor, sp_tag)
        got = read_stats(stats)
        want = {"attack": recipe.stats["attack"], "life": recipe.stats["life"],
                "life_infinite": recipe.stats["life_infinite"], "throwable": recipe.stats["throwable"],
                "subtype": recipe.stats["subtype"]}
        for k, v in want.items():
            if got[k] != v:
                out.append(f"the stats file reads back {k} = {got[k]!r}, not {v!r}")
        pk = actor_pack(files, recipe, stats)
        lost = [t for t in recipe.tags["drop"] if t in pk.tags]
        if lost:
            out.append(f"tags not dropped: {', '.join(lost)}")
        for lr in levels:          # the tempered levels: their numbers, the base blade's model
            got = read_stats(stats_file(files[gparam_name(files, donor)][0], lr, sp_actor, sp_tag))
            if got["attack"] != lr.stats["attack"] or abs(got["sp_ratio"] - lr.stats["guardian_ratio"]) > 1e-6:
                out.append(f"{lr.actor}'s stats read back attack {got['attack']}, Guardians x{got['sp_ratio']:g}")
            lpk = actor_pack(files, lr, stats, model=actor)
            ml = aamp.load(lpk.inner[f"Actor/ModelList/{lr.actor}.bmodellist"])
            folders = {o.get_value("Folder") for lst in ml.list("ModelData").lists.values()
                       for o in [lst.object("Base")] if o is not None}
            if folders != {actor}:
                out.append(f"{lr.actor}'s model list names {sorted(folders)}, not {actor}")
    except (DragonError, ak.AkError, ModelError, aamp.AampError) as e:
        out.append(f"the blade can't be built: {e}")
    spot = recipe.spot
    try:
        t = game.terrain()
        h, _ = t.height(spot["x"], spot["z"])
        if abs(h - spot["ground"]) > GROUND_TOLERANCE:
            out.append(f"the terrain at the test spot is {h:.1f} m high, not about {spot['ground']:.1f} m")
        sp = ak.ground_spread(t, spot["x"], spot["z"])
        if sp > MAX_SLOPE_SPREAD:
            out.append(f"the ground at the test spot rises {sp:.2f} m within 1.2 m (too steep for a blade to lie)")
    except ak.AkError as e:
        out.append(str(e))
    for unit_name in (spot["unit"], spot["unit"].rsplit("_", 1)[0] + "_Static"):
        try:
            unit = game.unit(unit_name)
            for o in (unit.get("Objs") or []) if isinstance(unit, dict) else []:
                tr = o.get("Translate") if isinstance(o, dict) else None
                if isinstance(tr, list) and len(tr) == 3 and \
                        math.hypot(tr[0] - spot["x"], tr[2] - spot["z"]) < NEAR_RADIUS:
                    out.append(f"{o.get('UnitConfigName')} ({unit_name}) stands within {NEAR_RADIUS:g} m of the "
                               "test spot")
        except (ak.AkError, OSError, bm.LogError, wc.FormatError, struct.error, IndexError) as e:
            out.append(f"the map unit {unit_name} can't be read ({e})")
    out += moves_problems(game.install, os.path.join(recipe_dir, "clips"), log)
    if stage2:
        try:
            out += [f"stage 2: {p}" for p in s2.check(library, install=install, folder=stage2_dir, log=log)]
        except s2.Stage2Error as e:
            out.append(f"stage 2: {e}")
    return out


def moves_problems(install: str, folder: str, log=None) -> list[str]:
    """Stage 3's clip checks on Link's own skeleton (tools/dragonbreath_clips.py check), when the recipe has clips."""
    import dragonbreath_clips as dc
    import emote_build as eb
    try:
        clips = dc.load_clips(folder) if os.path.isdir(folder) else []
    except dc.ClipError as e:
        return [f"clips: {e}"]
    if not clips:
        return []
    bad = [f"clips: {p}" for p in dc.lint(clips)]
    if bad:
        return bad
    try:
        _solved, reps = dc.check_all(dc.game_rig(eb.find_game(install), log), clips, log)
    except (dc.ClipError, eb.EmoteError) as e:
        return [f"clips: {e}"]
    return [f"clip {r.cid}: {p}" for r in reps for p in r.problems]


# ------------------------------------------------------------------------------------------------ clean

def clean(library: str | None = None, *, item: bool = False, recipe_dir: str = RECIPE, log=None) -> list[str]:
    """Take the main switch's mod out of the library (item=True: the item mod too). Returns what was removed."""
    recipe = load_recipe(recipe_dir)
    lib = mm.Library.load(mm.resolve_library(library))
    if lib is None:
        raise DragonError("No mod library here.")
    gone = []
    for mid in [recipe.manifest["id"], moves_id()] + ([recipe.item_manifest["id"]] if item else []):
        if any(m["id"] == mid for m in lib.mods):
            mm.remove_mod(lib, mid)
            gone.append(mid)
            _log(log, f"Removed {mid}.")
    return gone


# ------------------------------------------------------------------------------------------------ command line

def main(argv: list[str] | None = None) -> int:
    with contextlib.suppress(AttributeError, ValueError, OSError):
        sys.stdout.reconfigure(errors="replace")
    lib_help = "the mod library (default: as modman: SWMOD_LIBRARY, else next to Savage Wind)"
    game_help = "the Savage Wind folder (default: the library's)"
    common = argparse.ArgumentParser(add_help=False)   # (before or after the command: the launcher puts --library last)
    common.add_argument("--library", help=lib_help, default=argparse.SUPPRESS)
    common.add_argument("--game", help=game_help, default=argparse.SUPPRESS)
    ap = argparse.ArgumentParser(prog="dragonbreath_build.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--library", help=lib_help)
    ap.add_argument("--game", help=game_help)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", parents=[common], help="build the two mods and add them to the library")
    b.add_argument("--on", action="store_true", help="turn the Dragonbreath switch on (a new one is off otherwise)")
    b.add_argument("--no-add", action="store_true", help="only write the mod folders (into --out)")
    b.add_argument("--out", help="where to write the mod folders (needed with --no-add; a new or empty folder)")
    b.add_argument("--optional", action="store_true", help="a build that can't be made says why and ends with 0")
    b.add_argument("--quiet", action="store_true", help="say only whether it worked (the launcher's Details)")
    b.add_argument("--test-spot", action="store_true", help="developers only: also lay a blade near the test save's "
                                                            "start (picking it up moves the quest on)")
    b.add_argument("--no-events", action="store_true", help="stage 2 without its event flows: the cave and chest "
                                                            "only (no quest, talk or first-draw scene)")
    sub.add_parser("check", parents=[common], help="offline checks on your files (reads only)")
    p = sub.add_parser("preview", help="draw the blade to a PNG for a look (no game files needed, changes nothing)")
    p.add_argument("--out", required=True, help="the PNG to write")
    p.add_argument("--scale", type=int, default=2, choices=(1, 2, 3), help="texture detail (default 2)")
    p.add_argument("--width", type=int, default=1800, help="picture width in pixels (default 1800)")
    sub.add_parser("where", parents=[common], help="the game files Dragonbreath is built from")
    c = sub.add_parser("clean", parents=[common], help="take the Dragonbreath switch's mod out of the library")
    c.add_argument("--item", action="store_true", help="the item mod too (a save that holds the blade may then not "
                                                       "load: keep it unless you know the save has none)")
    args = ap.parse_args(argv)
    ak._low_priority()
    quiet = getattr(args, "quiet", False)
    optional = args.cmd == "build" and getattr(args, "optional", False)
    try:
        if args.cmd == "where":
            recipe = load_recipe()
            lib = mm.Library.load(mm.resolve_library(args.library))
            game = ak.Game(os.path.abspath(args.game or (lib.install if lib else None) or mm.default_install()),
                           recipe.donor)
            for k in ak.FILES:
                print(f"{'ok  ' if os.path.isfile(game.path(game.rel(k))) else 'MISSING'} {game.rel(k)}")
            return 0 if not game.missing() else 1
        if args.cmd == "check":
            problems = check(args.library, install=args.game, log=print)
            print("Dragonbreath check: all good." if not problems else
                  f"Dragonbreath check: {len(problems)} problem(s):")
            for x in problems:
                print(f"  - {x}")
            return 1 if problems else 0
        if args.cmd == "preview":
            recipe = load_recipe()
            if not 400 <= args.width <= 4000:
                raise DragonError("--width: 400 to 4000")
            pic = dm.preview(recipe.shape(), args.width, args.scale)
            menu_bg.write_png(args.out, pic)
            print(f"Preview written: {args.out} ({pic.w}x{pic.h}: the blade's face, its edge, the dragon's head)")
            return 0
        if args.cmd == "clean":
            if args.item:
                print("Note: a save that holds Dragonbreath may not load without the item mod.")
            gone = clean(args.library, item=args.item, log=print)
            print("Nothing to remove." if not gone else "Next: python tools/modman.py deploy")
            return 0
        if args.no_add and not args.out:
            raise DragonError("--no-add needs --out <folder>")
        r = build(args.library, install=args.game, out=args.out, add=not args.no_add, on=args.on,
                  log=None if quiet else print, test_spot=args.test_spot, events=not args.no_events)
        made = r["built"]
        if quiet:
            print("Done: Dragonbreath is built" + (" and in your library." if r["entry"] else ".")
                  + (f" ({len(made.notes) + len(r['notes'])} note(s))" if made.notes or r["notes"] else ""))
            return 0
        where = (f"in your library ({r['entry']['name']}; the switch {r['main_entry']['name']} is "
                 f"{'on' if r['main_entry'].get('enabled') else 'off'})") if r["entry"] else \
            f"{made.folder} and {made.main_folder}"
        print(f"Dragonbreath built ({made.vertices} vertices, {made.triangles} triangles): {where}.")
        if made.spot:
            print(f"  test spot (developer build): x={made.spot[0]:.1f} y={made.spot[1]:.1f} z={made.spot[2]:.1f}")
        if made.levels:
            print(f"  tempered levels: {', '.join(made.levels)}")
        r2 = made.stage2
        if r2 is not None:
            print(f"  stage 2: cave ({len(r2.cave.dynamic) if r2.cave else 0} objects)"
                  + (", quest, first draw" if r2.events else ", no quest or scene (events off)"))
        mv = r.get("moves")
        if mv is not None:
            print(f"  fast moves: {mv.clips} clips in {', '.join(mv.sets)} ({mv.folder}), "
                  f"{mv.speed['hits_per_second_x']:.1f}x the hits per second of the game's combo")
        for n in made.notes + r["notes"] + (mv.notes if mv is not None else []):
            print(f"  note: {n}")
        if r["entry"]:
            print("Next: python tools/modman.py deploy")
        return 0
    except (DragonError, ak.AkError, mm.ModmanError) as e:
        print(f"error: {e}", file=sys.stderr)
        if not optional:
            return 2
    except Exception as e:  # noqa: BLE001 (--optional: anything else that goes wrong never stops the launcher)
        if not optional:
            raise
        print("".join(traceback.format_exception(type(e), e, e.__traceback__, limit=-3)).rstrip(), file=sys.stderr)
    print("Dragonbreath wasn't built (it is optional). Build it later with: python tools/dragonbreath_build.py build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
