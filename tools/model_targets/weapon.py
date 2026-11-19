"""Weapon target (converter stage 1): a sword-like rigid model in any format tools/model_in reads -> a BotW
one-handed weapon mod, built on this PC from the player's own game files.

    python tools/model_targets/weapon.py build <model> --name "My Katana" --attack 30 --donor Weapon_Sword_047 \
        --out <folder> [--desc ...] [--length 1.1] [--durability 40] [--mesh NAME ...] [--game <Savage Wind folder>]
        [--share <folder>]

Steps (the AK-47 and Dragonbreath builders' own, imported, not copied):
1. read the model (model_in.load), pick its meshes (all, or --mesh), one rigid vertex list with an atlas of its
   materials' base colour pictures (WeaponMesh, as ak47_build.gather makes);
2. orient it: the longest side is the blade; the grip is the end nearest a bone or empty named like "grip"/"handle"
   (or the model's origin when it has none); the next-longest side is the edge. Scale it to --length metres (default:
   the donor's length) with ak47_build.to_weapon (hilt where the donor's is);
3. the donor's model, textures and icon -> ours (ak47_build.build_model / make_textures / build_icon);
4. the donor's actor pack -> ours with our stats file (dragonbreath_build.actor_pack; attack and durability set
   here), actor info, the IsGet flag, name and description texts, size entries.

Output: <out>/<mod id>/ (a BCML-style package folder like the AK-47's item mod). It holds parts of the player's game
(the donor's pack files), so it is never shared. --share writes a recipe instead (the model + recipe.json, no game
files), and only when the model's licence allows sharing: Scene meta "licence" with may_share true, or no licence
data and the player marked it shareable (meta "share" == "shareable"). Otherwise the mod is built private only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import zlib
from dataclasses import dataclass, field

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import aamp  # noqa: E402
import ak47_build as ak  # noqa: E402
import bcml_data as bd  # noqa: E402
import dragonbreath_build as db  # noqa: E402
import menu_bg  # noqa: E402
import ukmm  # noqa: E402
from model_in import ReadError, load  # noqa: E402
from wiiu_model import checks, nx_bfres  # noqa: E402
from wiiu_model.errors import ModelError  # noqa: E402

AXIS_NAMES = ("x", "y", "z")
GRIP_NAMES = ("grip", "handle", "hilt")
NEEDED = ("pack", "model", "tex", "icon", "actorinfo", "bootup")   # ak47_build.FILES keys this target reads
ATTACK_RANGE = (1, 999)
DURABILITY_RANGE = (1, 9999)
LENGTH_RANGE = (0.2, 4.0)        # metres
RECIPE_FILE = "recipe.json"
RECIPE_FORMAT = 1
DONOR_NAME = "the donor's"


class WeaponError(Exception):
    """A plain sentence for the player."""


def _log(log, msg: str) -> None:
    if log:
        log(msg)


# ------------------------------------------------------------------------------------------------- settings

@dataclass
class Settings:
    name: str
    attack: int
    donor: str
    desc: str = ""
    length: float | None = None           # metres; None = the donor's length
    durability: int | None = None         # None = the donor's
    meshes: list = field(default_factory=list)   # mesh names to keep (empty = all)
    actor: str = ""                        # default: from the name
    mod_id: str = ""                       # default: from the name

    def __post_init__(self):
        self.name = (self.name or "").strip()
        if not self.name or len(self.name) > 40:
            raise WeaponError("The weapon needs a name of 1 to 40 letters.")
        if not isinstance(self.attack, int) or not ATTACK_RANGE[0] <= self.attack <= ATTACK_RANGE[1]:
            raise WeaponError(f"Attack must be a whole number from {ATTACK_RANGE[0]} to {ATTACK_RANGE[1]}.")
        if self.durability is not None and not DURABILITY_RANGE[0] <= self.durability <= DURABILITY_RANGE[1]:
            raise WeaponError(f"Durability must be from {DURABILITY_RANGE[0]} to {DURABILITY_RANGE[1]}.")
        if self.length is not None and not LENGTH_RANGE[0] <= self.length <= LENGTH_RANGE[1]:
            raise WeaponError(f"Length must be {LENGTH_RANGE[0]} to {LENGTH_RANGE[1]} metres.")
        for what, v in (("donor", self.donor), ("actor", self.actor)):
            if v and (not re.fullmatch(r"[A-Za-z0-9_]{1,48}", v)):
                raise WeaponError(f"The {what} isn't an actor name (letters, digits and _ only, up to 48).")
        if not self.donor:
            raise WeaponError("Pick a donor sword from your game, e.g. Weapon_Sword_047.")
        words = re.findall(r"[A-Za-z0-9]+", self.name)
        if not self.actor:
            camel = "".join(w[:1].upper() + w[1:] for w in words) or f"X{zlib.crc32(self.name.encode()):08X}"
            self.actor = ("Weapon_Sword_" + camel)[:48]
        if not self.mod_id:
            self.mod_id = "weapon." + ("-".join(w.lower() for w in words) or self.actor.lower())
        self.desc = (self.desc or "").strip() or f"{self.name}, made with Breathalyzer's model converter."


# ------------------------------------------------------------------------------------------------- the scene

def _srgb(c: float) -> int:
    c = max(0.0, min(1.0, float(c)))
    s = c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
    return max(0, min(255, round(s * 255)))


def pick_meshes(scene, names=()) -> list:
    """The scene's meshes with triangles (only the named ones when names are given)."""
    want = set(names or ())
    if want:
        have = {m.name for m in scene.meshes}
        gone = sorted(want - have)
        if gone:
            raise WeaponError(f"The model has no mesh called {gone[0]!r} (it has: "
                              f"{', '.join(sorted(n for n in have if n)) or 'unnamed meshes'}).")
    out = [m for m in scene.meshes if m.triangles and m.positions and (not want or m.name in want)]
    if not out:
        raise WeaponError("The model has no triangles to make a weapon from.")
    return out


def scene_mesh(scene, names=(), atlas_size: int = 512) -> ak.WeaponMesh:
    """The picked meshes as one rigid WeaponMesh (scene space): every material's base colour picture (or its colour)
    an atlas slot, UVs moved into their slot, back faces added for parts with open edges or double-sided
    materials (the game's material draws front faces only)."""
    meshes = pick_meshes(scene, names)
    slots, slot_of, notes = [], {}, []
    pos, nrm, uv, idx, slot_ix = [], [], [], [], []
    for mesh in meshes:
        mat = scene.materials[mesh.material] if isinstance(mesh.material, int) and \
            0 <= mesh.material < len(scene.materials) else None
        metal = max(0.0, min(1.0, float(mat.metallic))) if mat else 1.0
        tex_i = mat.textures.get("base") if mat else None
        uv_set = (mat.texcoords.get("base", 0) if mat else 0) or 0
        if isinstance(tex_i, int) and 0 <= tex_i < len(scene.textures):
            key = ("image", tex_i, round(metal, 2))
        else:
            tex_i = None
            key = ("color", tuple(mat.base_color[:3]) if mat else (0.5, 0.5, 0.5), round(metal, 2))
        if key not in slot_of:
            if tex_i is not None:
                try:
                    data = scene.textures[tex_i].read_bytes()
                except ReadError as e:
                    raise WeaponError(str(e)) from None
                try:
                    pic = ak.load_image(data, f"the model's picture {scene.textures[tex_i].name or tex_i}")
                except ak.AkError as e:
                    raise WeaponError(str(e)) from None
            else:
                c = bytes(_srgb(x) for x in key[1])
                pic = menu_bg.Picture(4, 4, bytearray((c + b"\xff") * 16))
            slot_of[key] = len(slots)
            slots.append((pic, metal))
        s = slot_of[key]
        if mesh.skinned:
            notes.append(f"{mesh.name or 'a mesh'}: its skin is ignored (a weapon is one rigid piece)")
        uvs = mesh.uvs[uv_set] if len(mesh.uvs) > uv_set else (mesh.uvs[0] if mesh.uvs else [])
        base = len(pos)
        for k, p in enumerate(mesh.positions):
            pos.append(tuple(float(c) for c in p[:3]))
            nrm.append(tuple(float(c) for c in mesh.normals[k][:3]) if len(mesh.normals) == len(mesh.positions)
                       else None)
            uv.append(tuple(float(c) for c in uvs[k][:2]) if len(uvs) == len(mesh.positions) else (0.0, 0.0))
            slot_ix.append(s)
        part_start = len(idx)
        for a, b, c in mesh.triangles:
            idx += [a + base, b + base, c + base]
        if (mat and mat.double_sided) or _open_edges(pos, idx[part_start:]):
            back = []
            for t in range(part_start, len(idx), 3):
                nb = len(pos)
                for i in idx[t:t + 3]:
                    pos.append(pos[i])
                    nrm.append(None if nrm[i] is None else (-nrm[i][0], -nrm[i][1], -nrm[i][2]))
                    uv.append(uv[i])
                    slot_ix.append(slot_ix[i])
                back += [nb, nb + 2, nb + 1]
            idx += back
            notes.append(f"{mesh.name or 'a mesh'}: its back faces are drawn too (open edges or double-sided)")
    if len(pos) > ak.MAX_VERTICES:
        raise WeaponError(f"The model has {len(pos)} vertices; a weapon can have at most {ak.MAX_VERTICES} for now "
                          "(pick fewer meshes with --mesh).")
    if len(slots) > 8:
        raise WeaponError(f"The model uses {len(slots)} pictures and colours; a weapon can use at most 8 for now.")
    m = ak.WeaponMesh(pos=pos, nrm=nrm, uv=[], idx=idx, slots=slots, notes=notes, raw_uv=uv, slot_ix=slot_ix)
    m.layout(atlas_size, atlas_size)
    ak._fill_normals(m)
    return m


def _open_edges(pos, idx) -> bool:
    edges: dict = {}
    key = lambda i: tuple(round(c, 5) for c in pos[i])  # noqa: E731
    for t in range(0, len(idx), 3):
        a, b, c = key(idx[t]), key(idx[t + 1]), key(idx[t + 2])
        for e in (frozenset((a, b)), frozenset((b, c)), frozenset((c, a))):
            edges[e] = edges.get(e, 0) + 1
    return any(n == 1 for n in edges.values())


def grip_point(scene) -> tuple | None:
    """Where a bone or empty named like grip/handle/hilt is (rest pose, scene space), else None."""
    bones = scene.skeleton.bones
    for i, b in enumerate(bones):
        if any(g in (b.name or "").lower() for g in GRIP_NAMES):
            w = scene.skeleton.world_matrices()[i]
            return (w[12], w[13], w[14])
    return None


def orientation(m: ak.WeaponMesh, grip: tuple | None) -> tuple[str, str]:
    """(forward, up) for ak47_build.to_weapon: forward = the longest side, pointing from the grip end to the tip;
    up = the next-longest side (the edge)."""
    lo, hi = m.bounds()
    ext = [hi[k] - lo[k] for k in range(3)]
    order = sorted(range(3), key=lambda k: -ext[k])
    long_, edge = order[0], order[1]
    if ext[long_] < 1e-6:
        raise WeaponError("The model has no length.")
    g = grip if grip is not None else (0.0, 0.0, 0.0)
    near_lo = abs(g[long_] - lo[long_]) <= abs(g[long_] - hi[long_])
    return ("+" if near_lo else "-") + AXIS_NAMES[long_], "+" + AXIS_NAMES[edge]


# ------------------------------------------------------------------------------------------------- licence

def share_problem(scene) -> str | None:
    """None when the model may be shared (as a recipe), else the reason in one sentence."""
    meta = scene.meta or {}
    lic = meta.get("licence")
    if isinstance(lic, dict) and "may_share" in lic:
        return None if lic.get("may_share") is True else \
            "The model's licence doesn't allow sharing it, so it was built for you only."
    if meta.get("share") == "shareable":
        return None
    return "The model has no licence that allows sharing (mark it shareable only if its author allows it), so " \
           "it was built for you only."


# ------------------------------------------------------------------------------------------------- stats

def _set(obj, name: str, value, what: str) -> None:
    key = aamp.name_hash(name)
    if obj is None or key not in obj:
        raise WeaponError(f"The donor's stats file has no {what}; pick another donor sword.")
    old = obj[key]
    obj[key] = aamp.Param(old.type, type(old.value)(value))


def stats_file(data: bytes, s: Settings) -> bytes:
    """Our stats file from the donor's: attack (and its twin value), durability when given. Everything else stays."""
    def edit(root: aamp.PList) -> None:
        att, gen = root.object("Attack"), root.object("General")
        old = att.get_value("Power") if att is not None else None
        _set(att, "Power", s.attack, "Attack Power")
        if att is not None and db.POWER_TWIN in att and att[db.POWER_TWIN].value == old:
            att[db.POWER_TWIN] = aamp.Param(att[db.POWER_TWIN].type, type(att[db.POWER_TWIN].value)(s.attack))
        if s.durability is not None:
            _set(gen, "Life", s.durability, "General Life")
    try:
        return ak._aamp_edit(data, edit)
    except aamp.AampError as e:
        raise WeaponError(f"The donor's stats file can't be read ({e}).") from None


@dataclass
class _PackRecipe:   # what dragonbreath_build.actor_pack reads
    actor: str
    donor: str
    texts: dict
    tags: dict


# ------------------------------------------------------------------------------------------------- building

@dataclass
class Built:
    folder: str
    actor: str
    vertices: int = 0
    triangles: int = 0
    shared: str = ""                     # the recipe folder, when --share was allowed
    notes: list = field(default_factory=list)


def find_game(install: str | None, donor: str) -> ak.Game:
    """The player's game (--game, else the usual Savage Wind folder); only what this target reads must be there."""
    game = ak.Game(os.path.abspath(install or ak.mm.default_install()), donor)
    if not os.path.isdir(game.romfs):
        raise WeaponError(f"Savage Wind's game files aren't in {game.romfs}. Set them up in Savage Wind first, or "
                          "pass --game <Savage Wind folder>.")
    gone = [game.rel(k) for k in NEEDED if not os.path.isfile(game.path(game.rel(k)))]
    if gone:
        raise WeaponError(f"Your game files lack what the weapon is built from ({donor}): " + ", ".join(gone))
    return game


def _texts_log(actor: str, msg_file: str, name: str, desc: str) -> dict:
    return {"USen": {f"{msg_file}.msyt": {
        f"{actor}_Name": {"contents": [{"text": name}]},
        f"{actor}_Desc": {"contents": [{"text": desc}]},
        f"{actor}_PictureBook": {"contents": [{"text": desc}]}}}}


def build_package(game: ak.Game, scene, s: Settings, out: str, *, log=None) -> Built:
    """Write the weapon mod into out/<mod id> (out: a new or empty folder). Reads only the player's game files and
    the scene."""
    if os.path.isdir(out) and os.listdir(out):
        raise WeaponError(f"{out} isn't empty.")
    actor, donor = s.actor, s.donor
    ai = game.actorinfo()
    if donor not in ai:
        raise WeaponError(f"Your game has no actor called {donor} to build from.")
    if actor in ai:
        raise WeaponError(f"Your game already has an actor called {actor}; pick another name.")
    folder = os.path.join(out, s.mod_id)
    romfs = os.path.join(folder, ak.BASE_TID, "romfs")
    res = Built(folder, actor)
    rstb = game.rstb()
    if rstb is None:
        res.notes.append("the game's size table isn't there: no size entries for the new files")
    rstb_json: dict[str, int] = {}

    def size(name, ref, ref_len, new_len, pack):
        v = ak._estimate(rstb, ref, ref_len, new_len, pack, res.notes, name)
        if v:
            rstb_json[name] = v

    try:
        # the model
        _log(log, "Fitting the model to the donor sword...")
        donor_raw = ak._unyaz0(game.raw("model"))
        try:
            dres = ak.read_model(donor_raw)
        except ModelError as e:
            raise WeaponError(f"{donor}'s model can't be read ({e}).") from None
        raw = scene_mesh(scene, s.meshes)
        forward, up = orientation(raw, grip_point(scene))
        length = s.length if s.length is not None else ak.donor_length(dres)
        m = ak.to_weapon(raw, forward, up, ak.donor_hilt(dres), length)
        res.notes += m.notes + [n for n in (scene.meta.get("notes") or []) if isinstance(n, str)]
        res.vertices, res.triangles = len(m.pos), len(m.idx) // 3
        tex_raw = ak._unyaz0(game.raw("tex"))
        try:
            donor_ts = nx_bfres.read_textures(tex_raw)
        except ModelError as e:
            raise WeaponError(f"{donor}'s textures can't be read ({e}).") from None
        ts, tex_names, alb = ak.make_textures(m, donor_ts, ak.donor_sampler_textures(donor_raw), actor, log)
        _log(log, f"Writing the model ({res.vertices} vertices, {res.triangles} triangles)...")
        model_bytes, _ = ak.build_model(donor_raw, m, actor, tex_names, donor=DONOR_NAME, weapon=s.name)
        tex_bytes = nx_bfres.write_textures(ts)
        problems = checks.switch_tex_problems(tex_bytes)
        if problems:
            raise WeaponError(f"The weapon's textures fail their self-check: {'; '.join(map(str, problems[:3]))}")
        ak._write(os.path.join(romfs, "Model", f"{actor}.sbfres"),
                  ukmm.yaz0(model_bytes, ak._yaz0_align(game.raw("model"))))
        ak._write(os.path.join(romfs, "Model", f"{actor}.Tex.sbfres"),
                  ukmm.yaz0(tex_bytes, ak._yaz0_align(game.raw("tex"))))
        size(f"Model/{actor}.bfres", f"Model/{donor}.bfres", len(donor_raw), len(model_bytes), False)
        size(f"Model/{actor}.Tex.bfres", f"Model/{donor}.Tex.bfres", len(tex_raw), len(tex_bytes), False)

        # the icon
        _log(log, "Drawing the inventory icon...")
        icon_raw = ak._unyaz0(game.raw("icon"))
        icon_bytes, _pic = ak.build_icon(icon_raw, m, alb, actor, donor=DONOR_NAME)
        ak._write(os.path.join(romfs, "UI", "StockItem", f"{actor}.sbitemico"),
                  ukmm.yaz0(icon_bytes, ak._yaz0_align(game.raw("icon"))))
        size(f"UI/StockItem/{actor}.bitemico", f"UI/StockItem/{donor}.bitemico", len(icon_raw), len(icon_bytes),
             False)

        # the actor pack, with our stats
        _log(log, f"Building {actor}'s actor pack from {donor}'s...")
        files = game.pack()
        try:
            gp = db.gparam_name(files, donor)
        except db.DragonError as e:
            raise WeaponError(str(e)) from None
        stats = stats_file(files[gp][0], s)
        pr = _PackRecipe(actor, donor, {"name": s.name}, {"drop": [], "add": []})
        try:
            pk = db.actor_pack(files, pr, stats, ak._yaz0_align(game.raw("pack")))
        except db.DragonError as e:
            raise WeaponError(str(e)) from None
        ak._write(os.path.join(romfs, "Actor", "Pack", f"{actor}.sbactorpack"), pk.data)
        size(f"Actor/Pack/{actor}.bactorpack", f"Actor/Pack/{donor}.bactorpack",
             ukmm.unpacked_size(game.raw("pack")), pk.raw_len, True)
        for name, data in pk.inner.items():
            size(name, pk.refs[name], len(files[pk.refs[name]][0]), len(data), False)
    except ak.AkError as e:
        raise WeaponError(str(e)) from None

    # actor info, flag, texts, sizes
    lo, hi = m.bounds()
    used_sort = {a.get("sortKey") for a in ai.values() if isinstance(a.get("sortKey"), int)}
    entry = ak.actorinfo_entry(ai[donor], actor, lo, hi, pk.tags, used_sort)
    if "attackPower" in entry:
        entry["attackPower"] = s.attack
    if s.durability is not None and "generalLife" in entry:
        entry["generalLife"] = s.durability
    for k in ("pictureBookLiveSpot1", "pictureBookLiveSpot2", "pictureBookSpecialDrop"):
        if k in entry:      # (as the Dragonbreath's: no Hyrule Compendium spots that were the donor's)
            entry[k] = -1
    entry.pop("homeArea", None)
    logs = {"actorinfo.yml": bd.to_text({str(zlib.crc32(actor.encode())): entry})}
    game_flags = game.flags()
    like = game_flags.get(f"IsGet_{donor}")
    isget = ak.flag_entry(f"IsGet_{actor}", like, **({} if like else {"IsOneTrigger": True}))
    logs["gamedata.yml"] = bd.to_text({"bool_data": {"add": {isget["DataName"]: isget}, "del": []}})
    logs["savedata.yml"] = bd.to_text({"add": [{"DataName": isget["DataName"], "HashValue": isget["HashValue"]}],
                                       "del": []})
    msg_file = f"ActorType/{ai[donor].get('profile') or 'WeaponSword'}"
    logs["texts.json"] = json.dumps(_texts_log(actor, msg_file, s.name, s.desc), indent=1, ensure_ascii=False) + "\n"
    logs["rstb.json"] = json.dumps(dict(sorted(rstb_json.items())), indent=1) + "\n"
    try:
        ak.check_logs(logs, "the weapon's")
    except ak.AkError as e:
        raise WeaponError(str(e)) from None
    manifest = {"id": s.mod_id, "name": s.name, "version": "1.0.0", "description": s.desc}
    toml_text = ("# Built on this PC by tools/model_targets/weapon.py from your own game files and your model.\n"
                 "# It holds parts of your game (the donor's actor pack): don't share this folder.\n\n"
                 f"[mod]\nid = {json.dumps(s.mod_id)}\nname = {json.dumps(s.name)}\nversion = \"1.0.0\"\n"
                 f"description = {json.dumps(s.desc)}\ngame_versions = [\"1.9.0\"]\n")
    ak._package_files(folder, toml_text, manifest, logs, [
        "# Weapon: built on this PC by tools/model_targets/weapon.py from the player's own game files and a model.",
        "", f"- actor: {actor} (from {donor})", f"- model: {res.vertices} vertices, {res.triangles} triangles",
        f"- attack: {s.attack}" + (f", durability {s.durability}" if s.durability is not None else ""),
        "- private: holds parts of the player's game; never shared (a shareable recipe is a separate folder)"])
    return res


def write_recipe(scene, model_path: str, s: Settings, folder: str) -> str:
    """The shareable recipe (the model file + recipe.json; no game files), only when the licence allows it."""
    why = share_problem(scene)
    if why:
        raise WeaponError(why)
    if os.path.isdir(folder) and os.listdir(folder):
        raise WeaponError(f"{folder} isn't empty.")
    os.makedirs(folder, exist_ok=True)
    name = os.path.basename(model_path)
    shutil.copyfile(model_path, os.path.join(folder, name))
    with open(model_path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    doc = {"format": RECIPE_FORMAT, "target": "weapon", "model": {"file": name, "sha256": sha,
                                                                  "meshes": list(s.meshes)},
           "licence": scene.meta.get("licence"), "actor": s.actor, "donor": s.donor, "mod_id": s.mod_id,
           "stats": {"attack": s.attack, "durability": s.durability, "length": s.length},
           "texts": {"name": s.name, "desc": s.desc}}
    with open(os.path.join(folder, RECIPE_FILE), "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    return folder


def build(model_path: str, s: Settings, out: str, *, install: str | None = None, share: str | None = None,
          log=None) -> Built:
    try:
        scene = load(model_path)
    except ReadError as e:
        raise WeaponError(str(e)) from None
    game = find_game(install, s.donor)
    res = build_package(game, scene, s, out, log=log)
    if share:
        why = share_problem(scene)
        if why:
            res.notes.append(why)
        else:
            res.shared = write_recipe(scene, model_path, s, share)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="weapon.py", description="Turn a sword-like model into a one-handed weapon mod.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build the weapon mod from a model")
    b.add_argument("model")
    b.add_argument("--name", required=True)
    b.add_argument("--attack", type=int, required=True)
    b.add_argument("--donor", default="Weapon_Sword_047")
    b.add_argument("--out", required=True)
    b.add_argument("--desc", default="")
    b.add_argument("--length", type=float, default=None, help="metres (default: the donor's length)")
    b.add_argument("--durability", type=int, default=None)
    b.add_argument("--mesh", action="append", default=[], help="keep only this mesh (repeatable)")
    b.add_argument("--actor", default="")
    b.add_argument("--id", default="", help="the mod's id")
    b.add_argument("--game", default=None, help="the Savage Wind folder (default: the usual one)")
    b.add_argument("--share", default=None, help="also write a shareable recipe here (only if the licence allows)")
    a = ap.parse_args(argv)
    try:
        s = Settings(name=a.name, attack=a.attack, donor=a.donor, desc=a.desc, length=a.length,
                     durability=a.durability, meshes=a.mesh, actor=a.actor, mod_id=a.id)
        res = build(a.model, s, a.out, install=a.game, share=a.share, log=print)
    except WeaponError as e:
        print(f"Can't build the weapon: {e}", file=sys.stderr)
        return 1
    print(f"Built {res.actor} in {res.folder} ({res.vertices} vertices, {res.triangles} triangles).")
    if res.shared:
        print(f"Shareable recipe: {res.shared}")
    for n in res.notes:
        print(f"Note: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
