#!/usr/bin/env python3
"""AK-47 (a Breathalyzer secret extra by giruka that comes with the multiplayer mod): builds the mod on your PC from
your own game files and a free 3D model, and adds it to your mod library with modman.

    python tools/ak47_build.py build [--library DIR] [--game DIR] [--model FILE] [--no-add --out DIR] [--optional]
                                     [--quiet | --verbose]
    python tools/ak47_build.py check [--library DIR] [--game DIR]      offline checks on your files (reads only)
    python tools/ak47_build.py icon --out FILE.png [--game DIR]          draws the inventory icon to a PNG
    python tools/ak47_build.py where [--library DIR] [--game DIR]       the game files it reads
    python tools/ak47_build.py rewood --model IN.glb --out OUT.glb       (developers) the model with our own wood

A new two-handed weapon, Weapon_Lsword_AK47 ("AK-47"), that never fires: it is the Traveler's Claymore
(Weapon_Lsword_001) with an AK-47 model, held in the claymore's two-handed grip. One lies on a hilltop of the Great
Plateau (assets/ak47/ak47.json says where; docs/easter-eggs.md has only a vague hint). Our own parts are in
assets/ak47/: the recipe (ak47.json), the two mod.toml files and the model (AK-47.glb, "PSX style AK-47 Model" by
Plewr, CC0, with our own procedural wood texture: `rewood`). Nothing from the game is in this repo or in what we ship:
every pack, model, icon, map change and size entry is made here from the player's `extracted\\romfs` (only read).

What `build` makes: two BCML-style package folders that `modman add` installs, so the map, actor info, game data, save
data and text changes are merged by tools/bcml_maps.py, bcml_data.py and bcml_texts.py the normal way. Both are
secret ([mod] secret: never listed on Home, in More... or on the game's Mods page). giruka.ak47, the item (always on
once built, so a save that holds the AK-47 always loads):

  Actor/Pack/Weapon_Lsword_AK47.sbactorpack   the claymore's pack: its actor link and model list renamed and pointed
                                              at our model; the amiibo and Hyrule Compendium tags dropped
  Model/Weapon_Lsword_AK47.sbfres             the claymore's model file (its Root bone, its material) with the AK's
                                              mesh: turned so the barrel points where the blade does, scaled to the
                                              blade's length, the stock where the hilt is (Link holds it there)
  Model/Weapon_Lsword_AK47.Tex.sbfres         the model's textures as an atlas in the claymore's formats (BC1 colour,
                                              BC5 normal and specular), with mipmaps
  UI/StockItem/Weapon_Lsword_AK47.sbitemico   an inventory icon drawn from the model, like the game's (diagonal)
  logs/actorinfo.yml                          the claymore's actor info, renamed (its model, bounds, tags)
  logs/texts.json                             "AK-47", its description (ActorType/WeaponLargeSword)
  logs/gamedata.yml, savedata.yml             IsGet_Weapon_Lsword_AK47 and the placed one's pick-up flag
                                              (MainField_Weapon_Lsword_AK47_<HashId>: back after a Blood Moon)
  logs/rstb.json                              size entries for the new files, from the game's entries for the files
                                              they come from

and giruka.ak47-spot, where one lies (its spot.toml says part_of = "botw-mod-loader.ghost": modman uses it only
while the multiplayer mod is on):

  logs/map.yml                                one AK-47 lying on the hilltop (a loose _Dynamic map unit), its height
                                              read from the player's terrain files; a HashId that neither the game's
                                              map nor another library mod's map changes use

The multiplayer mod's entry in tools/packaging/bundled-mods.toml runs `build --optional --quiet` the first time the
multiplayer mod is turned on (after_on), so the AK-47 comes with it (a build that can't be made leaves the multiplayer
mod alone; --quiet names nothing, for the launcher's Details; only --verbose prints where it lies).

Pure Python (no third-party packages): the glTF reader, the geometry, the BC encoders (tools/menu_bg.py's), the Switch
model and texture writers (tools/wiiu_model) and the icon's rasterizer are all here or in our own tools.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import math
import os
import re
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
import bcml_data as bd  # noqa: E402
import bcml_maps as bm  # noqa: E402
import bcml_texts as bt  # noqa: E402
import bnp  # noqa: E402
import menu_bg  # noqa: E402
import modman as mm  # noqa: E402
import ukmm  # noqa: E402
import wiiu_convert as wc  # noqa: E402
from wiiu_model import checks, formats, nx_bfres, nx_model, read_model  # noqa: E402
from wiiu_model.errors import ModelError  # noqa: E402
from wiiu_model.neutral import Mesh, Texture, TextureSet, VertexBuffer  # noqa: E402

REPO = os.path.dirname(TOOLS)
RECIPE = os.path.join(REPO, "assets", "ak47")
BASE_TID = "01007EF00011E000"
MP_ID = "botw-mod-loader.ghost"           # the multiplayer mod the AK-47 comes with (spot.toml's [mod] part_of)
SPOT_TOML = "spot.toml"                  # assets/ak47: the spot mod's mod.toml (mod.toml is the item's)
GROUND_TOLERANCE = 2.0                   # m: the player's terrain may differ this much from the recipe's height
MAX_SLOPE_SPREAD = 0.6                   # m: the most the ground may rise or fall within 1.2 m of the spot
NEAR_RADIUS = 3.0                        # m: nothing the game places (static or dynamic) may stand this near the spot
MAX_GLB = 64 << 20
MAX_VERTICES = 60000                     # u16 indices, one shape
MAX_IMAGE = 4096
OUT_FILE = "BUILT.md"
ICON_SUPERSAMPLE = 3
# The placed weapon's parameters: a weapon lying on the ground (not stuck in it), not upgraded by the game's level
# sensor, not dropped by an enemy. (The keys every placed claymore in the game has.)
PLACED_PARAMS = {"AddParam": 0, "AddSpecialFlag": 0, "DropTable": "Normal", "EnemyOwnItem": False,
                 "IsCreateSharpWeapon": False, "IsEmitLandNoise": False, "IsFixedPlace": False, "IsPlayerPut": False,
                 "IsWeaponCreateByRawLife": False, "LevelSensorMode": 0, "Life": -1, "SharpWeaponJudgeType": 0}
FLAG_DEFAULT = {"DeleteRev": -1, "InitValue": 0, "IsEventAssociated": False, "IsOneTrigger": False,
                "IsProgramReadable": True, "IsProgramWritable": True, "IsSave": True, "MaxValue": True,
                "MinValue": False, "ResetType": 0}


class AkError(Exception):
    """Something the player has to fix; the message says what."""


def _log(log, msg: str) -> None:
    if log:
        log(msg)


# ---------------------------------------------------------------------------------------------------- the recipe

@dataclass
class Recipe:
    manifest: dict
    toml_text: str
    actor: str
    donor: str
    model: dict
    drop_tags: list[str]
    texts: dict
    spot: dict
    folder: str
    spot_manifest: dict = field(default_factory=dict)   # spot.toml's [mod]
    spot_toml_text: str = ""

    @property
    def model_path(self) -> str:
        return os.path.join(self.folder, self.model["file"])


AXES = {"+x": (1.0, 0.0, 0.0), "-x": (-1.0, 0.0, 0.0), "+y": (0.0, 1.0, 0.0), "-y": (0.0, -1.0, 0.0),
        "+z": (0.0, 0.0, 1.0), "-z": (0.0, 0.0, -1.0)}


def _num(v, what: str) -> float:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
        raise AkError(f"assets/ak47/ak47.json: {what} isn't a number")
    return float(v)


def load_recipe(folder: str = RECIPE) -> Recipe:
    """assets/ak47: mod.toml (the item's), spot.toml (the spot's) and ak47.json, checked."""
    try:
        with open(os.path.join(folder, "mod.toml"), encoding="utf-8") as f:
            toml_text = f.read()
        manifest = tomllib.loads(toml_text)
        with open(os.path.join(folder, SPOT_TOML), encoding="utf-8") as f:
            spot_text = f.read()
        spot_manifest = tomllib.loads(spot_text)
        with open(os.path.join(folder, "ak47.json"), encoding="utf-8") as f:
            r = json.load(f)
    except (OSError, ValueError) as e:
        raise AkError(f"The AK-47 recipe in {folder} can't be read ({e}).") from None
    mod = manifest.get("mod") or {}
    if not isinstance(mod.get("id"), str) or not isinstance(mod.get("version"), str):
        raise AkError("assets/ak47/mod.toml has no [mod] id and version")
    if mod.get("part_of"):
        raise AkError("assets/ak47/mod.toml (the item) has a part_of: the item stays on, so saves that hold it load")
    smod = spot_manifest.get("mod") or {}
    if not isinstance(smod.get("id"), str) or not isinstance(smod.get("version"), str) or smod["id"] == mod["id"]:
        raise AkError(f"assets/ak47/{SPOT_TOML} has no [mod] id (not the item's) and version")
    if not isinstance(smod.get("part_of"), str):
        raise AkError(f"assets/ak47/{SPOT_TOML} has no [mod] part_of (the multiplayer mod)")
    if not isinstance(r, dict) or r.get("format") != 1:
        raise AkError("assets/ak47/ak47.json isn't format 1")
    actor, donor = r.get("actor"), r.get("donor")
    for what, v in (("actor", actor), ("donor", donor)):
        if not isinstance(v, str) or not v.replace("_", "").isalnum() or len(v) > 48:
            raise AkError(f"assets/ak47/ak47.json: {what} isn't an actor name")
    model = r.get("model") or {}
    if not isinstance(model.get("file"), str) or os.path.basename(model["file"]) != model["file"]:
        raise AkError("assets/ak47/ak47.json: model file must be a file name in assets/ak47")
    for k in ("forward", "up"):
        if model.get(k) not in AXES:
            raise AkError(f"assets/ak47/ak47.json: model {k} must be one of {', '.join(AXES)}")
    if not any(abs(a) > 0 for a in _cross(AXES[model["forward"]], AXES[model["up"]])):
        raise AkError("assets/ak47/ak47.json: model forward and up must be at right angles")
    model["length"] = _num(model.get("length", 1.0), "model length")
    if not 0.2 <= model["length"] <= 3.0:
        raise AkError("assets/ak47/ak47.json: model length is a scale of the claymore's, 0.2 to 3")
    texts = r.get("texts") or {}
    for k in ("name", "desc"):
        if not isinstance(texts.get(k), str) or not texts[k].strip():
            raise AkError(f"assets/ak47/ak47.json: texts {k} is missing")
    spot = r.get("spot") or {}
    if not isinstance(spot.get("unit"), str) or not bm.UNIT_RE.fullmatch(spot["unit"]):
        raise AkError("assets/ak47/ak47.json: spot unit isn't a map unit like D-6_Dynamic")
    for k in ("x", "z", "ground", "lift", "yaw"):
        spot[k] = _num(spot.get(k), f"spot {k}")
    want = _unit_of(spot["x"], spot["z"])
    if spot["unit"].rsplit("_", 1)[0] != want:
        raise AkError(f"assets/ak47/ak47.json: the spot is in map section {want}, not {spot['unit']}")
    tags = r.get("drop_tags") or []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise AkError("assets/ak47/ak47.json: drop_tags isn't a list of tag names")
    return Recipe(manifest=mod, toml_text=toml_text, actor=actor, donor=donor, model=model, drop_tags=tags,
                  texts=texts, spot=spot, folder=folder, spot_manifest=smod, spot_toml_text=spot_text)


def _unit_of(x: float, z: float) -> str:
    """The main field's map section ("D-6") of a point: columns A-J are 1000 m wide from x = -5000, rows 1-8 from
    z = -4000."""
    col = min(9, max(0, int(math.floor((x + 5000) / 1000))))
    row = min(7, max(0, int(math.floor((z + 4000) / 1000))))
    return f"{'ABCDEFGHIJ'[col]}-{row + 1}"


# ---------------------------------------------------------------------------------------------------- small math

def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _norm(v, fallback=(0.0, 0.0, 1.0)):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / n, v[1] / n, v[2] / n) if n > 1e-12 else fallback


def _mat_mul(a: list, b: list) -> list:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _trs(node: dict) -> list:
    """A glTF node's local matrix (row-major 4x4)."""
    if "matrix" in node:
        m = node["matrix"]
        return [[m[c * 4 + r] for c in range(4)] for r in range(4)]   # (glTF stores it column by column)
    tx, ty, tz = node.get("translation", (0, 0, 0))
    qx, qy, qz, qw = node.get("rotation", (0, 0, 0, 1))
    sx, sy, sz = node.get("scale", (1, 1, 1))
    r = [[1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
         [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
         [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)]]
    return [[r[0][0] * sx, r[0][1] * sy, r[0][2] * sz, tx], [r[1][0] * sx, r[1][1] * sy, r[1][2] * sz, ty],
            [r[2][0] * sx, r[2][1] * sy, r[2][2] * sz, tz], [0.0, 0.0, 0.0, 1.0]]


def _det3(m) -> float:
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def _normal_matrix(m) -> list:
    """Inverse transpose of a 4x4's upper 3x3 (for normals under non-uniform scale)."""
    d = _det3(m)
    if abs(d) < 1e-20:
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    c = [[(m[(i + 1) % 3][(j + 1) % 3] * m[(i + 2) % 3][(j + 2) % 3]
           - m[(i + 1) % 3][(j + 2) % 3] * m[(i + 2) % 3][(j + 1) % 3]) / d for j in range(3)] for i in range(3)]
    return c   # cofactors / det = inverse transpose


# ---------------------------------------------------------------------------------------------------- glTF reader

COMPONENTS = {5120: ("b", 1, 127.0), 5121: ("B", 1, 255.0), 5122: ("h", 2, 32767.0), 5123: ("H", 2, 65535.0),
              5125: ("I", 4, None), 5126: ("f", 4, None)}
COUNTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


class Glb:
    """A binary glTF 2.0 file (.glb): its JSON and its one binary chunk. Only what a static textured mesh needs is
    read (accessors without sparse parts, node transforms, materials' base colour images)."""

    def __init__(self, data: bytes, name: str = "the model"):
        self.name = name
        if len(data) < 20 or data[:4] != b"glTF":
            raise AkError(f"{name} isn't a .glb file (binary glTF)")
        version, total = struct.unpack_from("<II", data, 4)
        if version != 2 or total > len(data):
            raise AkError(f"{name} isn't a glTF 2.0 .glb file, or it is cut short")
        pos, self.doc, self.bin = 12, None, b""
        while pos + 8 <= total:
            size, kind = struct.unpack_from("<II", data, pos)
            chunk = data[pos + 8:pos + 8 + size]
            if len(chunk) != size:
                raise AkError(f"{name} is cut short")
            if kind == 0x4E4F534A and self.doc is None:      # JSON
                try:
                    self.doc = json.loads(chunk.decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as e:
                    raise AkError(f"{name}'s JSON can't be read ({e})") from None
            elif kind == 0x004E4942 and not self.bin:        # BIN
                self.bin = chunk
            pos += 8 + ((size + 3) & ~3)
        if not isinstance(self.doc, dict):
            raise AkError(f"{name} has no JSON part")
        need = [x for x in self.doc.get("extensionsRequired") or []]
        if need:
            raise AkError(f"{name} needs glTF extensions this tool can't read: {', '.join(map(str, need))} "
                          "(export it again without compression)")

    def _list(self, key: str) -> list:
        v = self.doc.get(key) or []
        return v if isinstance(v, list) else []

    def view(self, i: int) -> tuple[bytes, int]:
        """(the bytes of buffer view i, its stride or 0)."""
        views = self._list("bufferViews")
        if not 0 <= i < len(views):
            raise AkError(f"{self.name}: a buffer view is missing")
        v = views[i]
        if v.get("buffer", 0) != 0 or "uri" in (self._list("buffers")[0] if self._list("buffers") else {}):
            raise AkError(f"{self.name} keeps its data in another file: export a single .glb")
        off, n = int(v.get("byteOffset", 0)), int(v["byteLength"])
        if off < 0 or n < 0 or off + n > len(self.bin):
            raise AkError(f"{self.name}: a buffer view runs past the data")
        return self.bin[off:off + n], int(v.get("byteStride", 0))

    def accessor(self, i: int) -> list[tuple]:
        accs = self._list("accessors")
        if not isinstance(i, int) or not 0 <= i < len(accs):
            raise AkError(f"{self.name}: an accessor is missing")
        a = accs[i]
        if "sparse" in a:
            raise AkError(f"{self.name} uses sparse accessors: export it again without them")
        ct, typ, count = a.get("componentType"), a.get("type"), int(a.get("count", 0))
        if ct not in COMPONENTS or typ not in COUNTS or not 0 <= count <= 1 << 22:
            raise AkError(f"{self.name}: an accessor of a kind this tool can't read ({typ}, {ct})")
        fmt, size, scale = COMPONENTS[ct]
        k = COUNTS[typ]
        if "bufferView" not in a:
            return [tuple([0] * k)] * count
        data, stride = self.view(a["bufferView"])
        stride = stride or size * k
        base = int(a.get("byteOffset", 0))
        if count and base + stride * (count - 1) + size * k > len(data):
            raise AkError(f"{self.name}: an accessor runs past its data")
        unpack = struct.Struct("<" + fmt * k).unpack_from
        out = [unpack(data, base + stride * j) for j in range(count)]
        if a.get("normalized") and scale:
            out = [tuple(max(-1.0, x / scale) for x in v) for v in out]
        return out

    def image_source(self, tex_index) -> int | None:
        """The image index of texture tex_index, or None."""
        texs, imgs = self._list("textures"), self._list("images")
        if not isinstance(tex_index, int) or not 0 <= tex_index < len(texs):
            return None
        src = texs[tex_index].get("source")
        return src if isinstance(src, int) and 0 <= src < len(imgs) else None

    def image(self, src) -> bytes:
        """The encoded image (PNG...) number src."""
        im = self._list("images")[src]
        if "bufferView" not in im:
            raise AkError(f"{self.name} keeps an image in another file: export a single .glb")
        return self.view(im["bufferView"])[0]

    def node_worlds(self) -> dict[int, list]:
        """World matrices of the nodes of the default scene (all root nodes when it has none)."""
        nodes = self._list("nodes")
        scenes = self._list("scenes")
        sc = self.doc.get("scene", 0)
        roots = (scenes[sc].get("nodes") if isinstance(sc, int) and 0 <= sc < len(scenes) else None)
        if roots is None:
            kids = {c for n in nodes for c in n.get("children") or []}
            roots = [i for i in range(len(nodes)) if i not in kids]
        out: dict[int, list] = {}
        eye = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
        stack = [(r, eye) for r in roots]
        while stack:
            i, parent = stack.pop()
            if not isinstance(i, int) or not 0 <= i < len(nodes) or i in out or len(out) > 10000:
                continue
            out[i] = _mat_mul(parent, _trs(nodes[i]))
            stack += [(c, out[i]) for c in nodes[i].get("children") or []]
        return out


def load_image(data: bytes, what: str) -> menu_bg.Picture:
    """A model texture: PNG in pure Python, other kinds with Pillow when it is installed."""
    try:
        if data[:8] == menu_bg.PNG_SIG:
            pic = menu_bg.read_png(data)
        else:
            Image = menu_bg.pillow()
            if Image is None:
                raise AkError(f"{what} isn't a PNG, and other picture kinds need Pillow (pip install pillow)")
            import io
            with Image.open(io.BytesIO(data)) as im:
                im = im.convert("RGBA")
                pic = menu_bg.Picture(im.width, im.height, bytearray(im.tobytes()))
    except (menu_bg.PngError, OSError, ValueError) as e:
        raise AkError(f"{what} can't be read ({e})") from None
    if not (1 <= pic.w <= MAX_IMAGE and 1 <= pic.h <= MAX_IMAGE):
        raise AkError(f"{what} is {pic.w}x{pic.h}: at most {MAX_IMAGE}x{MAX_IMAGE}")
    return pic


# ---------------------------------------------------------------------------------------------------- the weapon mesh

@dataclass
class WeaponMesh:
    """The model in the claymore's space (metres; +Z the blade's direction, +X the edge, Y the flat side), one
    vertex list for the whole weapon. uv: in the texture atlas (laid out for its size by layout()); raw_uv and
    slot_ix: the model's own UVs and each vertex's atlas slot. slots: the images of the atlas, top to bottom."""
    pos: list
    nrm: list
    uv: list
    tan: list = field(default_factory=list)       # (x, y, z, w) w = handedness
    idx: list = field(default_factory=list)
    slots: list = field(default_factory=list)     # [(Picture, metallic 0..1)]
    notes: list = field(default_factory=list)
    raw_uv: list = field(default_factory=list)
    slot_ix: list = field(default_factory=list)

    def bounds(self) -> tuple[tuple, tuple]:
        lo = tuple(min(p[k] for p in self.pos) for k in range(3))
        hi = tuple(max(p[k] for p in self.pos) for k in range(3))
        return lo, hi

    def layout(self, width: int, height: int) -> None:
        """The UVs for an atlas of this size (atlas() draws it; the pad depends on the size)."""
        n = len(self.slots)
        self.uv = [_band_uv(u, v, s, n, width, height) for (u, v), s in zip(self.raw_uv, self.slot_ix)]


ATLAS_PAD = 4   # px of repeated edge around each atlas band (keeps mipmaps from bleeding into the next band)


def _pad(band: int) -> int:
    return min(ATLAS_PAD, band // 8)


def _band_uv(u: float, v: float, slot: int, nslots: int, width: int, height: int) -> tuple[float, float]:
    band = height // nslots
    pu, pv = _pad(width), _pad(band)
    u = min(1.0, max(0.0, u))
    v = min(1.0, max(0.0, v))
    return ((pu + u * (width - 2 * pu)) / width, (slot * band + pv + v * (band - 2 * pv)) / height)


def read_model_file(path: str) -> Glb:
    try:
        size = os.path.getsize(path)
        if size > MAX_GLB:
            raise AkError(f"{path} is bigger than {MAX_GLB >> 20} MB")
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise AkError(f"The model {path} can't be read ({e.strerror or e}).") from None
    return Glb(data, os.path.basename(path))


def gather(glb: Glb, atlas_size: int = 512) -> WeaponMesh:
    """Every triangle of the default scene in scene space (node transforms applied), the materials' base colour
    images as atlas slots, UVs moved into their slot. A mesh part with open edges also gets its back faces (glTF
    models are often drawn double-sided; the game's material draws front faces only)."""
    nodes = glb._list("nodes")
    meshes = glb._list("meshes")
    mats = glb._list("materials")
    slots: list = []
    slot_of: dict = {}
    pics: dict = {}
    pos, nrm, uv, idx, slot_ix = [], [], [], [], []
    notes: list[str] = []
    worlds = glb.node_worlds()
    for ni in sorted(worlds):
        node = nodes[ni]
        mi = node.get("mesh")
        if not isinstance(mi, int) or not 0 <= mi < len(meshes):
            continue
        if "skin" in node:
            notes.append(f"{node.get('name', ni)}: its skin is ignored (a weapon is one rigid piece)")
        w = worlds[ni]
        nm = _normal_matrix(w)
        mirrored = _det3(w) < 0
        part_start = len(idx)
        for p in meshes[mi].get("primitives") or []:
            if p.get("mode", 4) != 4:
                notes.append(f"{meshes[mi].get('name', mi)}: a part that isn't triangles was left out")
                continue
            attrs = p.get("attributes") or {}
            if "POSITION" not in attrs:
                continue
            P = glb.accessor(attrs["POSITION"])
            N = glb.accessor(attrs["NORMAL"]) if "NORMAL" in attrs else None
            T = glb.accessor(attrs["TEXCOORD_0"]) if "TEXCOORD_0" in attrs else [(0.0, 0.0)] * len(P)
            I = [x[0] for x in glb.accessor(p["indices"])] if "indices" in p else list(range(len(P)))
            if len(I) % 3 or any(not 0 <= i < len(P) for i in I):
                raise AkError(f"{glb.name}: a mesh has a broken index list")
            # its material's image -> an atlas slot
            mat = mats[p["material"]] if isinstance(p.get("material"), int) and 0 <= p["material"] < len(mats) \
                else {}
            pbr = mat.get("pbrMetallicRoughness") or {}
            tex = (pbr.get("baseColorTexture") or {}).get("index")
            metal = max(0.0, min(1.0, float(pbr.get("metallicFactor", 1.0))))
            src = glb.image_source(tex)
            if src is not None:
                key = ("image", src, round(metal, 2))
            else:
                key = ("color", tuple(pbr.get("baseColorFactor", (1, 1, 1, 1))), round(metal, 2))
            if key not in slot_of:
                if key[0] == "image":
                    pic = pics.get(src) or load_image(glb.image(src), f"{glb.name}'s image {src}")
                    pics[src] = pic
                else:
                    c = bytes(max(0, min(255, round(float(x) * 255))) for x in (list(key[1]) + [1, 1, 1, 1])[:3])
                    pic = menu_bg.Picture(4, 4, bytearray((c + b"\xff") * 16))
                slot_of[key] = len(slots)
                slots.append((pic, metal))
            s = slot_of[key]
            base = len(pos)
            for k in range(len(P)):
                x, y, z = P[k][:3]
                pos.append((w[0][0] * x + w[0][1] * y + w[0][2] * z + w[0][3],
                            w[1][0] * x + w[1][1] * y + w[1][2] * z + w[1][3],
                            w[2][0] * x + w[2][1] * y + w[2][2] * z + w[2][3]))
                if N is not None:
                    a, b, c = N[k][:3]
                    nrm.append(_norm((nm[0][0] * a + nm[0][1] * b + nm[0][2] * c,
                                      nm[1][0] * a + nm[1][1] * b + nm[1][2] * c,
                                      nm[2][0] * a + nm[2][1] * b + nm[2][2] * c)))
                else:
                    nrm.append(None)
                uv.append(tuple(float(t) for t in T[k][:2]))
                slot_ix.append(s)
            for t in range(0, len(I), 3):
                a, b, c = I[t] + base, I[t + 1] + base, I[t + 2] + base
                idx += [a, c, b] if mirrored else [a, b, c]
        # back faces for a part with open edges
        edges: dict = {}
        key = lambda i: (round(pos[i][0], 5), round(pos[i][1], 5), round(pos[i][2], 5))  # noqa: E731
        for t in range(part_start, len(idx), 3):
            tri = (key(idx[t]), key(idx[t + 1]), key(idx[t + 2]))
            for e in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
                k2 = frozenset(e)
                edges[k2] = edges.get(k2, 0) + 1
        if any(n == 1 for n in edges.values()):
            back = []
            for t in range(part_start, len(idx), 3):
                tri = idx[t:t + 3]
                nb = len(pos)
                for i in tri:
                    pos.append(pos[i])
                    nrm.append(None if nrm[i] is None else (-nrm[i][0], -nrm[i][1], -nrm[i][2]))
                    uv.append(uv[i])
                    slot_ix.append(slot_ix[i])
                back += [nb, nb + 2, nb + 1]
            idx += back
            notes.append(f"{node.get('name', ni)}: has open edges, so its back faces are drawn too")
    if not idx:
        raise AkError(f"{glb.name} has no triangles")
    if len(pos) > MAX_VERTICES:
        raise AkError(f"{glb.name} has {len(pos)} vertices; at most {MAX_VERTICES} (use a lower-poly model)")
    if not slots:
        slots.append((menu_bg.Picture(4, 4, bytearray(b"\x80\x80\x80\xff" * 16)), 1.0))
    if len(slots) > 8:
        raise AkError(f"{glb.name} uses {len(slots)} textures; at most 8")
    nslots = len(slots)
    if atlas_size // nslots < 2:
        raise AkError(f"{glb.name} uses too many textures for an atlas of {atlas_size} px")
    m = WeaponMesh(pos=pos, nrm=nrm, uv=[], idx=idx, slots=slots, notes=notes, raw_uv=uv, slot_ix=slot_ix)
    m.layout(atlas_size, atlas_size)
    _fill_normals(m)
    return m


def _fill_normals(m: WeaponMesh) -> None:
    """Face normals (area-weighted) for vertices the model gave none."""
    if all(n is not None for n in m.nrm):
        return
    acc = [[0.0, 0.0, 0.0] for _ in m.pos]
    for t in range(0, len(m.idx), 3):
        a, b, c = m.idx[t:t + 3]
        fn = _cross(_sub(m.pos[b], m.pos[a]), _sub(m.pos[c], m.pos[a]))
        for i in (a, b, c):
            for k in range(3):
                acc[i][k] += fn[k]
    m.nrm = [n if n is not None else _norm(tuple(acc[i]), (0.0, 1.0, 0.0)) for i, n in enumerate(m.nrm)]


def to_weapon(m: WeaponMesh, forward: str, up: str, hilt: tuple[float, float], length: float,
              flip: bool = False) -> WeaponMesh:
    """Turn the model so `forward` (the muzzle's way) is the blade's +Z and `up` (the sights' way) the edge's +X
    (flip: -X), scale it to `length`, put its back end at the hilt's end (hilt = (pommel z, top of the hilt z) of the
    claymore) and centre the part in the hilt zone on the Z axis (where Link's hands are)."""
    f, u = AXES[forward], AXES[up]
    s = _cross(f, u)
    if flip:
        u, s = (-u[0], -u[1], -u[2]), (-s[0], -s[1], -s[2])
    rot = lambda v: (_dot(u, v), _dot(s, v), _dot(f, v))  # noqa: E731
    pos = [rot(p) for p in m.pos]
    zmin = min(p[2] for p in pos)
    zmax = max(p[2] for p in pos)
    if zmax - zmin < 1e-6:
        raise AkError("the model has no length along its forward axis")
    sc = length / (zmax - zmin)
    pos = [(p[0] * sc, p[1] * sc, (p[2] - zmin) * sc + hilt[0]) for p in pos]
    grip = [p for p in pos if hilt[0] <= p[2] <= hilt[1]] or pos
    cx = (min(p[0] for p in grip) + max(p[0] for p in grip)) / 2
    cy = (min(p[1] for p in grip) + max(p[1] for p in grip)) / 2
    pos = [(p[0] - cx, p[1] - cy, p[2]) for p in pos]
    out = WeaponMesh(pos=pos, nrm=[_norm(rot(n)) for n in m.nrm], uv=list(m.uv), idx=list(m.idx),
                     slots=m.slots, notes=list(m.notes), raw_uv=list(m.raw_uv), slot_ix=list(m.slot_ix))
    out.tan = tangents(out)
    return out


def tangents(m: WeaponMesh) -> list:
    """Per-vertex tangents from the UVs (summed per triangle, made at right angles to the normal), w = handedness."""
    n = len(m.pos)
    T = [[0.0, 0.0, 0.0] for _ in range(n)]
    B = [[0.0, 0.0, 0.0] for _ in range(n)]
    for t in range(0, len(m.idx), 3):
        a, b, c = m.idx[t:t + 3]
        e1, e2 = _sub(m.pos[b], m.pos[a]), _sub(m.pos[c], m.pos[a])
        d1 = (m.uv[b][0] - m.uv[a][0], m.uv[b][1] - m.uv[a][1])
        d2 = (m.uv[c][0] - m.uv[a][0], m.uv[c][1] - m.uv[a][1])
        r = d1[0] * d2[1] - d2[0] * d1[1]
        if abs(r) < 1e-12:
            r = 1e-12
        sd = tuple((e1[k] * d2[1] - e2[k] * d1[1]) / r for k in range(3))
        td = tuple((e2[k] * d1[0] - e1[k] * d2[0]) / r for k in range(3))
        for i in (a, b, c):
            for k in range(3):
                T[i][k] += sd[k]
                B[i][k] += td[k]
    out = []
    for i in range(n):
        nv = m.nrm[i]
        t = tuple(T[i])
        d = _dot(nv, t)
        t = (t[0] - nv[0] * d, t[1] - nv[1] * d, t[2] - nv[2] * d)
        if math.sqrt(_dot(t, t)) < 1e-9:   # any direction at right angles to the normal
            t = _cross(nv, (0.0, 1.0, 0.0) if abs(nv[1]) < 0.9 else (1.0, 0.0, 0.0))
        t = _norm(t, (1.0, 0.0, 0.0))
        w = -1.0 if _dot(_cross(nv, t), tuple(B[i])) < 0 else 1.0
        out.append((t[0], t[1], t[2], w))
    return out


# ---------------------------------------------------------------------------------------------------- textures

def _box_down(px: bytes, w: int, h: int) -> tuple[bytes, int, int]:
    """Half size (2x2 average; a side of 1 stays 1)."""
    nw, nh = max(1, w // 2), max(1, h // 2)
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        y0 = min(h - 1, 2 * y)
        y1 = min(h - 1, 2 * y + 1)
        r0, r1 = y0 * w * 4, y1 * w * 4
        for x in range(nw):
            x0 = min(w - 1, 2 * x) * 4
            x1 = min(w - 1, 2 * x + 1) * 4
            o = (y * nw + x) * 4
            for c in range(4):
                out[o + c] = (px[r0 + x0 + c] + px[r0 + x1 + c] + px[r1 + x0 + c] + px[r1 + x1 + c] + 2) >> 2
    return bytes(out), nw, nh


def atlas(slots: list, width: int, height: int) -> menu_bg.Picture:
    """The slots' images stacked in bands (width x height, opaque), each with a few px of repeated edge (_pad: what
    WeaponMesh.layout leaves around each band)."""
    n = len(slots)
    band = height // n
    pu, pv = _pad(width), _pad(band)
    inner_w, inner_h = width - 2 * pu, band - 2 * pv
    rows: list[bytes] = []
    for pic, _metal in slots:
        img = menu_bg.resample(pic, (0, 0, pic.w, pic.h), inner_w, inner_h)
        lines = []
        for y in range(inner_h):
            r = bytes(img.px[y * inner_w * 4:(y + 1) * inner_w * 4])
            lines.append(r[:4] * pu + r + r[-4:] * pu)
        rows += [lines[0]] * pv + lines + [lines[-1]] * pv
    while len(rows) < height:
        rows.append(rows[-1])
    px = bytearray(b"".join(rows[:height]))
    px[3::4] = bytes([255]) * (width * height)
    return menu_bg.Picture(width, height, px)


def mip_levels(px: bytes, w: int, h: int, count: int) -> list[tuple[bytes, int, int]]:
    out = [(bytes(px), w, h)]
    while len(out) < count:
        out.append(_box_down(*out[-1]))
    return out


def bc5_encode(w: int, h: int, rg) -> bytes:
    """BC5 blocks (row-major) of a w x h two-channel image: rg(x, y) -> (r, g) for each texel."""
    out = []
    for by in range(0, max(h, 1), 4):
        for bx in range(0, max(w, 1), 4):
            rs, gs = [], []
            for y in range(by, by + 4):
                for x in range(bx, bx + 4):
                    r, g = rg(min(x, w - 1), min(y, h - 1))
                    rs.append(r)
                    gs.append(g)
            out.append(menu_bg._alpha_block(rs) + menu_bg._alpha_block(gs))   # (a BC3 alpha block is a BC4 block)
    return b"".join(out)


def _block_kind(fmt: int) -> str:
    return {0x1A: "bc1", 0x1C: "bc3", 0x1E: "bc5", 0x0B: "rgba8"}.get(fmt >> 8, "")


def _encode_color(px: bytes, w: int, h: int, fmt: int) -> bytes:
    kind = _block_kind(fmt)
    if kind in ("bc1", "bc3"):
        return menu_bg.encode_bc(px, w, h, kind == "bc3")
    if kind == "rgba8":
        return bytes(px)
    raise AkError(f"the claymore's colour texture is {formats.format_name(fmt)}, which this tool can't write")


def _spm_average(tex: Texture) -> tuple[int, int]:
    """The average of the claymore's specular texture's two channels (BC5), for ours."""
    if _block_kind(tex.format) != "bc5":
        return 96, 160
    data = tex.levels[0]
    rs = gs = n = 0
    for o in range(0, len(data) - 15, 16):
        r = menu_bg.decode_bc4(data[o:o + 8], 4, 4)
        g = menu_bg.decode_bc4(data[o + 8:o + 16], 4, 4)
        rs += sum(r)
        gs += sum(g)
        n += 16
    return (rs // n, gs // n) if n else (96, 160)


def make_textures(m: WeaponMesh, donor_ts: TextureSet, donor_names: dict, actor: str, log=None) -> tuple:
    """Our textures in the formats and sizes of the claymore's ones: (TextureSet, {role: name}, the colour atlas).
    donor_names: {"_a0": name, "_n0": name, "_s0": name, ...} from the claymore's material."""
    by_name = {t.name: t for t in donor_ts.textures}
    out, names, alb_pic = [], {}, None
    for sampler, old in donor_names.items():
        d = by_name.get(old)
        if d is None:
            raise AkError(f"the claymore's textures have no {old}")
        if d.dim != "2d" or d.depth != 1 or d.array != 1:
            raise AkError(f"the claymore's {old} isn't a flat 2D texture")
        role = {"_a0": "Alb", "_n0": "Nrm", "_s0": "Spm"}.get(sampler, sampler.strip("_"))
        name = f"{actor}_{role}"
        w, h, count = d.width, d.height, len(d.levels)
        if role == "Alb":
            _log(log, f"Encoding the colour texture ({w}x{h}, {formats.format_name(d.format)})...")
            m.layout(w, h)
            alb_pic = atlas(m.slots, w, h)
            levels = [_encode_color(px, lw, lh, d.format) for px, lw, lh in mip_levels(alb_pic.px, w, h, count)]
        elif _block_kind(d.format) == "bc5":
            if role == "Spm":
                ar, ag = _spm_average(d)
                n = len(m.slots)

                def rg(x, y, lw, lh, ar=ar, ag=ag, n=n):
                    s = min(n - 1, int(y * n / lh))
                    k = 0.3 + 0.7 * m.slots[s][1]          # (wood shines less than metal)
                    return min(255, round(ar * k)), ag
            else:   # normal map: flat (128, 128 -> z up)

                def rg(x, y, lw, lh):
                    return 128, 128
            levels = []
            lw, lh = w, h
            for _ in range(count):
                levels.append(bc5_encode(lw, lh, lambda x, y, lw=lw, lh=lh: rg(x, y, lw, lh)))
                lw, lh = max(1, lw // 2), max(1, lh // 2)
        else:
            raise AkError(f"the claymore's {old} is {formats.format_name(d.format)}, which this tool can't write")
        for k, lv in enumerate(levels):
            want = formats.level_size(d.format, w, h, k)
            if len(lv) != want:
                raise AkError(f"internal: {name} level {k} is {len(lv)} bytes, not {want}")
        t = copy.deepcopy(d)
        t.name, t.levels = name, levels
        out.append(t)
        names[sampler] = name
    ts = copy.deepcopy(donor_ts)
    ts.name, ts.textures = actor, out
    return ts, names, alb_pic


# ---------------------------------------------------------------------------------------------------- the model

def _f16(v: float) -> bytes:
    return struct.pack("<e", max(-65504.0, min(65504.0, v)))


def _snorm8(v: float) -> int:
    return max(-127, min(127, round(v * 127))) & 0xFF


def _n10(n) -> int:
    q = [max(-511, min(511, round(c * 511))) & 0x3FF for c in n]
    return q[0] | (q[1] << 10) | (q[2] << 20) | (1 << 30)


ENCODERS = {   # nn::gfx attribute format -> (bytes per vertex, encoder of a value)
    0x1505: (8, lambda v: b"".join(_f16(c) for c in (*v[:3], 1.0))),            # 16_16_16_16 float (w = 1)
    0x1805: (12, lambda v: struct.pack("<3f", *v[:3])),                          # 32_32_32 float
    0x0E02: (4, lambda v: struct.pack("<I", _n10(v[:3]))),                       # 10_10_10_2 snorm (w = 1)
    0x0B02: (4, lambda v: bytes(_snorm8(c) for c in (*v[:3], v[3] if len(v) > 3 else 1.0))),   # 8_8_8_8 snorm
    0x1205: (4, lambda v: _f16(v[0]) + _f16(v[1])),                              # 16_16 float
    0x1305: (8, lambda v: struct.pack("<2f", v[0], v[1])),                       # 32_32 float
}


def vertex_values(m: WeaponMesh, name: str):
    """The values of a model attribute (_p0, _n0, _t0, _b0, _u0) per vertex."""
    if name == "_p0":
        return m.pos
    if name == "_n0":
        return m.nrm
    if name == "_t0":
        return m.tan
    if name == "_b0":
        return [(*(c * t[3] for c in _cross(n, t[:3])), t[3]) for n, t in zip(m.nrm, m.tan)]   # cross(n, t) x w
    if name == "_u0":
        return m.uv
    raise AkError(f"the claymore's model has an attribute {name} this tool can't fill")


def _box(pts) -> tuple[tuple, float]:
    lo = [min(p[k] for p in pts) for k in range(3)]
    hi = [max(p[k] for p in pts) for k in range(3)]
    c = [(lo[k] + hi[k]) / 2 for k in range(3)]
    e = [(hi[k] - lo[k]) / 2 for k in range(3)]
    r = max(math.sqrt(sum((p[k] - c[k]) ** 2 for k in range(3))) for p in pts)
    return (c[0], c[1], c[2], e[0], e[1], e[2]), r


def donor_hilt(res) -> tuple[float, float]:
    """(the claymore's lowest z, 0): its hilt is the part below the origin, where Link holds it."""
    md = res.models[0]
    lo = min(s.bounds[0][2] - s.bounds[0][5] for s in md.shapes)
    return (lo, 0.0) if lo < 0 else (lo, lo + 0.5)


def donor_length(res) -> float:
    md = res.models[0]
    lo = min(s.bounds[0][2] - s.bounds[0][5] for s in md.shapes)
    hi = max(s.bounds[0][2] + s.bounds[0][5] for s in md.shapes)
    return hi - lo


def build_model(donor_raw: bytes, m: WeaponMesh, actor: str, tex_names: dict[str, str]) -> tuple[bytes, object]:
    """The claymore's model file with our mesh in its (first) shape and our texture names in its material. Returns
    (the BFRES bytes, the ResFile). Checked with the writer's own checks."""
    try:
        res = read_model(donor_raw)
    except ModelError as e:
        raise AkError(f"the claymore's model can't be read ({e})") from None
    if len(res.models) != 1 or not res.models[0].shapes:
        raise AkError("the claymore's model isn't one model with a shape")
    md = res.models[0]
    sh = md.shapes[0]
    vb_old = md.vertex_buffers[sh.vertex_buffer]
    if sh.skin_count != 0 or vb_old.skin_count != 0:
        raise AkError("the claymore's model is skinned; this tool expects a rigid weapon")
    n = len(m.pos)
    bufs = [bytearray() for _ in vb_old.strides]
    layout = []
    for a in vb_old.attributes:
        if a.format not in ENCODERS:
            raise AkError(f"the claymore's {a.name} is in a format this tool can't write ({a.format:#06x})")
        layout.append((a, ENCODERS[a.format], vertex_values(m, a.name)))
    for i in range(n):
        rows = [bytearray(s) for s in vb_old.strides]
        for a, (size, enc), vals in layout:
            rows[a.buffer][a.offset:a.offset + size] = enc(vals[i])
        for b, r in enumerate(rows):
            bufs[b] += r
    vb = VertexBuffer(vertex_count=n, skin_count=0, attributes=copy.deepcopy(vb_old.attributes),
                      strides=list(vb_old.strides), buffers=[bytes(b) for b in bufs], extra=copy.deepcopy(vb_old.extra))
    idx = struct.pack(f"<{len(m.idx)}H", *m.idx)
    mesh = Mesh(primitive="triangles", index_format="u16", index_count=len(m.idx), indices=idx, first_vertex=0,
                submeshes=[(0, len(m.idx))])
    box, radius = _box(m.pos)
    md.vertex_buffers = [vb]
    sh.vertex_buffer = 0
    sh.meshes = [mesh]
    sh.bounds = [box, box]
    sh.radii = [radius]
    sh.key_shapes = []
    sh.skin_bones = []
    md.shapes = [sh]
    mat = md.materials[sh.material]
    mat.textures = [tex_names.get(s.name, t) for s, t in zip(mat.samplers, mat.textures)]
    md.materials = [mat]
    sh.material = 0
    md.name = actor
    md.total_vertex_count = n
    res.name = actor
    try:
        out = nx_model.write(res)
    except (ModelError, struct.error, ValueError) as e:
        raise AkError(f"the AK-47 model can't be written ({e})") from None
    problems = checks.switch_model_problems(out)
    if problems:
        raise AkError(f"the AK-47 model fails its self-check: {'; '.join(map(str, problems[:3]))}")
    return out, res


def donor_sampler_textures(donor_raw: bytes) -> dict[str, str]:
    res = read_model(donor_raw)
    md = res.models[0]
    mat = md.materials[md.shapes[0].material]
    return {s.name: t for s, t in zip(mat.samplers, mat.textures)}


# ---------------------------------------------------------------------------------------------------- the icon

def draw_icon(m: WeaponMesh, alb: menu_bg.Picture, size: int, margin: float = 0.06) -> menu_bg.Picture:
    """The inventory icon: the weapon seen from its flat side, diagonal like the game's (hilt bottom left, tip top
    right), textured with the atlas and lit from the top left. Drawn at ICON_SUPERSAMPLE x and scaled down."""
    big = size * ICON_SUPERSAMPLE
    r2 = math.sqrt(0.5)
    scr = [((p[2] - p[0]) * r2, (-p[2] - p[0]) * r2, p[1]) for p in m.pos]   # (right, down, toward the viewer)
    xs, ys = [s[0] for s in scr], [s[1] for s in scr]
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    k = big * (1 - 2 * margin) / span
    ox = big / 2 - (max(xs) + min(xs)) / 2 * k
    oy = big / 2 - (max(ys) + min(ys)) / 2 * k
    pts = [(s[0] * k + ox, s[1] * k + oy, s[2]) for s in scr]
    light = _norm((0.75, 0.45, 0.5))
    color = bytearray(big * big * 4)
    depth = [-1e30] * (big * big)
    tw, th, tpx = alb.w, alb.h, alb.px
    for t in range(0, len(m.idx), 3):
        a, b, c = m.idx[t:t + 3]
        (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = pts[a], pts[b], pts[c]
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) < 1e-9:
            continue
        nrm = _norm(tuple(m.nrm[a][q] + m.nrm[b][q] + m.nrm[c][q] for q in range(3)))
        if nrm[1] < -0.05:   # (facing away from the viewer: the camera looks along -Y)
            continue
        shade = 0.5 + 0.5 * max(0.0, _dot(nrm, light))
        bx0, bx1 = max(0, int(min(x0, x1, x2))), min(big - 1, int(max(x0, x1, x2)) + 1)
        by0, by1 = max(0, int(min(y0, y1, y2))), min(big - 1, int(max(y0, y1, y2)) + 1)
        (u0, v0), (u1, v1), (u2, v2) = m.uv[a], m.uv[b], m.uv[c]
        inv = 1.0 / area
        for py in range(by0, by1 + 1):
            cy = py + 0.5
            for px in range(bx0, bx1 + 1):
                cx = px + 0.5
                w0 = ((x1 - cx) * (y2 - cy) - (x2 - cx) * (y1 - cy)) * inv
                w1 = ((x2 - cx) * (y0 - cy) - (x0 - cx) * (y2 - cy)) * inv
                w2 = 1.0 - w0 - w1
                if w0 < 0 or w1 < 0 or w2 < 0:
                    continue
                z = w0 * z0 + w1 * z1 + w2 * z2
                o = py * big + px
                if z <= depth[o]:
                    continue
                depth[o] = z
                u = w0 * u0 + w1 * u1 + w2 * u2
                v = w0 * v0 + w1 * v1 + w2 * v2
                ti = (min(th - 1, max(0, int(v * th))) * tw + min(tw - 1, max(0, int(u * tw)))) * 4
                q = o * 4
                color[q] = min(255, int(tpx[ti] * shade))
                color[q + 1] = min(255, int(tpx[ti + 1] * shade))
                color[q + 2] = min(255, int(tpx[ti + 2] * shade))
                color[q + 3] = 255
    pic = menu_bg.Picture(big, big, color)
    small = menu_bg.unpremultiply(menu_bg.resample(menu_bg.premultiply(pic), (0, 0, big, big), size, size))
    return small


def build_icon(donor_icon_raw: bytes, m: WeaponMesh, alb: menu_bg.Picture, actor: str) -> tuple[bytes, object]:
    """The inventory icon in the claymore's icon's format and size."""
    try:
        ts = nx_bfres.read_textures(donor_icon_raw)
    except ModelError as e:
        raise AkError(f"the claymore's icon can't be read ({e})") from None
    if len(ts.textures) != 1 or _block_kind(ts.textures[0].format) != "rgba8":
        raise AkError("the claymore's icon isn't one RGBA8 picture")
    d = ts.textures[0]
    pic = draw_icon(m, alb, d.width) if d.width == d.height else menu_bg.resample(
        draw_icon(m, alb, max(d.width, d.height)), (0, 0, max(d.width, d.height), max(d.width, d.height)),
        d.width, d.height)
    t = copy.deepcopy(d)
    t.name = actor
    t.levels = [bytes(pic.px)]
    out = copy.deepcopy(ts)
    out.name = ts.name.replace(d.name, actor) if d.name in ts.name else actor
    out.textures = [t]
    data = nx_bfres.write_textures(out)
    problems = checks.switch_tex_problems(data)
    if problems:
        raise AkError(f"the icon fails its self-check: {'; '.join(map(str, problems[:3]))}")
    return data, pic


# ---------------------------------------------------------------------------------------------------- the actor pack

def _set_text(obj, name: str, value: str) -> None:
    old = obj.get(aamp.name_hash(name))
    obj[aamp.name_hash(name)] = aamp.Param(old.type if old else aamp.STRING_REF, value)


def _aamp_edit(data: bytes, edit) -> bytes:
    root = aamp.load(data)
    edit(root)
    return aamp.dump(root, aamp.type_name(data) or "xml", struct.unpack_from("<I", data, 0x10)[0])


@dataclass
class Pack:
    data: bytes                    # as stored (Yaz0)
    raw_len: int                   # unpacked size
    inner: dict[str, bytes]        # the files we made, by name (for size entries)
    refs: dict[str, str]           # each of them -> the game file it was made from
    tags: list[str]                # the actor link's tags that are left


def actor_pack(files: dict[str, tuple[bytes, int]], actor: str, donor: str, drop_tags: list[str],
               name_jpn: str = "AK-47", yaz0_align: int = 0) -> Pack:
    """Our actor pack from the claymore's (name -> (bytes, alignment)): its actor link and model list renamed, the
    model list pointing at our model, the given tags dropped. Every other file stays as it is (they keep their
    names: the pack still holds them)."""
    link_name = f"Actor/ActorLink/{donor}.bxml"
    if link_name not in files:
        raise AkError(f"{donor}'s pack has no {link_name}")
    link = aamp.load(files[link_name][0])
    target = link.object("LinkTarget")
    if target is None:
        raise AkError(f"{donor}'s actor link has no LinkTarget")
    model_user = target.get_value("ModelUser")
    ml_name = f"Actor/ModelList/{model_user}.bmodellist"
    if ml_name not in files:
        raise AkError(f"{donor}'s pack has no {ml_name}")
    kept: list[str] = []

    def relink(root: aamp.PList) -> None:
        t = root.object("LinkTarget")
        _set_text(t, "ModelUser", actor)
        _set_text(t, "ActorNameJpn", name_jpn)
        tags = root.object("Tags")
        if tags is not None:
            names = [p.value for p in tags.values() if isinstance(p.value, str)]
            keep = [x for x in names if x not in drop_tags]
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
                _set_text(base, "Folder", actor)
                n += 1
            units = lst.list("Unit")
            for u in (units.objects.values() if units else []):
                if aamp.name_hash("UnitName") in u:
                    _set_text(u, "UnitName", actor)
        if not n:
            raise AkError(f"{ml_name} names no model folder")

    made = {f"Actor/ActorLink/{actor}.bxml": _aamp_edit(files[link_name][0], relink),
            f"Actor/ModelList/{actor}.bmodellist": _aamp_edit(files[ml_name][0], remodel)}
    refs = {f"Actor/ActorLink/{actor}.bxml": link_name, f"Actor/ModelList/{actor}.bmodellist": ml_name}
    items = [(n, d, a) for n, (d, a) in files.items() if n not in (link_name, ml_name)]
    for name, data in made.items():
        items.append((name, data, max(files[refs[name]][1], ukmm.new_alignment(name, data, 4))))
    raw = bnp.sarc_build(items)
    return Pack(ukmm.yaz0(raw, yaz0_align), len(raw), made, refs, kept)


def tag_hashes(tags: list[str]) -> dict:
    """The actor info "tags" map: tag<crc32 hex> -> crc32."""
    out = {}
    for t in tags:
        h = zlib.crc32(t.encode())
        out[f"tag{h:08x}"] = h if h < 1 << 31 else bd.U32(h)   # (as the game's: a signed int when it fits)
    return out


# ---------------------------------------------------------------------------------------------------- terrain

class Terrain:
    """The main field's terrain heights (Terrain/A/MainField.tscb: the areas, each a 256x256 height map of
    little-endian u16 in a .hght file inside <name>.hght.sstera packs; height = value / 65535 x the table's top)."""

    def __init__(self, romfs: str):
        self.base = os.path.join(romfs, "Terrain", "A", "MainField")
        path = os.path.join(romfs, "Terrain", "A", "MainField.tscb")
        try:
            with open(path, "rb") as f:
                d = f.read()
        except OSError as e:
            raise AkError(f"Your game's terrain table can't be read ({e.strerror or e}).") from None
        self.areas = parse_tscb(d)
        self.top = struct.unpack_from(">f", d, 0x14)[0]
        self._maps: dict[str, bytes | None] = {}

    def _height_map(self, name: str) -> bytes | None:
        if name not in self._maps:
            self._maps[name] = None
            try:
                idx = int(name[2:], 16)
            except ValueError:
                return None
            for pack in dict.fromkeys((f"{name[:2]}{idx & ~3:0{len(name) - 2}X}", name)):
                p = os.path.join(self.base, pack + ".hght.sstera")
                if not os.path.isfile(p):
                    continue
                try:
                    with open(p, "rb") as f:
                        files = bnp.Blob(f.read()).open()
                except (OSError, wc.FormatError, bnp.BnpError, struct.error, IndexError):
                    continue
                hit = files.get(name + ".hght")
                if hit and len(hit[0]) == 256 * 256 * 2:
                    self._maps[name] = hit[0]
                    break
        return self._maps[name]

    def height(self, x: float, z: float) -> tuple[float, float]:
        """(the ground height at x, z, the size of the area it came from in metres)."""
        best = None
        for ax, az, size, name in self.areas:
            if abs(x - ax) <= size / 2 and abs(z - az) <= size / 2 and (best is None or size < best[2]):
                if self._height_map(name) is not None:
                    best = (ax, az, size, name)
        if best is None:
            raise AkError(f"your terrain files have no height at x={x:.0f}, z={z:.0f}")
        ax, az, size, name = best
        data = self._maps[name]
        fx = (x - (ax - size / 2)) / size * 255
        fz = (z - (az - size / 2)) / size * 255
        i0, j0 = max(0, min(254, int(fx))), max(0, min(254, int(fz)))
        tx, tz = min(1.0, max(0.0, fx - i0)), min(1.0, max(0.0, fz - j0))

        def g(i, j):
            return struct.unpack_from("<H", data, 2 * (j * 256 + i))[0] * self.top / 65535
        h = (g(i0, j0) * (1 - tx) + g(i0 + 1, j0) * tx) * (1 - tz) + (g(i0, j0 + 1) * (1 - tx)
                                                                     + g(i0 + 1, j0 + 1) * tx) * tz
        return h, size


def parse_tscb(d: bytes) -> list[tuple[float, float, float, str]]:
    """The areas of a terrain table: (centre x, centre z, size, file name) in metres (big-endian; the table's
    coordinates are in units of its world scale)."""
    try:
        if d[:4] != b"TSCB":
            raise AkError("Your game's terrain table isn't a TSCB file")
        scale = struct.unpack_from(">f", d, 0x10)[0]
        count = struct.unpack_from(">I", d, 0x1C)[0]
        tab = 0x30 + struct.unpack_from(">I", d, 0x30)[0]
        out = []
        for i in range(min(count, 100000)):
            p = tab + 4 * i
            o = p + struct.unpack_from(">I", d, p)[0]
            x, z, size = struct.unpack_from(">3f", d, o)
            s = o + 0x20 + struct.unpack_from(">I", d, o + 0x20)[0]
            end = d.index(b"\0", s, s + 64)
            out.append((x * scale, z * scale, size * scale, d[s:end].decode("ascii")))
        return out
    except (struct.error, ValueError, UnicodeDecodeError) as e:
        raise AkError(f"Your game's terrain table can't be read ({e})") from None


def ground_spread(t: Terrain, x: float, z: float, r: float = 1.2) -> float:
    hs = [t.height(x, z)[0]] + [t.height(x + r * math.cos(a), z + r * math.sin(a))[0]
                                for a in (k * math.pi / 4 for k in range(8))]
    return max(hs) - min(hs)


# ---------------------------------------------------------------------------------------------------- the game's files

FILES = {   # what build and check read, in extracted\romfs ({donor} filled in)
    "pack": "Actor/Pack/{donor}.sbactorpack",
    "model": "Model/{donor}.sbfres",
    "tex": "Model/{donor}.Tex.sbfres",
    "icon": "UI/StockItem/{donor}.sbitemico",
    "actorinfo": "Actor/ActorInfo.product.sbyml",
    "bootup": "Pack/Bootup.pack",
    "titlebg": "Pack/TitleBG.pack",
    "rstb": "System/Resource/ResourceSizeTable.product.srsizetable",
    "tscb": "Terrain/A/MainField.tscb",
}
MESSAGES = "Pack/Bootup_USen.pack"
MSG_FILE = "ActorType/WeaponLargeSword"


def _unyaz0(data: bytes) -> bytes:
    return wc.yaz0_decompress(data)[0] if data[:4] == b"Yaz0" else data


def _yaz0_align(data: bytes) -> int:
    return struct.unpack_from(">I", data, 8)[0] if data[:4] == b"Yaz0" else 0


class Game:
    """The player's game files (extracted\\romfs, only read), each read once."""

    def __init__(self, install: str, donor: str):
        self.install = install
        self.extracted = os.path.join(install, "extracted")
        self.romfs = os.path.join(self.extracted, "romfs")
        self.donor = donor
        self._cache: dict[str, object] = {}

    def rel(self, key: str) -> str:
        return FILES[key].format(donor=self.donor)

    def path(self, rel: str) -> str:
        return os.path.join(self.romfs, *rel.split("/"))

    def missing(self) -> list[str]:
        return [self.rel(k) for k in FILES if not os.path.isfile(self.path(self.rel(k)))]

    def read(self, rel: str) -> bytes:
        if rel in self._cache:
            return self._cache[rel]
        try:
            with open(self.path(rel), "rb") as f:
                self._cache[rel] = f.read()
        except OSError as e:
            raise AkError(f"Your game's {rel} can't be read ({e.strerror or e}).") from None
        return self._cache[rel]

    def raw(self, key: str) -> bytes:
        return self.read(self.rel(key))

    def _get(self, key: str, make):
        if key not in self._cache:
            self._cache[key] = make()
        return self._cache[key]

    def pack(self) -> dict[str, tuple[bytes, int]]:
        def make():
            try:
                return {n: (d, a) for n, (d, a) in bnp.Blob(self.raw("pack")).open().items()}
            except (wc.FormatError, struct.error, IndexError) as e:
                raise AkError(f"{self.rel('pack')} can't be read ({e})") from None
        return self._get("pack:", make)

    def actorinfo(self) -> dict[str, dict]:
        def make():
            root = bd.byml_load(_unyaz0(self.raw("actorinfo")))
            return {a["name"]: a for a in root.get("Actors", []) if isinstance(a, dict) and isinstance(a.get("name"),
                                                                                                     str)}
        return self._get("actorinfo:", make)

    def bootup(self) -> bnp.Blob:
        return self._get("bootup:", lambda: bnp.Blob(self.raw("bootup")))

    def flags(self) -> dict[str, dict]:
        """The game's bool flags by name."""
        def make():
            gd = self.bootup().child("GameData/gamedata.ssarc")
            if gd is None:
                raise AkError("Your game's Bootup.pack has no GameData/gamedata.ssarc")
            out = {}
            for name in gd.open():
                if "bool_data" not in name or "array" in name:
                    continue
                root = bd.byml_load(gd.child(name).raw)
                for rows in (root.values() if isinstance(root, dict) else []):
                    for r in rows if isinstance(rows, list) else []:
                        if isinstance(r, dict) and isinstance(r.get("DataName"), str):
                            out[r["DataName"]] = r
            return out
        return self._get("flags:", make)

    def rstb(self) -> bnp.Rstb | None:
        return self._get("rstb:", lambda: bnp.load_game_rstb(self.extracted))

    def terrain(self) -> Terrain:
        return self._get("terrain:", lambda: Terrain(self.romfs))

    def unit(self, unit: str):
        """A loose map unit ("D-6_Dynamic") as typed values; a static one is read from TitleBG.pack."""
        def make():
            target = bm.map_target(unit)
            if "//" in target:
                raw = _unyaz0(self.raw("titlebg"))
                s = wc.sarc_parse(raw)
                inner = target.split("//", 1)[1]
                node = next((x for x in s.nodes if x.name == inner), None)
                if node is None:
                    raise AkError(f"Your game's TitleBG.pack has no {inner}")
                return bm.byml_load(raw[s.data_offset + node.start:s.data_offset + node.end])
            return bm.byml_load(self.read(target))
        return self._get("unit:" + unit, make)

    def hash_ids(self, log=None) -> set[int]:
        """Every HashId in the main field's map units (statics in TitleBG.pack and the loose dynamics)."""
        def make():
            _log(log, "Reading every main field map unit for the HashIds in use (about 10 s)...")
            ids: set[int] = set()

            def add(root):
                for o in ((root.get("Objs") or []) + (root.get("Rails") or [])) if isinstance(root, dict) else []:
                    h = o.get("HashId") if isinstance(o, dict) else None
                    if isinstance(h, int):
                        ids.add(h & 0xFFFFFFFF)
            raw = _unyaz0(self.raw("titlebg"))
            s = wc.sarc_parse(raw)
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
        return self._get("hash_ids:", make)

    def message_labels(self) -> set[str] | None:
        """The labels of the English weapon texts (None when they can't be read)."""
        try:
            files = bnp.Blob(self.read(MESSAGES)).child("Message/Msg_USen.product.ssarc").open()
            return set(bt.msbt_read(files[f"{MSG_FILE}.msbt"][0]).labels)
        except (AkError, AttributeError, KeyError, wc.FormatError, bt.TextError, struct.error, ValueError):
            return None


def find_game(install: str | None, lib: mm.Library | None, donor: str) -> Game:
    """The player's game: --game, else the library's Savage Wind folder, else the default one. Only its
    extracted\\romfs is read."""
    install = install or (lib.install if lib else None) or mm.default_install()
    game = Game(os.path.abspath(install), donor)
    if not os.path.isdir(game.romfs):
        raise AkError(f"Savage Wind's game files aren't in {game.romfs}. Set them up in Savage Wind first, or pass "
                      "--game <Savage Wind folder>.")
    gone = game.missing()
    if gone:
        raise AkError("Your game files lack what the AK-47 is built from: " + ", ".join(gone))
    return game


# ---------------------------------------------------------------------------------------------------- logs

def _hid(key: str, used: set[int]) -> bd.U32:
    """A HashId for our object: from its name, so a rebuild gives the same one; never one in use."""
    h = zlib.crc32(f"giruka.ak47/{key}".encode())
    while h in used or h == 0:
        h = (h + 1) & 0xFFFFFFFF
    used.add(h)
    return bd.U32(h)


def _signed(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >= 1 << 31 else v


def placed_object(actor: str, spot: dict, ground: float, used: set[int]) -> dict:
    hid = _hid("hilltop", set(used))
    pos = [bm.f32(spot["x"]), bm.f32(ground + spot["lift"]), bm.f32(spot["z"])]
    yaw = bm.f32(math.radians(spot["yaw"]))
    return {"!Parameters": dict(PLACED_PARAMS), "HashId": hid, "Rotate": [bm.f32(0.0), yaw, bm.f32(0.0)],
            "SRTHash": _signed(zlib.crc32(f"{actor}:{pos}:{yaw}".encode())), "Translate": pos,
            "UnitConfigName": actor}


def flag_entry(name: str, like: dict | None, **over) -> dict:
    e = copy.deepcopy(like) if isinstance(like, dict) else dict(FLAG_DEFAULT)
    e.update(over)
    e["DataName"] = name
    e["HashValue"] = _signed(zlib.crc32(name.encode()))
    return dict(sorted(e.items()))


def flags_logs(actor: str, donor: str, hash_id: int, game_flags: dict[str, dict]) -> tuple[dict, dict]:
    """(gamedata.yml, savedata.yml): the item's IsGet flag and the placed one's pick-up flag, shaped like the
    claymore's own (IsGet: once; MainField_...: reset by the Blood Moon)."""
    isget_like = game_flags.get(f"IsGet_{donor}")
    placed_like = next((v for k, v in sorted(game_flags.items()) if k.startswith(f"MainField_{donor}_")), None)
    isget = flag_entry(f"IsGet_{actor}", isget_like, **({} if isget_like else {"IsOneTrigger": True}))
    placed = flag_entry(f"MainField_{actor}_{int(hash_id)}", placed_like,
                        **({} if placed_like else {"ResetType": 1}))
    gd = {"bool_data": {"add": {isget["DataName"]: isget, placed["DataName"]: placed}, "del": []}}
    sd = {"add": [{"DataName": e["DataName"], "HashValue": e["HashValue"]} for e in (isget, placed)], "del": []}
    return gd, sd


def texts_log(actor: str, texts: dict) -> dict:
    """BCML's texts.json: the weapon's name and description (English; the other languages get them too)."""
    desc = texts["desc"]
    return {"USen": {f"{MSG_FILE}.msyt": {
        f"{actor}_Name": {"contents": [{"text": texts["name"]}]},
        f"{actor}_Desc": {"contents": [{"text": desc}]},
        f"{actor}_PictureBook": {"contents": [{"text": texts.get("picture_book", desc)}]}}}}


def actorinfo_entry(src: dict, actor: str, lo: tuple, hi: tuple, tags: list[str], used_sort: set[int]) -> dict:
    e = copy.deepcopy(src)
    e["name"] = actor
    e["bfres"] = actor
    e["mainModel"] = actor
    e["aabbMin"] = {"X": bd.f32(lo[0]), "Y": bd.f32(lo[1]), "Z": bd.f32(lo[2])}
    e["aabbMax"] = {"X": bd.f32(hi[0]), "Y": bd.f32(hi[1]), "Z": bd.f32(hi[2])}
    if "tags" in e:
        e["tags"] = tag_hashes(tags)
    if isinstance(e.get("sortKey"), int):
        k = e["sortKey"] + 1
        while k in used_sort:
            k += 1
        e["sortKey"] = k
    return e


def check_logs(logs: dict[str, str]) -> None:
    """Read our logs back with the mergers modman uses: one they'd refuse is refused here, before it ships."""
    for name, text in logs.items():
        data = text.encode("utf-8")
        try:
            if name == "texts.json":
                got = bt.read_log(name, data, "logs/")
                if got.notes:
                    raise AkError("; ".join(got.notes))
            elif name in bm.LOGS:
                bm.read_log(name, data)
            elif name in bd.LOGS:
                bd.read_log(name, data)
            elif name == "rstb.json":
                bnp.read_rstb_json(data, f"logs/{name}")
        except (bt.LogError, bm.LogError, bd.LogError, bnp.BnpError) as e:
            raise AkError(f"the AK-47's {name} would be refused by modman ({e})") from None


# ---------------------------------------------------------------------------------------------------- building

@dataclass
class Built:
    folder: str                        # the item mod's folder (giruka.ak47)
    spot_folder: str = ""              # the spot mod's folder (giruka.ak47-spot)
    spot: tuple = ()                   # (x, y, z) where it lies
    ground: float = 0.0
    vertices: int = 0
    triangles: int = 0
    hash_id: int = 0
    notes: list[str] = field(default_factory=list)


def _write(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(data, (bytes, bytearray)) else "w"
    with open(path, mode, **({} if mode == "wb" else {"encoding": "utf-8", "newline": "\n"})) as f:
        f.write(data)


def _estimate(rstb: bnp.Rstb | None, ref_name: str, ref_len: int, new_len: int, pack: bool,
              notes: list[str], what: str) -> int | None:
    g = rstb.get(ref_name) if rstb else None
    if g is None:
        notes.append(f"no size entry for {what}: the game's table has none for {ref_name}")
        return None
    return ukmm.estimate_entry(ref_len, g, new_len, pack)


def model_check(recipe: Recipe, model_path: str | None = None) -> None:
    """The model file is the one the recipe names (its SHA-256), when it is the recipe's own file."""
    path = model_path or recipe.model_path
    want = recipe.model.get("sha256")
    if model_path is None and isinstance(want, str):
        with open(path, "rb") as f:
            got = hashlib.sha256(f.read()).hexdigest()
        if got != want:
            raise AkError(f"{path} isn't the model the recipe was made for (its SHA-256 differs)")


def make_weapon(game: Game, recipe: Recipe, model_path: str | None = None, log=None):
    """(WeaponMesh in the claymore's space, the claymore's model bytes)."""
    path = model_path or recipe.model_path
    model_check(recipe, model_path)
    _log(log, f"Reading the model ({os.path.basename(path)})...")
    glb = read_model_file(path)
    donor_raw = _unyaz0(game.raw("model"))
    res = read_model(donor_raw)
    raw = gather(glb)
    m = to_weapon(raw, recipe.model["forward"], recipe.model["up"], donor_hilt(res),
                  donor_length(res) * recipe.model["length"], bool(recipe.model.get("flip")))
    return m, donor_raw


def _package_files(folder: str, toml_text: str, manifest: dict, logs: dict[str, str], built: list[str]) -> None:
    """A package folder's own files: logs/, mod.toml, info.json and BUILT.md (what was made, for developers)."""
    for name, text in logs.items():
        _write(os.path.join(folder, "logs", name), text)
    _write(os.path.join(folder, "mod.toml"), toml_text)
    _write(os.path.join(folder, "info.json"), json.dumps({
        "name": manifest.get("name", manifest["id"]), "version": manifest["version"],
        "desc": manifest.get("description", ""), "platform": "switch", "options": {}}, indent=1) + "\n")
    _write(os.path.join(folder, OUT_FILE), "\n".join(built + [""]))


def build_package(game: Game, recipe: Recipe, out: str, *, model_path: str | None = None, log=None,
                  used: set[int] | None = None, verbose: bool = False) -> Built:
    """Write the AK-47's two mods (BCML-style package folders: info.json, mod.toml, logs/, the new files) into `out`
    (a new or empty folder): out/<item id> (the model, textures, icon, actor pack, actor info, flags, texts, sizes)
    and out/<spot id> (map.yml: where one lies). used: more HashIds to keep clear of (other library mods' map
    changes). verbose: the log names where it lies. Reads only the player's game files and the model."""
    if os.path.isdir(out) and os.listdir(out):
        raise AkError(f"{out} isn't empty")
    actor, donor = recipe.actor, recipe.donor
    ai = game.actorinfo()
    if donor not in ai:
        raise AkError(f"Your game's actor info has no {donor}")
    if actor in ai:
        raise AkError(f"Your game already has an actor called {actor}")
    item_dir = os.path.join(out, recipe.manifest["id"])
    spot_dir = os.path.join(out, recipe.spot_manifest["id"])
    romfs = os.path.join(item_dir, BASE_TID, "romfs")
    res = Built(item_dir, spot_dir)
    rstb = game.rstb()
    if rstb is None:
        res.notes.append("the game's size table isn't there: no size entries for the new files")
    rstb_json: dict[str, int] = {}

    def size(name, ref, ref_len, new_len, pack):
        v = _estimate(rstb, ref, ref_len, new_len, pack, res.notes, name)
        if v:
            rstb_json[name] = v

    # the model and its textures
    m, donor_raw = make_weapon(game, recipe, model_path, log)
    res.notes += m.notes
    res.vertices, res.triangles = len(m.pos), len(m.idx) // 3
    tex_raw = _unyaz0(game.raw("tex"))
    try:
        donor_ts = nx_bfres.read_textures(tex_raw)
    except ModelError as e:
        raise AkError(f"the claymore's textures can't be read ({e})") from None
    ts, tex_names, alb = make_textures(m, donor_ts, donor_sampler_textures(donor_raw), actor, log)
    _log(log, f"Writing the model ({res.vertices} vertices, {res.triangles} triangles)...")
    model_bytes, _ = build_model(donor_raw, m, actor, tex_names)
    tex_bytes = nx_bfres.write_textures(ts)
    problems = checks.switch_tex_problems(tex_bytes)
    if problems:
        raise AkError(f"the AK-47 textures fail their self-check: {'; '.join(map(str, problems[:3]))}")
    _write(os.path.join(romfs, "Model", f"{actor}.sbfres"), ukmm.yaz0(model_bytes, _yaz0_align(game.raw("model"))))
    _write(os.path.join(romfs, "Model", f"{actor}.Tex.sbfres"), ukmm.yaz0(tex_bytes, _yaz0_align(game.raw("tex"))))
    size(f"Model/{actor}.bfres", f"Model/{donor}.bfres", len(donor_raw), len(model_bytes), False)
    size(f"Model/{actor}.Tex.bfres", f"Model/{donor}.Tex.bfres", len(tex_raw), len(tex_bytes), False)

    # the icon
    _log(log, "Drawing the inventory icon...")
    icon_raw = _unyaz0(game.raw("icon"))
    icon_bytes, _pic = build_icon(icon_raw, m, alb, actor)
    _write(os.path.join(romfs, "UI", "StockItem", f"{actor}.sbitemico"), ukmm.yaz0(icon_bytes,
                                                                                  _yaz0_align(game.raw("icon"))))
    size(f"UI/StockItem/{actor}.bitemico", f"UI/StockItem/{donor}.bitemico", len(icon_raw), len(icon_bytes), False)

    # the actor pack
    _log(log, f"Building {actor}'s actor pack from {donor}'s...")
    files = game.pack()
    pk = actor_pack(files, actor, donor, recipe.drop_tags, recipe.texts["name"], _yaz0_align(game.raw("pack")))
    _write(os.path.join(romfs, "Actor", "Pack", f"{actor}.sbactorpack"), pk.data)
    size(f"Actor/Pack/{actor}.bactorpack", f"Actor/Pack/{donor}.bactorpack", ukmm.unpacked_size(game.raw("pack")),
         pk.raw_len, True)
    for name, data in pk.inner.items():
        size(name, pk.refs[name], len(files[pk.refs[name]][0]), len(data), False)

    # actor info
    lo, hi = m.bounds()
    used_sort = {a.get("sortKey") for a in ai.values() if isinstance(a.get("sortKey"), int)}
    entry = actorinfo_entry(ai[donor], actor, lo, hi, pk.tags, used_sort)
    logs = {"actorinfo.yml": bd.to_text({str(zlib.crc32(actor.encode())): entry})}

    # where it lies (the spot mod)
    spot = recipe.spot
    _log(log, "Reading your terrain at its spot" + (f" (x={spot['x']:.0f}, z={spot['z']:.0f})" if verbose else "")
         + "...")
    ground, area = game.terrain().height(spot["x"], spot["z"])
    if abs(ground - spot["ground"]) > GROUND_TOLERANCE:
        raise AkError(f"your terrain at the AK-47's spot is {ground:.1f} m high, not about {spot['ground']:.1f} m "
                      "as the recipe says: the hill isn't where it should be")
    spread = ground_spread(game.terrain(), spot["x"], spot["z"])
    if spread > MAX_SLOPE_SPREAD:
        res.notes.append(f"the ground at the spot rises {spread:.2f} m within 1.2 m: the AK-47 may slide")
    unit = game.unit(spot["unit"])
    if not isinstance(unit, dict) or not isinstance(unit.get("Objs"), list):
        raise AkError(f"Your game's {spot['unit']} map unit can't be read")
    taken = set(game.hash_ids(log)) | set(used or ())
    obj = placed_object(actor, spot, ground, taken)
    res.hash_id = int(obj["HashId"])
    res.ground = ground
    res.spot = tuple(float(v) for v in obj["Translate"])
    spot_logs = {"map.yml": bd.to_text({spot["unit"]: {"Objs": {"add": [obj]}}})}

    # flags (the placed one's too: the item's, so a save that picked it up keeps its flag), texts, sizes
    gd, sd = flags_logs(actor, donor, res.hash_id, game.flags())
    logs["gamedata.yml"] = bd.to_text(gd)
    logs["savedata.yml"] = bd.to_text(sd)
    logs["texts.json"] = json.dumps(texts_log(actor, recipe.texts), indent=1, ensure_ascii=False) + "\n"
    logs["rstb.json"] = json.dumps(dict(sorted(rstb_json.items())), indent=1) + "\n"
    check_logs(logs)
    check_logs(spot_logs)
    head = "# AK-47: built on this PC by tools/ak47_build.py from the player's own game files and assets/ak47's model."
    _package_files(item_dir, recipe.toml_text, recipe.manifest, logs, [
        head, "", f"- actor: {actor} (from {donor})", f"- model: {res.vertices} vertices, {res.triangles} triangles",
        f"- where it lies: the mod {recipe.spot_manifest['id']}"])
    _package_files(spot_dir, recipe.spot_toml_text, recipe.spot_manifest, spot_logs, [
        head, "", f"- placed in {spot['unit']} at x={res.spot[0]:.2f} y={res.spot[1]:.2f} z={res.spot[2]:.2f} "
        f"(ground {ground:.2f} m, terrain area {area:.1f} m), HashId {res.hash_id}",
        f"- part of: {recipe.spot_manifest['part_of']}"])
    return res


def map_hash_ids(lib: mm.Library | None, mods: list[dict]) -> dict[int, str]:
    """The HashIds these library mods' map changes add (a BCML package's .bnp/logs/map.yml and its options'):
    HashId -> the mod's name. A log that can't be read adds none (deploy names it)."""
    out: dict[int, str] = {}
    for m in mods if lib is not None else []:
        root = os.path.join(lib.mods_dir, m["id"])
        info = bnp.stored(root)
        if not info:
            continue
        try:
            data = bnp.package_logs(root, info, {"map.yml": bm})[0]
        except Exception:  # noqa: BLE001 (one mod's damaged logs: never a reason to stop)
            continue
        for diff in data.get("map.yml", []):
            for unit in diff.values() if isinstance(diff, dict) else []:
                for sec in unit.values() if isinstance(unit, dict) else []:
                    for o in ((sec.get("add") or []) if isinstance(sec, dict) else []):
                        h = o.get("HashId") if isinstance(o, dict) else None
                        if isinstance(h, int):
                            out.setdefault(int(h) & 0xFFFFFFFF, m.get("name") or m["id"])
    return out


def other_hash_ids(lib: mm.Library | None, recipe: Recipe) -> dict[int, str]:
    """map_hash_ids of every library mod but the AK-47's own two (on or off: one turned on later counts too)."""
    ours = {recipe.manifest["id"], recipe.spot_manifest["id"]}
    return map_hash_ids(lib, [m for m in lib.mods if m["id"] not in ours]) if lib is not None else {}


def add_to_library(lib: mm.Library, folder: str, mod_id: str, log=None) -> tuple[dict, list[str]]:
    """The built folder into the library through modman's normal add (the old copy removed first, its on/off and
    place in the load order kept; a new one goes last, on)."""
    idx = next((i for i, m in enumerate(lib.mods) if m["id"] == mod_id), None)
    was_on = lib.mods[idx]["enabled"] if idx is not None else True
    if idx is not None:
        mm.remove_mod(lib, mod_id)
    elif os.path.isdir(os.path.join(lib.mods_dir, mod_id)):
        raise AkError(f"{os.path.join(lib.mods_dir, mod_id)} is there but the library doesn't list it: move that "
                      "folder away and build again.")
    _log(log, "Adding it to your mod library (modman add: the logs are merged into your game's files)...")
    try:
        entry, _scan, notes = mm.add_mod(lib, folder)
    except mm.ModmanError as e:
        raise AkError(str(e)) from None
    i = next(i for i, m in enumerate(lib.mods) if m["id"] == entry["id"])
    m = lib.mods.pop(i)
    m["enabled"] = was_on
    lib.mods.insert(idx if idx is not None else len(lib.mods), m)
    lib.save()
    return m, notes


def build(library: str | None = None, *, install: str | None = None, out: str | None = None, add: bool = True,
          model_path: str | None = None, recipe_dir: str = RECIPE, log=None, verbose: bool = False) -> dict:
    """Build the AK-47's two mods and (add=True) put them in the library. Returns {"built": Built, "entry": the
    item's library entry, "spot_entry", "notes"}."""
    recipe = load_recipe(recipe_dir)
    root = mm.resolve_library(library)
    lib = mm.Library.load(root)
    if add and lib is None:
        raise AkError(f"No mod library in {root} yet. Run: python tools/modman.py init")
    game = find_game(install, lib, recipe.donor)
    tmp = None
    if out is None:
        tmp = tempfile.mkdtemp(prefix="ak47-build-")
        out = os.path.join(tmp, "out")
    out = os.path.abspath(out)
    try:
        built = build_package(game, recipe, out, model_path=model_path, log=log,
                              used=set(other_hash_ids(lib, recipe)), verbose=verbose)
        entry = spot_entry = None
        notes: list[str] = []
        if add:
            entry, n1 = add_to_library(lib, built.folder, recipe.manifest["id"], log)
            spot_entry, n2 = add_to_library(lib, built.spot_folder, recipe.spot_manifest["id"], log)
            notes = n1 + n2
        return {"built": built, "entry": entry, "spot_entry": spot_entry, "notes": notes}
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------------------------------- check

def check(library: str | None = None, *, install: str | None = None, recipe_dir: str = RECIPE,
          log=None) -> list[str]:
    """Offline checks on the player's files (reads only): the new names are free, the claymore's files are there and
    can be read, the model builds and passes the writers' self-checks, the hilltop is where the recipe says (the
    terrain's height, a gentle top, nothing the game places, static or dynamic, within NEAR_RADIUS m), the spot's
    HashId is used neither by the game's map nor by another library mod's map changes. Returns the problems (empty:
    all good)."""
    recipe = load_recipe(recipe_dir)
    lib = mm.Library.load(mm.resolve_library(library))
    game = find_game(install, lib, recipe.donor)
    out: list[str] = []
    actor, donor = recipe.actor, recipe.donor
    ai = game.actorinfo()
    if donor not in ai:
        out.append(f"actor {donor} isn't in your game's actor info")
    elif ai[donor].get("profile") != "WeaponLargeSword":
        out.append(f"{donor} isn't a two-handed weapon ({ai[donor].get('profile')})")
    if actor in ai:
        out.append(f"your game's actor info already has {actor}")
    if os.path.isfile(game.path(f"Actor/Pack/{actor}.sbactorpack")):
        out.append(f"your game already has Actor/Pack/{actor}.sbactorpack")
    rstb = game.rstb()
    for name in (f"Actor/Pack/{actor}.bactorpack", f"Model/{actor}.bfres"):
        if rstb is not None and rstb.get(name) is not None:
            out.append(f"your game's size table already has {name}")
    flags = game.flags()
    for f in (f"IsGet_{actor}",):
        if f in flags:
            out.append(f"your game already has the flag {f}")
    labels = game.message_labels()
    if labels is None:
        out.append("your English weapon texts can't be read")
    elif f"{actor}_Name" in labels:
        out.append(f"your English texts already have {actor}_Name")
    try:
        m, donor_raw = make_weapon(game, recipe, None, log)
        ts, names, alb = make_textures(m, nx_bfres.read_textures(_unyaz0(game.raw("tex"))),
                                       donor_sampler_textures(donor_raw), actor)
        build_model(donor_raw, m, actor, names)
        build_icon(_unyaz0(game.raw("icon")), m, alb, actor)
        pk = actor_pack(game.pack(), actor, donor, recipe.drop_tags)
        lost = [t for t in recipe.drop_tags if t in pk.tags]
        if lost:
            out.append(f"tags not dropped: {', '.join(lost)}")
    except (AkError, ModelError) as e:
        out.append(f"the weapon can't be built: {e}")
    spot = recipe.spot
    try:
        t = game.terrain()
        h, _ = t.height(spot["x"], spot["z"])
        if abs(h - spot["ground"]) > GROUND_TOLERANCE:
            out.append(f"the terrain at the spot is {h:.1f} m high, not about {spot['ground']:.1f} m")
        sp = ground_spread(t, spot["x"], spot["z"])
        if sp > MAX_SLOPE_SPREAD:
            out.append(f"the ground at the spot rises {sp:.2f} m within 1.2 m (too steep for a weapon to lie)")
        for r in (8.0, 20.0):
            ring = [t.height(spot["x"] + r * math.cos(a), spot["z"] + r * math.sin(a))[0]
                    for a in (k * math.pi / 8 for k in range(16))]
            if max(ring) > h + 1.0:
                out.append(f"the spot isn't a hilltop: the ground {r:.0f} m away is {max(ring) - h:.1f} m higher")
    except AkError as e:
        out.append(str(e))
    # nothing placed near it: the loose dynamic unit and the static one (trees, rocks: in TitleBG.pack)
    for unit_name in (spot["unit"], spot["unit"].rsplit("_", 1)[0] + "_Static"):
        try:
            unit = game.unit(unit_name)
            for o in (unit.get("Objs") or []) if isinstance(unit, dict) else []:
                tr = o.get("Translate") if isinstance(o, dict) else None
                if isinstance(tr, list) and len(tr) == 3 and \
                        math.hypot(tr[0] - spot["x"], tr[2] - spot["z"]) < NEAR_RADIUS:
                    out.append(f"{o.get('UnitConfigName')} ({unit_name}) stands within {NEAR_RADIUS:g} m of the spot")
        except (AkError, OSError, bm.LogError, wc.FormatError, struct.error, IndexError) as e:
            out.append(f"the map unit {unit_name} can't be read ({e})")
    # the spot's HashId: the built spot mod's (else the one a build would pick) against the game's map and the
    # other library mods' map changes
    used = game.hash_ids(log)
    others = other_hash_ids(lib, recipe)
    spot_mod = [m for m in (lib.mods if lib is not None else []) if m["id"] == recipe.spot_manifest["id"]]
    mine = set(map_hash_ids(lib, spot_mod)) if spot_mod else \
        {int(placed_object(actor, spot, spot["ground"], set(used) | set(others))["HashId"])}
    for h in sorted(mine):
        if h in used:
            out.append(f"HashId {h} of the spot is in your game's map too: build it again")
        if h in others:
            out.append(f"HashId {h} of the spot is added by {others[h]} too: build it again")
        if f"MainField_{actor}_{h}" in flags:
            out.append("the placed AK-47's flag is already in your game data")
    return out


# ---------------------------------------------------------------------------------------------------- our own wood

# The downloaded model's stock had a wood photo of unknown source; ours is drawn here (rewood put it in
# assets/ak47/AK-47.glb). Horizontal grain like the photo's: growth rings bent by low noise, long streaks, PSX-style
# speckle. Pure Python and the same picture every time (integer hashing, no random module).
WOOD_LIGHT = (198, 116, 52)
WOOD_DARK = (128, 64, 24)


def _hash01(ix: int, iy: int, seed: int) -> float:
    h = (ix * 374761393 + iy * 668265263 + seed * 2246822519) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFF) / 65535.0


def _noise(x: float, y: float, seed: int) -> float:
    """Value noise in 0..1 (smooth between the lattice points)."""
    x0, y0 = math.floor(x), math.floor(y)
    fx, fy = x - x0, y - y0
    sx, sy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a, b = _hash01(x0, y0, seed), _hash01(x0 + 1, y0, seed)
    c, d = _hash01(x0, y0 + 1, seed), _hash01(x0 + 1, y0 + 1, seed)
    return (a + (b - a) * sx) + ((c + (d - c) * sx) - (a + (b - a) * sx)) * sy


def wood_picture(w: int, h: int, seed: int = 47) -> menu_bg.Picture:
    """A w x h wood texture (RGBA, opaque), the grain running left to right."""
    px = bytearray(w * h * 4)
    for y in range(h):
        v = y / h
        for x in range(w):
            u = x / w
            warp = _noise(u * 2.0, v * 1.5, seed) * 0.7 + _noise(u * 6.0, v * 4.0, seed + 1) * 0.3
            rings = v * 8.0 + warp * 1.1 + 0.12 * math.sin(u * 3.3 + v * 1.7)
            r = rings - math.floor(rings)
            line = math.exp(-((r - 0.5) / 0.05) ** 2)                      # a thin dark ring line
            streak = 0.6 * _noise(u * 2.5, v * 90.0, seed + 2) + 0.4 * _noise(u * 7.0, v * 170.0, seed + 5)
            t = min(1.0, 0.42 * line + 0.5 * streak ** 1.5 + 0.18 * _noise(u * 1.2, v * 1.5, seed + 3))
            speck = (_hash01(x, y, seed + 4) - 0.5) * 22.0                 # PSX-style speckle, like the photo's
            k = (y * w + x) * 4
            for c in range(3):
                px[k + c] = max(0, min(255, round(WOOD_LIGHT[c] + (WOOD_DARK[c] - WOOD_LIGHT[c]) * t + speck)))
            px[k + 3] = 255
    return menu_bg.Picture(w, h, px)


def replace_image(data: bytes, which, png: bytes, name: str | None = None) -> bytes:
    """The .glb `data` with image `which` (its name, or its number) holding `png` instead; every buffer view laid out
    again (4-byte aligned, in their order), so nothing of the old picture is left in the file. Records what was
    changed in the file's asset copyright line."""
    g = Glb(data)
    doc = copy.deepcopy(g.doc)
    imgs, views = doc.get("images") or [], doc.get("bufferViews") or []
    k = which if isinstance(which, int) else next((i for i, im in enumerate(imgs) if im.get("name") == which), None)
    if k is None or not 0 <= k < len(imgs) or not isinstance(imgs[k].get("bufferView"), int):
        raise AkError(f"the model has no image {which!r} kept in the file")
    target = imgs[k]["bufferView"]
    if any(a.get("bufferView") == target for a in doc.get("accessors") or []) or \
            sum(im.get("bufferView") == target for im in imgs) > 1:
        raise AkError("the image's data is shared with something else in the model: can't replace it")
    if len(doc.get("buffers") or []) != 1:
        raise AkError("the model has more than one buffer")
    out = bytearray()
    for i, v in enumerate(views):
        part = png if i == target else g.view(i)[0]
        out += bytes(-len(out) % 4)
        v["byteOffset"] = len(out)
        v["byteLength"] = len(part)
        out += part
    out += bytes(-len(out) % 4)
    doc["buffers"][0]["byteLength"] = len(out)
    imgs[k]["mimeType"] = "image/png"
    if name:
        imgs[k]["name"] = name
    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    js += b" " * (-len(js) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(out), 0x004E4942) + bytes(out)
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


# ---------------------------------------------------------------------------------------------------- command line

def _low_priority() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)   # below normal: keeps the PC (and a game) smooth
        except (OSError, AttributeError):
            pass


def secret_text(text: str, recipe: Recipe | None = None) -> str:
    """text with the weapon's names written as "extra" (build --quiet: the launcher's Details never names it)."""
    words = ["Weapon_Lsword_AK47", "AK-47", "AK47"] + ([recipe.actor] if recipe else [])
    for w in sorted(set(words), key=len, reverse=True):
        text = re.sub(re.escape(w), "extra", text, flags=re.IGNORECASE)
    return text


def main(argv: list[str] | None = None) -> int:
    with contextlib.suppress(AttributeError, ValueError, OSError):
        sys.stdout.reconfigure(errors="replace")
    lib_help = "the mod library (default: as modman: SWMOD_LIBRARY, else next to Savage Wind)"
    game_help = "the Savage Wind folder (default: the library's)"
    common = argparse.ArgumentParser(add_help=False)   # (before or after the command: the launcher puts --library last)
    common.add_argument("--library", help=lib_help, default=argparse.SUPPRESS)
    common.add_argument("--game", help=game_help, default=argparse.SUPPRESS)
    ap = argparse.ArgumentParser(prog="ak47_build.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--library", help=lib_help)
    ap.add_argument("--game", help=game_help)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", parents=[common], help="build the AK-47's two mods and add them to the library")
    b.add_argument("--model", help="another .glb model to use (default: assets/ak47's)")
    b.add_argument("--no-add", action="store_true", help="only write the mod folders (into --out)")
    b.add_argument("--out", help="where to write the mod folders (needed with --no-add; a new or empty folder)")
    b.add_argument("--optional", action="store_true",
                   help="a build that can't be made, for any reason, says why and ends with exit code 0 (the AK-47 is "
                        "a bonus of the multiplayer mod)")
    say = b.add_mutually_exclusive_group()
    say.add_argument("--quiet", action="store_true",
                     help="say only whether it worked, naming nothing (the launcher's Details: it is a secret)")
    say.add_argument("--verbose", action="store_true", help="also say where it lies (a spoiler)")
    sub.add_parser("check", parents=[common], help="offline checks on your files (reads only)")
    i = sub.add_parser("icon", parents=[common], help="draw the inventory icon to a PNG (changes nothing)")
    i.add_argument("--out", required=True, help="the PNG to write")
    sub.add_parser("where", parents=[common], help="the game files the AK-47 is built from")
    w = sub.add_parser("rewood", help="(developers) write the model with our own procedural wood texture in place of "
                                      "its wood picture")
    w.add_argument("--model", required=True, help="the .glb to read")
    w.add_argument("--out", required=True, help="the .glb to write (a new file)")
    w.add_argument("--image", default="wood", help="the image to replace: its name in the .glb, or its number")
    w.add_argument("--size", default="600x400", help="the wood picture's size (default 600x400)")
    args = ap.parse_args(argv)
    _low_priority()
    quiet = getattr(args, "quiet", False)
    optional = args.cmd == "build" and getattr(args, "optional", False)
    try:
        if args.cmd == "where":
            recipe = load_recipe()
            lib = mm.Library.load(mm.resolve_library(args.library))
            game = Game(os.path.abspath(args.game or (lib.install if lib else None) or mm.default_install()),
                        recipe.donor)
            for k in FILES:
                print(f"{'ok  ' if os.path.isfile(game.path(game.rel(k))) else 'MISSING'} {game.rel(k)}")
            return 0 if not game.missing() else 1
        if args.cmd == "check":
            problems = check(args.library, install=args.game, log=print)
            print("AK-47 check: all good." if not problems else f"AK-47 check: {len(problems)} problem(s):")
            for p in problems:
                print(f"  - {p}")
            return 1 if problems else 0
        if args.cmd == "icon":
            recipe = load_recipe()
            lib = mm.Library.load(mm.resolve_library(args.library))
            game = find_game(args.game, lib, recipe.donor)
            m, donor_raw = make_weapon(game, recipe)
            _ts, _names, alb = make_textures(m, nx_bfres.read_textures(_unyaz0(game.raw("tex"))),
                                             donor_sampler_textures(donor_raw), recipe.actor)
            _data, pic = build_icon(_unyaz0(game.raw("icon")), m, alb, recipe.actor)
            menu_bg.write_png(args.out, pic)
            print(f"Icon written: {args.out}")
            return 0
        if args.cmd == "rewood":
            ww, _, hh = args.size.lower().partition("x")
            if not (ww.isdigit() and hh.isdigit() and 8 <= int(ww) <= MAX_IMAGE and 8 <= int(hh) <= MAX_IMAGE):
                raise AkError(f"--size {args.size}: write it like 600x400 (8 to {MAX_IMAGE})")
            if os.path.exists(args.out):
                raise AkError(f"{args.out} is there already: pick a new file")
            with open(args.model, "rb") as f:
                data = f.read()
            pic = wood_picture(int(ww), int(hh))
            new = replace_image(data, int(args.image) if args.image.isdigit() else args.image,
                                menu_bg.png_bytes(pic), "wood (procedural: tools/ak47_build.py wood_picture)")
            _write(args.out, new)
            print(f"Written: {args.out} ({len(new)} bytes, SHA-256 {hashlib.sha256(new).hexdigest()})")
            return 0
        if args.no_add and not args.out:
            raise AkError("--no-add needs --out <folder>")
        r = build(args.library, install=args.game, out=args.out, add=not args.no_add, model_path=args.model,
                  log=None if quiet else print, verbose=args.verbose)
        made = r["built"]
        if quiet:
            print("Done: the extra is built" + (" and in your library." if r["entry"] else ".")
                  + (f" ({len(made.notes) + len(r['notes'])} note(s): python tools/ak47_build.py build shows them)"
                     if made.notes or r["notes"] else ""))
            return 0
        where = f"in your library as {r['entry']['name']} and {r['spot_entry']['name']}" if r["entry"] else \
            f"{made.folder} and {made.spot_folder}"
        spot = f", on the hill at x={made.spot[0]:.1f} z={made.spot[2]:.1f}" if args.verbose else ""
        print(f"AK-47 built ({made.vertices} vertices{spot}): {where}.")
        for n in made.notes:
            print(f"  note: {n}")
        for n in r["notes"]:
            print(f"  {n}")
        if r["entry"]:
            print("The item is always on (saves that hold it keep loading); where it lies only while the multiplayer "
                  "mod is on. Next: python tools/modman.py deploy")
        return 0
    except (AkError, mm.ModmanError) as e:
        if not optional:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"error: {secret_text(str(e)) if quiet else e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 (--optional: anything else that goes wrong never stops the mod either)
        if not optional:
            raise
        tb = traceback.format_exception(type(e), e, e.__traceback__, limit=-3)
        text = "".join(tb).rstrip()
        msg = f"error: {type(e).__name__}: {e}\n{text}"
        print(secret_text(msg) if quiet else msg, file=sys.stderr)
    if quiet:
        print("The extra wasn't built (it is optional): the mod it comes with is fine.")
    else:
        print("The AK-47 (a bonus of the multiplayer mod) wasn't built; the multiplayer mod is fine. Build it "
              "later with: python tools/ak47_build.py build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
