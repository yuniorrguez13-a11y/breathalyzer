"""charsel import-vrm: a VRM avatar (VRoid Studio and the like) as a costume on Link's skeleton, the way Linkle's was
made (until 2026-11-18): Model/SwChar_<id>.sbfres is Link's own model file with his shapes shrunk to a point and the avatar's meshes on
his bones (so the costume plays Link's own clips), its pack on a free actor name that the ghost DLL knows as
`custom` (`iam custom`, and the aliases in puppet.c's PUP_ALIASES).

What it does: every skinned mesh of the VRM (glTF 2.0 .glb), each VRM humanoid bone moved to its Link bone (hips ->
Waist, spine -> Spine_1, chest -> Spine_2, arms, legs, fingers; every other bone, such as hair or a skirt, follows its
nearest humanoid ancestor), the mesh fitted from the VRM's T-pose to Link's rest pose with the avatar's OWN lengths
(each body part turned to his bone's direction about its own joint, its joints chained at the avatar's lengths, the
feet on Link's ankle height; the model's skeleton gets those joints as its rest pose: own_fits, own_skeleton), the
weights put on Link's bones, and each material's base colour texture (up to 512 px, scaled to the game's albedo range,
BC1, or BC3 with an alpha cut-out when the picture has transparency), drawn with Link's materials (SKIN_MATERIAL). Not done:
spring bones (hair and skirt stay rigid), blend shapes (no blinking or mouth), MToon shading and outlines,
emissive/normal maps.

The VRM is untrusted data: it is only read (no code in it runs). Nothing made from a VRM goes in the repo.
"""

from __future__ import annotations

import copy
import io
import math
import shutil
import struct
import tempfile
from pathlib import Path

import charsel
from charsel import CharselError

IMPORT_ID = "custom"                 # the DLL's id for the imported costume (puppet.c PUP_SLOTS)
IMPORT_SLOT = "Dm_Npc_Handmaid_A"    # its free actor name (charsel FREE_SLOTS; the Handmaid class: a bigger heap)
IMPORT_ALIASES = ()                 # other words `iam` takes for it (puppet.c PUP_ALIASES; add yours, rebuild ghost)
TEX_MAX = 512
SHAPE_TPL = "Skin__Mt_Underwear"     # Link's shape, as the template of ours
NRM_TEX, SPM_TEX = "SwVrm_Nrm", "SwVrm_Spm"
SPM_VALUE = 38                      # a dull specular, Zelda's skin's average (Npc_Zelda_Skin_Spm: 37.6 [V])
# 2026-11-15, lime-green skin: the base colour textures were the right ones [V: decoded back, skin (248, 230, 216)],
# but drawn with Link's underwear / eyelashes materials; now Zelda's skin material (charsel.npc_skin, opaque and a
# cut-out copy). VRM textures are painted for flat toon shading, near white; the game's lit albedos are darker (Zelda's
# skin 156, 120, 92 and her white dress 121, 113, 98 average [V]), so the colours are scaled down to the game's range.
ALBEDO_SCALE = 0.62     # [G: the in-game check says whether she looks too pale or too dark]
# 2026-11-18 game check FAIL: with Zelda's Mt_Skin on every part, `iam custom` showed ONLY her head (body, arms and
# legs invisible), also without own_skeleton and with make_fits, so her own proportions aren't it [V: the user's
# check]. The cause isn't proven offline [G]; what Zelda's Mt_Skin asks that our meshes don't give: its shader reads a
# third texture coordinate (_u2) from a vertex attribute "All" that only Zelda's own shapes have, and it turns on a
# vertex position modify (uking_vtx_position_modify_calc_type 8, reading a vertex channel) that Link's Mt_Underwear
# doesn't [V: options compared 2026-11-18]. So the parts are drawn again with Link's materials, which showed the whole
# body before the looks change (the skin then came out lime green: that colour is the known cost of this fallback).
# "zelda" brings Zelda's back for a game check; "zelda-fed" is Zelda's without those two (fed_by_our_meshes), the
# next session's check of the likely cause.
SKIN_MATERIAL = "link"
OPAQUE_MAT, MASK_MAT = "Mt_Underwear", "Mt_Eyelashes"     # Link's (SKIN_MATERIAL "link")

# VRM humanoid bone -> Link bone (the first VRM bone of a Link bone is where that Link bone sits on the avatar)
BONES: dict[str, str] = {"hips": "Waist", "spine": "Spine_1", "chest": "Spine_2", "upperChest": "Spine_2",
                         "neck": "Neck", "head": "Head", "leftEye": "Head", "rightEye": "Head", "jaw": "Head"}
for _side, _s in (("left", "L"), ("right", "R")):
    BONES.update({f"{_side}Shoulder": f"Clavicle_{_s}", f"{_side}UpperArm": f"Arm_1_{_s}",
                  f"{_side}LowerArm": f"Arm_2_{_s}", f"{_side}Hand": f"Wrist_{_s}",
                  f"{_side}UpperLeg": f"Leg_1_{_s}", f"{_side}LowerLeg": f"Leg_2_{_s}",
                  f"{_side}Foot": f"Ankle_{_s}", f"{_side}Toes": f"Toe_{_s}"})
    for _f, _c, _parts in (("Thumb", "A", ("Metacarpal", "Proximal", "Distal")),
                           ("Index", "B", ("Proximal", "Intermediate", "Distal")),
                           ("Middle", "C", ("Proximal", "Intermediate", "Distal")),
                           ("Ring", "D", ("Proximal", "Intermediate", "Distal")),
                           ("Little", "E", ("Proximal", "Intermediate", "Distal"))):
        for _k, _p in enumerate(_parts):
            BONES[f"{_side}{_f}{_p}"] = f"Finger_{_c}_{_k + 1}_{_s}"

# the Link bone each one points to (its direction and length the avatar's part is fitted to)
LINK_NEXT = {"Spine_1": "Spine_2", "Spine_2": "Neck", "Neck": "Head"}
for _s in "LR":
    LINK_NEXT.update({f"Clavicle_{_s}": f"Arm_1_{_s}", f"Arm_1_{_s}": f"Arm_2_{_s}", f"Arm_2_{_s}": f"Wrist_{_s}",
                      f"Wrist_{_s}": f"Finger_C_1_{_s}", f"Leg_1_{_s}": f"Leg_2_{_s}",
                      f"Leg_2_{_s}": f"Ankle_{_s}", f"Ankle_{_s}": f"Toe_{_s}"})
    for _c in "ABCDE":
        LINK_NEXT.update({f"Finger_{_c}_1_{_s}": f"Finger_{_c}_2_{_s}", f"Finger_{_c}_2_{_s}": f"Finger_{_c}_3_{_s}"})


# ------------------------------------------------------------------------------------------------------ small math

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(v, fallback=(0.0, 1.0, 0.0)):
    n = math.sqrt(_dot(v, v))
    return (v[0] / n, v[1] / n, v[2] / n) if n > 1e-9 else fallback


def _apply(m, v):
    return tuple(m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2] for i in range(3))


def rot_between(a, b) -> list[list[float]]:
    """The 3x3 rotation turning unit vector a onto unit vector b (the shortest turn)."""
    v, c = _cross(a, b), _dot(a, b)
    if c < -0.9999:     # (opposite: half a turn about any axis square to a)
        p = _unit(_cross(a, (1.0, 0.0, 0.0) if abs(a[0]) < 0.9 else (0.0, 1.0, 0.0)))
        return [[2 * p[i] * p[j] - (1.0 if i == j else 0.0) for j in range(3)] for i in range(3)]
    k = 1.0 / (1.0 + c)
    vx = [[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]]
    return [[(1.0 if i == j else 0.0) + vx[i][j] + k * sum(vx[i][t] * vx[t][j] for t in range(3)) for j in range(3)]
            for i in range(3)]


EYE3 = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


class Fit:
    """One Link bone's fit: an avatar point near that bone's VRM joint goes to the costume's joint (a_link), turned
    from the VRM bone's direction to Link's (or by `rot`), the part beyond the joint stretched by `stretch` (1: the
    avatar's own length)."""

    def __init__(self, a_vrm, a_link, d_vrm=None, d_link=None, stretch=1.0, rot=None):
        self.a_vrm, self.a_link = a_vrm, a_link
        self.d_vrm, self.d_link, self.k = d_vrm, d_link, stretch
        self.rot = rot or (rot_between(d_vrm, d_link) if d_vrm and d_link else EYE3)

    def point(self, p):
        x = _sub(p, self.a_vrm)
        if self.k == 1.0:          # (own length: a plain turn about the joint)
            x = _apply(self.rot, x)
        elif self.d_vrm:
            along = _dot(x, self.d_vrm)
            perp = _apply(self.rot, (x[0] - along * self.d_vrm[0], x[1] - along * self.d_vrm[1],
                                     x[2] - along * self.d_vrm[2]))
            a = along * self.k if along > 0 else along
            x = (perp[0] + a * self.d_link[0], perp[1] + a * self.d_link[1], perp[2] + a * self.d_link[2])
        return (self.a_link[0] + x[0], self.a_link[1] + x[1], self.a_link[2] + x[2])

    def normal(self, n):
        return _apply(self.rot, n)


def make_fits(vrm_joints: dict[str, tuple], link_joints: dict[str, tuple]) -> dict[str, Fit]:
    """{Link bone: Fit}. vrm_joints: {VRM humanoid bone: position (already scaled to Link's size)}, link_joints:
    {Link bone: position at rest}."""
    anchor: dict[str, tuple] = {}
    for hb, lb in BONES.items():
        if hb in vrm_joints and lb in link_joints and lb not in anchor:
            anchor[lb] = vrm_joints[hb]
    fits = {}
    for lb, av in anchor.items():
        nb = LINK_NEXT.get(lb)
        if nb in anchor:
            dv, dl = _sub(anchor[nb], av), _sub(link_joints[nb], link_joints[lb])
            lv, ll = math.sqrt(_dot(dv, dv)), math.sqrt(_dot(dl, dl))
            if lv > 1e-4 and ll > 1e-4:
                fits[lb] = Fit(av, link_joints[lb], _unit(dv), _unit(dl), max(0.5, min(3.0, ll / lv)))
                continue
        fits[lb] = Fit(av, link_joints[lb])
    return fits


# 2026-11-15 (the user: "why are her arms so far away from her chest, it looks so weird"): make_fits stretched every
# part to Link's lengths (her collarbones x2.2, forearms x1.4, neck x1.66), so her shoulders came out as wide as his
# and her arms hung far from her chest. Link's clips turn the body bones only (no limb moves: every Player_Animation
# clip moves only Root, Skl_Root, the weapon bones and the face [V]), so the costume can keep the avatar's own lengths:
# each part is only turned to Link's rest direction (rot_between, as before), its joint placed at the end of its
# parent part turned the same way, and the model's skeleton (Link's bones and rest turns) gets those joints as its
# rest pose (own_skeleton). The feet are put on Link's ankle height (Skl_Root's moves are Link's hip heights).
KEEP_UPRIGHT = ("Head",)       # bones whose part keeps the avatar's own turn (no direction of its own to fit)


def own_fits(vrm_joints: dict[str, tuple], bones) -> tuple[dict[str, Fit], dict[str, tuple], dict]:
    """({Link bone: Fit}, {Link bone: the costume's joint}, info) keeping the avatar's own lengths. vrm_joints: {VRM
    humanoid bone: position (at Link's size)}; bones: Link's skeleton bones (rest pose)."""
    lw = charsel._worlds(bones)
    index = {b.name: i for i, b in enumerate(bones)}
    link_joints = {b.name: (lw[i][0][3], lw[i][1][3], lw[i][2][3]) for i, b in enumerate(bones)}
    anchor: dict[str, tuple] = {}
    for hb, lb in BONES.items():
        if hb in vrm_joints and lb in index and lb not in anchor:
            anchor[lb] = vrm_joints[hb]

    def parent_of(lb):          # (the nearest anchored ancestor in Link's skeleton)
        i = bones[index[lb]].parent
        while i >= 0 and bones[i].name not in anchor:
            i = bones[i].parent
        return bones[i].name if i >= 0 else None
    order = sorted(anchor, key=lambda n: index[n])       # (Link's bones list parents first)
    rot: dict[str, list] = {}
    joint: dict[str, tuple] = {}
    for lb in order:
        p = parent_of(lb)
        nb = LINK_NEXT.get(lb)
        if nb in anchor:
            dv, dl = _sub(anchor[nb], anchor[lb]), _sub(link_joints[nb], link_joints[lb])
            rot[lb] = rot_between(_unit(dv), _unit(dl)) if _dot(dv, dv) > 1e-8 and _dot(dl, dl) > 1e-8 else EYE3
        else:
            rot[lb] = EYE3 if lb in KEEP_UPRIGHT or p is None else rot[p]
        joint[lb] = anchor[lb] if p is None else tuple(
            a + b for a, b in zip(joint[p], _apply(rot[p], _sub(anchor[lb], anchor[p]))))
    # the feet on Link's ankle height
    ank = [lb for lb in ("Ankle_L", "Ankle_R") if lb in joint and lb in link_joints]
    dy = (sum(link_joints[a][1] - joint[a][1] for a in ank) / len(ank)) if ank else 0.0
    joint = {lb: (j[0], j[1] + dy, j[2]) for lb, j in joint.items()}
    fits = {lb: Fit(anchor[lb], joint[lb], rot=rot[lb]) for lb in order}

    def width(a, b, js):
        return round(math.dist(js[a], js[b]), 3) if a in js and b in js else None
    info = {"own_lengths": True, "feet_shift_m": round(dy, 3),
            "shoulders_m": width("Arm_1_L", "Arm_1_R", joint), "link_shoulders_m": width("Arm_1_L", "Arm_1_R",
                                                                                        link_joints),
            "hips_m": width("Leg_1_L", "Leg_1_R", joint), "link_hips_m": width("Leg_1_L", "Leg_1_R", link_joints)}
    return fits, joint, info


def own_skeleton(sk, joints: dict[str, tuple]):
    """Link's skeleton with its rest pose moved so the named bones rest at `joints` (model space): each such bone
    keeps Link's rest turn, every other bone its place on its parent. New inverse bind matrices to match. Returns the
    new Skeleton (sk itself unchanged)."""
    import copy
    from wiiu_model.neutral import Skeleton
    bones = copy.deepcopy(sk.bones)
    old = charsel._worlds(sk.bones)
    new: list = [None] * len(bones)
    for i, b in enumerate(bones):     # (parents come first in the game's skeletons)
        if b.parent >= i:
            raise CharselError("Link's skeleton lists a bone before its parent")
        pw = new[b.parent] if b.parent >= 0 else None
        if b.name in joints:
            w = [list(old[i][r][:3]) + [joints[b.name][r]] for r in range(3)] + [[0.0, 0.0, 0.0, 1.0]]
            if pw is not None:            # (Link's rest turn kept: only the translation in the parent's frame moves)
                rel = charsel._mul(charsel._inv(pw), w)
                b.translation = (rel[0][3], rel[1][3], rel[2][3])
            else:
                b.translation = tuple(joints[b.name])
        new[i] = charsel._local(b) if pw is None else charsel._mul(pw, charsel._local(b))
    ibs = bytearray()
    for k in range(sk.smooth_count):
        w = charsel._inv(new[sk.matrix_to_bone[k]])
        ibs += struct.pack("<12f", *(w[r][c] for r in range(3) for c in range(4)))
    return Skeleton(sk.flags, bones, list(sk.matrix_to_bone), sk.smooth_count, sk.rigid_count, bytes(ibs),
                    dict(sk.extra))


# ------------------------------------------------------------------------------------------------------ the VRM

def read_vrm(data: bytes, name: str = "the VRM"):
    """(Glb, {humanoid bone: node}, flip): flip for VRM 0.x (it faces -Z; Link and VRM 1.0 face +Z)."""
    from ak47_build import AkError, Glb
    try:
        g = Glb(data, name)
    except AkError as e:
        raise CharselError(str(e)) from None
    ext = g.doc.get("extensions") or {}
    if isinstance(ext.get("VRMC_vrm"), dict):
        hb = {k: v.get("node") for k, v in ((ext["VRMC_vrm"].get("humanoid") or {}).get("humanBones") or {}).items()
              if isinstance(v, dict)}
        flip = False
    elif isinstance(ext.get("VRM"), dict):
        old = {"Proximal": "Metacarpal", "Intermediate": "Proximal"}   # (0.x thumbs: one step lower)
        hb = {}
        for b in (ext["VRM"].get("humanoid") or {}).get("humanBones") or []:
            n = str(b.get("bone", ""))
            for side in ("left", "right"):
                for o, new in old.items():
                    if n == f"{side}Thumb{o}":
                        n = f"{side}Thumb{new}"
                        break
            hb[n] = b.get("node")
        flip = True
    else:
        raise CharselError(f"{name} isn't a VRM (no VRMC_vrm or VRM humanoid in it)")
    nodes = g._list("nodes")
    hb = {k: v for k, v in hb.items() if isinstance(v, int) and 0 <= v < len(nodes)}
    for need in ("hips", "head", "leftUpperArm", "rightUpperArm", "leftUpperLeg", "rightUpperLeg"):
        if need not in hb:
            raise CharselError(f"{name}'s humanoid has no {need} bone")
    return g, hb, flip


def node_links(g, hb: dict[str, int]) -> dict[int, str]:
    """{node: Link bone}: a humanoid bone's own, else its nearest humanoid ancestor's (hair, skirt...), else Waist."""
    nodes = g._list("nodes")
    parent = {c: i for i, n in enumerate(nodes) for c in n.get("children") or [] if isinstance(c, int)}
    own = {n: BONES[b] for b, n in hb.items() if b in BONES}
    out = {}
    for i in range(len(nodes)):
        j, seen = i, 0
        while j is not None and j not in own and seen < 1000:
            j, seen = parent.get(j), seen + 1
        out[i] = own.get(j, "Waist")
    return out


# ------------------------------------------------------------------------------------------------------ the model

def _snorm8(v):
    return max(-127, min(127, round(v * 127))) & 0xFF


def _n10(n):
    q = [max(-511, min(511, round(c * 511))) & 0x3FF for c in n]
    return q[0] | (q[1] << 10) | (q[2] << 20) | (1 << 30)


# our vertex layout: positions (3 x f32) | weights (4 x unorm8), matrix indices (4 x u8) | normal (10_10_10_2),
# tangent (4 x snorm8), uv (2 x f16). Link's materials read _p0 _n0 _t0 _w0 _i0 _u0 (their shader assign).
def vertex_buffer(pos, nrm, uv, infl) -> object:
    from wiiu_model.neutral import Attribute, VertexBuffer
    b0, b1, b2 = bytearray(), bytearray(), bytearray()
    for p, n, t, (ix, wt) in zip(pos, nrm, uv, infl):
        b0 += struct.pack("<3f", *p)
        b1 += bytes(wt) + bytes(ix)
        tg = _unit(_cross((0.0, 1.0, 0.0), n), (1.0, 0.0, 0.0))
        b2 += struct.pack("<I", _n10(n)) + bytes((_snorm8(tg[0]), _snorm8(tg[1]), _snorm8(tg[2]), 127))
        b2 += struct.pack("<2e", max(-65504.0, min(65504.0, t[0])), max(-65504.0, min(65504.0, t[1]))) * 2
    attrs = [Attribute("_p0", 0x1805, 0, 0), Attribute("_w0", 0x0B01, 0, 1), Attribute("_i0", 0x0B03, 4, 1),
             Attribute("_n0", 0x0E02, 0, 2), Attribute("_t0", 0x0B02, 4, 2), Attribute("_u0", 0x1205, 8, 2),
             Attribute("_u1", 0x1205, 12, 2)]      # (_u1: the NPC skin material's shade-map coordinates, = _u0)
    return VertexBuffer(len(pos), 4, attrs, [12, 8, 16], [bytes(b0), bytes(b1), bytes(b2)], {})


def quantize(weights: dict[int, float]) -> tuple[list[int], list[int]]:
    """(4 matrix indices, 4 u8 weights summing 255) from {matrix: weight}: the 4 heaviest."""
    top = sorted(weights.items(), key=lambda kv: -kv[1])[:4]
    tot = sum(w for _m, w in top) or 1.0
    q = [round(255 * w / tot) for _m, w in top]
    q[0] += 255 - sum(q)
    ix = [m for m, _w in top]
    while len(ix) < 4:
        ix.append(ix[0] if ix else 0)
        q.append(0)
    return ix, q


def _texture(tpl, name: str, img, bc3: bool):
    import menu_bg
    from wiiu_model import formats
    w, h = img.size
    levels = []
    for k in range(int(math.log2(max(w, h))) + 1):
        lw, lh = max(1, w >> k), max(1, h >> k)
        im = img if k == 0 else img.resize((lw, lh), 1)   # (1: Image.LANCZOS)
        lv = menu_bg.encode_bc(im.tobytes(), lw, lh, bc3)
        if len(lv) != formats.level_size(tpl.format, w, h, k):
            raise CharselError(f"internal: {name} level {k} is {len(lv)} bytes")
        levels.append(lv)
    t = copy.deepcopy(tpl)
    t.name, t.width, t.height, t.levels = name, w, h, levels
    t.extra.pop("nx.block_height_log2", None)
    return t


def fed_by_our_meshes(mat, like):
    """A copy of Zelda's skin material without what our meshes can't feed (the 2026-11-18 head-only check [G]): no
    shader attribute from a vertex attribute our buffers lack ("All", her own shapes' third coordinates), and the
    vertex position modify options set to `like`'s (Link's Mt_Underwear: off)."""
    m = copy.deepcopy(mat)
    ours = {"_p0", "_n0", "_t0", "_w0", "_i0", "_u0", "_u1"}
    m.shader.attribs = [(a, v) for a, v in m.shader.attribs if v in ours]
    theirs = dict(like.shader.options)
    m.shader.options = [(k, theirs.get(k, v) if k.startswith("uking_vtx_position_modify") else v)
                        for k, v in m.shader.options]
    return m


def as_mask(mat):
    """A copy of a material drawn as a cut-out (alpha test at 0.5), the way Link's eyelashes differ from his
    underwear: only these two render infos [V: their options are the same]."""
    m = copy.deepcopy(mat)
    for r in m.render_infos:
        if r.name == "gsys_render_state_mode":
            r.values = ("mask",)
        elif r.name == "gsys_alpha_test_enable":
            r.values = ("true",)
    return m


def game_albedo(img, k: float = ALBEDO_SCALE):
    """An RGBA picture with its colour (not its alpha) scaled by k."""
    import menu_bg
    r, g, b, a = img.split()
    lut = [max(0, min(255, round(v * k))) for v in range(256)]
    return menu_bg.pillow().merge("RGBA", (r.point(lut), g.point(lut), b.point(lut), a))


def _flat_texture(tpl, name: str, block: bytes):
    t = copy.deepcopy(tpl)
    t.name, t.width, t.height, t.levels = name, 4, 4, [block]
    t.extra.pop("nx.block_height_log2", None)
    return t


def vrm_costume(data: bytes, romfs, name: str = "the VRM", say=print) -> tuple[bytes, bytes, dict]:
    """(model, textures, info) of the avatar on Link's skeleton: Link's model file (his shapes shrunk to a point) with
    the avatar's meshes, and Link's textures with the avatar's. Yaz0-packed like the game's .sbfres."""
    import menu_bg
    from ak47_build import bc5_encode
    from wiiu_model import checks, nx_bfres, nx_model
    from wiiu_model.convert import set_texinfo
    from wiiu_model.neutral import Mesh, ResFile, TextureSet
    Image = menu_bg.pillow()
    if Image is None:
        raise CharselError("import-vrm needs Pillow for the textures (pip install pillow)")
    g, hb, flip = read_vrm(data, name)
    link_raw, link_tex = charsel.model_file(Path(romfs), "Link"), charsel.model_file(Path(romfs), "Link.Tex")
    if not link_raw or not link_tex:
        raise CharselError("Link's model or textures aren't in the game's files")
    link_res = nx_model.read(charsel._unyaz(link_raw))
    link = copy.deepcopy(link_res.models[0])
    sk = link.skeleton
    bix = {b.name: i for i, b in enumerate(sk.bones)}
    lw = charsel._worlds(sk.bones)
    link_joints = {b.name: (lw[i][0][3], lw[i][1][3], lw[i][2][3]) for i, b in enumerate(sk.bones)}
    shape_tpl = copy.deepcopy(next(s for s in link.shapes if s.name == SHAPE_TPL))
    if SKIN_MATERIAL in ("zelda", "zelda-fed"):
        skin_mat, ztex = charsel.npc_skin(romfs)      # (Zelda's skin material: see charsel NPC_SKIN_MODEL)
        if SKIN_MATERIAL == "zelda-fed":
            under = next(m for m in link_res.models[0].materials if m.name == OPAQUE_MAT)
            skin_mat = fed_by_our_meshes(skin_mat, under)
        mask_mat = as_mask(skin_mat)
    else:                                             # (Link's: see SKIN_MATERIAL)
        lmats = {m.name: m for m in link_res.models[0].materials}
        if OPAQUE_MAT not in lmats or MASK_MAT not in lmats:
            raise CharselError(f"Link's model has no {OPAQUE_MAT} / {MASK_MAT} (other game version?)")
        skin_mat, mask_mat, ztex = lmats[OPAQUE_MAT], lmats[MASK_MAT], None

    def matrix_of(lb: str) -> int:      # (the Link bone's smooth matrix, or its nearest parent's)
        i = bix[lb]
        while sk.bones[i].smooth_index < 0 and sk.bones[i].parent >= 0:
            i = sk.bones[i].parent
        return max(0, sk.bones[i].smooth_index)

    # the avatar's joints, at Link's size (head height) and facing
    worlds = g.node_worlds()
    fl = (-1.0, 1.0, -1.0) if flip else (1.0, 1.0, 1.0)

    def jpos(n):
        w = worlds.get(n)
        return (w[0][3] * fl[0], w[1][3], w[2][3] * fl[2]) if w else None
    head = jpos(hb["head"])
    if not head or head[1] <= 0.05:
        raise CharselError(f"{name}'s head bone isn't above its feet")
    s = link_joints["Head"][1] / head[1]
    vj = {b: tuple(c * s for c in jpos(n)) for b, n in hb.items() if jpos(n)}
    fits, joints, fit_info = own_fits(vj, sk.bones)     # (her own lengths, turned to Link's rest pose)
    link.skeleton = sk = own_skeleton(sk, joints)
    links = node_links(g, hb)

    _shrink = charsel._shrink_model(link)
    nodes, meshes, gmats = g._list("nodes"), g._list("meshes"), g._list("materials")
    skins = g._list("skins")
    cache: dict = {}

    def acc(i):
        if i not in cache:
            cache[i] = g.accessor(i)
        return cache[i]

    # textures: Link's, then ours (one per image used; a flat normal and a dull specular for all)
    lt = nx_bfres.read_textures(charsel._unyaz(link_tex))
    ltex = {t.name: t for t in lt.textures}
    ours = [_flat_texture(ltex["Link_Skin_Nrm"], NRM_TEX, bc5_encode(4, 4, lambda x, y: (128, 128))),
            _flat_texture(ltex["Link_Skin_Spm"], SPM_TEX, menu_bg._alpha_block([SPM_VALUE] * 16))]
    if ztex:
        ours += charsel.flat_skin_fills(ztex)
    tex_of: dict = {}      # image or colour key -> (texture name, has alpha)

    def texture_for(m: dict) -> tuple[str, bool]:
        pbr = m.get("pbrMetallicRoughness") or {}
        src = g.image_source((pbr.get("baseColorTexture") or {}).get("index"))
        key = ("image", src) if src is not None else ("color", tuple(pbr.get("baseColorFactor") or (1, 1, 1, 1)))
        if key not in tex_of:
            if src is not None:
                img = Image.open(io.BytesIO(g.image(src))).convert("RGBA")
            else:
                c = tuple(max(0, min(255, round(float(x) * 255))) for x in (list(key[1]) + [1, 1, 1, 1])[:4])
                img = Image.new("RGBA", (4, 4), c)
            if max(img.size) > TEX_MAX:
                f = TEX_MAX / max(img.size)
                img = img.resize((max(4, round(img.width * f)), max(4, round(img.height * f))), 1)
            img = game_albedo(img)
            alpha = img.getchannel("A").getextrema()[0] < 250
            tname = f"SwVrm_{len(tex_of):02}_Alb"
            say(f"    texture {tname} {img.width}x{img.height} {'BC3 (cut-out)' if alpha else 'BC1'}")
            ours.append(_texture(ltex["Link_Eyelashes_Alb" if alpha else "Link_Skin_Alb"], tname, img, alpha))
            tex_of[key] = (tname, alpha)
        return tex_of[key]

    info = {"shapes": 0, "vertices": 0, "triangles": 0, "left_out": [], "scale": round(s, 4), "fit": fit_info,
            "material": (f"{charsel.NPC_SKIN_MODEL} {charsel.NPC_SKIN_MAT}" if ztex else
                         f"Link {OPAQUE_MAT} / {MASK_MAT}"), "albedo_scale": ALBEDO_SCALE}
    for ni, node in enumerate(nodes):
        mi, si = node.get("mesh"), node.get("skin")
        if not isinstance(mi, int) or not 0 <= mi < len(meshes):
            continue
        if not isinstance(si, int) or not 0 <= si < len(skins):
            info["left_out"].append(f"{node.get('name', ni)} (not skinned)")
            continue
        joints = skins[si].get("joints") or []
        for pi, p in enumerate(meshes[mi].get("primitives") or []):
            at = p.get("attributes") or {}
            if p.get("mode", 4) != 4 or not {"POSITION", "JOINTS_0", "WEIGHTS_0"} <= set(at) or "indices" not in p:
                info["left_out"].append(f"{meshes[mi].get('name', mi)} part {pi}")
                continue
            P, J, W = acc(at["POSITION"]), acc(at["JOINTS_0"]), acc(at["WEIGHTS_0"])
            N = acc(at["NORMAL"]) if "NORMAL" in at else None
            T = acc(at["TEXCOORD_0"]) if "TEXCOORD_0" in at else None
            idx = [x[0] for x in acc(p["indices"])]
            if len(idx) % 3 or any(not 0 <= i < len(P) for i in idx):
                raise CharselError(f"{name}: a mesh has a broken index list")
            used = sorted(set(idx))
            remap = {v: k for k, v in enumerate(used)}
            pos, nrm, uv, infl = [], [], [], []
            for v in used:
                wsum: dict[str, float] = {}
                for j, w in zip(J[v], W[v]):
                    if w > 0 and 0 <= int(j) < len(joints):
                        lb = links.get(joints[int(j)], "Waist")
                        wsum[lb] = wsum.get(lb, 0.0) + float(w)
                if not wsum:
                    wsum = {"Waist": 1.0}
                tot = sum(wsum.values())
                x, y, z = P[v][:3]
                pv = (x * fl[0] * s, y * s, z * fl[2] * s)
                nv = (N[v][0] * fl[0], N[v][1], N[v][2] * fl[2]) if N else (0.0, 1.0, 0.0)
                fp, fn = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
                for lb, w in wsum.items():
                    f = fits.get(lb) or Fit(pv, pv)
                    a, b = f.point(pv), f.normal(nv)
                    for c in range(3):
                        fp[c] += a[c] * w / tot
                        fn[c] += b[c] * w / tot
                mw: dict[int, float] = {}
                for lb, w in wsum.items():
                    m = matrix_of(lb if lb in bix else "Waist")
                    mw[m] = mw.get(m, 0.0) + w
                pos.append(tuple(fp))
                nrm.append(_unit(tuple(fn)))
                uv.append(tuple(float(c) for c in T[v][:2]) if T else (0.0, 0.0))
                infl.append(quantize(mw))
            tris = [remap[i] for i in idx]
            m = gmats[p["material"]] if isinstance(p.get("material"), int) and 0 <= p["material"] < len(gmats) else {}
            if m.get("doubleSided"):     # (the game's materials draw front faces only: the back as its own faces)
                n0 = len(pos)
                pos, uv, infl = pos + pos, uv + uv, infl + infl
                nrm = nrm + [(-a, -b, -c) for a, b, c in nrm]
                tris += [n0 + tris[t + k] for t in range(0, len(tris), 3) for k in (0, 2, 1)]
            if len(pos) > 0xFFFF:
                raise CharselError(f"{name}: a mesh part has {len(pos)} vertices (at most 65535)")
            tname, alpha = texture_for(m)
            mat = copy.deepcopy(mask_mat if alpha else skin_mat)
            mat.name = f"SwVrm_Mt_{info['shapes']:02}"
            ours_of = {"_a0": tname, "_a1": charsel.FLAT_DAMAGE, "_n0": NRM_TEX, "_s0": SPM_TEX, "_ao0": charsel.FLAT_AO}
            mat.textures = [ours_of.get(sm.name, t) for sm, t in zip(mat.samplers, mat.textures)]
            link.materials.append(mat)
            link.vertex_buffers.append(vertex_buffer(pos, nrm, uv, infl))
            sh = copy.deepcopy(shape_tpl)
            sh.name = f"SwVrm_{info['shapes']:02}__{mat.name}"
            sh.material, sh.vertex_buffer = len(link.materials) - 1, len(link.vertex_buffers) - 1
            sh.skin_count, sh.key_shapes = 4, []
            sh.skin_bones = sorted({sk.matrix_to_bone[i] for ix, _w in infl for i in ix})
            sh.meshes = [Mesh("triangles", "u16", len(tris), struct.pack(f"<{len(tris)}H", *tris), 0,
                              [(0, len(tris))])]
            box = (0.0, 0.8, 0.0, 1.5, 1.5, 1.5)          # (generous: never culled while Link's clips move it)
            sh.bounds, sh.radii = [box, box], [2.6]
            link.shapes.append(sh)
            info["shapes"] += 1
            info["vertices"] += len(pos)
            info["triangles"] += len(tris) // 3
    if not info["shapes"]:
        raise CharselError(f"{name} has no skinned mesh")
    link.total_vertex_count = sum(vb.vertex_count for vb in link.vertex_buffers)
    ts = TextureSet(lt.name, lt.textures + ours, "model", dict(lt.extra))
    res = ResFile(link_res.name, [link], [], [], [copy.deepcopy(e) for e in link_res.external], dict(link_res.extra))
    set_texinfo(res, len(ts.textures))
    raw = nx_model.write(res)
    bad = checks.switch_model_problems(raw)
    if bad:
        raise CharselError(f"the imported model fails the self-check: {bad[:3]}")
    info["textures"] = len(ours)
    return charsel._yaz(raw), charsel._yaz(nx_bfres.write_textures(ts)), info


# ------------------------------------------------------------------------------------------------------ the pack

def import_vrm(vrm: Path, cid: str, romfs=None, out=None, say=print) -> dict:
    """Build the costume into a characters mod folder (out: charsel's, default out\\charsel-packs): the model, its
    textures, the pack on IMPORT_SLOT, catalog["imported"][cid], the size table. A later `charsel build` keeps it."""
    from mp import ghost_link_pack as glp
    romfs = Path(romfs or charsel.DEFAULT_ROMFS).resolve()
    out = Path(out or charsel.DEFAULT_OUT).resolve()
    cid = (cid or "").strip().lower()
    if not cid.isascii() or not cid.replace("_", "").isalnum() or len(cid) > 20:
        raise CharselError("--id is up to 20 letters, digits or _")
    if out == romfs or romfs in out.parents or out in romfs.parents:
        raise CharselError("--out must be a folder of your own, not inside the game's files")
    say(f"Reading {Path(vrm).name} and Link's model...")
    model, tex, info = vrm_costume(Path(vrm).read_bytes(), romfs, Path(vrm).name, say)
    model, tex, _ = charsel.own_names(model, tex, charsel.ours(cid))   # (named as their files, not "Link": docs/room.md)
    try:
        ainfo = charsel.read_actorinfo(romfs)
    except Exception:
        ainfo = None
    profile, problems = charsel.check_slot(romfs, IMPORT_SLOT, ainfo)
    if problems:
        raise CharselError("; ".join(problems))
    template = glp.read_pack(glp.find_pack(romfs, charsel.TEMPLATE))
    pause = glp.read_pack(glp.find_pack(romfs, glp.DONOR_LINK))
    files = charsel.make_copy_pack(cid, IMPORT_SLOT, profile, template, pause)
    data, unpacked = glp.pack_bytes(files)
    problems = charsel.check_pack(data, IMPORT_SLOT)
    if problems:
        raise CharselError("; ".join(problems))
    top = out / "romfs"
    cat = charsel.load_catalog(out) or {"format": 1, "builder": charsel.VERSION, "mod": charsel.MOD_ID, "built": {},
                                        "kept": [], "skipped": {}, "notes": [], "linkle": {"how": "missing"}}
    for old in list((cat.get("imported") or {})):
        for ext in (".sbfres", ".Tex.sbfres"):
            p = top / "Model" / f"{charsel.ours(old)}{ext}"
            if p.is_file():
                p.unlink()
    for k in [k for k, v in (cat.get("built") or {}).items() if isinstance(v, dict) and v.get("slot") == IMPORT_SLOT]:
        del cat["built"][k]           # (a --model-copies Link on the same name: the import wins)
    for rel, d in ((f"Actor/Pack/{IMPORT_SLOT}.sbactorpack", data), (f"Model/{charsel.ours(cid)}.sbfres", model),
                   (f"Model/{charsel.ours(cid)}.Tex.sbfres", tex)):
        t = top / rel
        t.parent.mkdir(parents=True, exist_ok=True)
        if t.exists():
            t.unlink()
        t.write_bytes(d)
    cat["imported"] = {cid: {"slot": IMPORT_SLOT, "profile": profile, "iam": IMPORT_ID, "from": Path(vrm).name,
                             "packed": len(data), "unpacked": unpacked, **info}}
    cat["sizes"] = charsel.write_sizes(romfs, out, say)
    (out / charsel.BUILT_MARK).write_text(charsel.BUILT_TEXT, encoding="utf-8")
    if not (out / "mod.toml").is_file():
        (out / "mod.toml").write_text(charsel.MOD_TOML, encoding="utf-8")
    (out / charsel.CATALOG).write_text(__import__("json").dumps(cat, indent=1) + "\n", encoding="utf-8")
    return cat["imported"][cid]


def import_into_library(vrm: Path, cid: str, library, romfs=None, say=print) -> dict:
    """The same into a mod library's characters mod (a copy, then put back in its place with its ON/OFF)."""
    import modman as mm
    lib = charsel._open_library(library)
    romfs = romfs or (Path(lib.install) / "extracted" / "romfs" if lib.install else charsel.DEFAULT_ROMFS)
    idx = next((i for i, m in enumerate(lib.mods) if m["id"] == charsel.MOD_ID), None)
    tmp = Path(tempfile.mkdtemp(prefix="charsel-vrm-"))
    out = tmp / charsel.MOD_ID
    try:
        if idx is not None:
            shutil.copytree(lib.mod_path(charsel.MOD_ID), out)
        got = import_vrm(vrm, cid, romfs, out, say)
        was_on = lib.mods[idx]["enabled"] if idx is not None else True
        if idx is not None:
            mm.remove_mod(lib, charsel.MOD_ID)
        entry, _scan, _notes = mm.add_mod(lib, str(out))
        i = next(k for k, m in enumerate(lib.mods) if m["id"] == entry["id"])
        m = lib.mods.pop(i)
        m["enabled"] = was_on
        lib.mods.insert(len(lib.mods) if idx is None else min(idx, len(lib.mods)), m)
        lib.save()
    except mm.ModmanError as e:
        raise CharselError(str(e)) from None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return got
