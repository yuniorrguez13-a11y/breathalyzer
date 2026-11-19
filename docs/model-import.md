# Model import (groundwork)

Readers in `tools/model_in/` turn a model file into one neutral `Scene` (see `tools/model_in/scene.py` for the
contract: Y up, metres, right-handed, glTF-shaped, bind = rest). Nothing in the launcher, modman, charsel or the game
uses them yet. Pure Python standard library; no third-party code. `model_in.load(path)` picks the reader by the
file's extension. Each reader writes what it dropped or changed into `scene.meta["notes"]` as plain sentences.
The full plan is `out\research\model-converter-plan.md` (not in git).

## Use

```python
import model_in                       # with tools/ on sys.path
scene = model_in.load("girl.vrm")     # picks the reader by extension
problems = scene.validate()           # [] = fine; else short sentences
print(scene.summary())                # counts only, safe to print
```

A reader is a module `tools/model_in/<format>.py` with `read(path, **opts) -> Scene`. `load()` sends `.gltf`,
`.glb` and `.vrm` to `gltf.py`, and any other `.ext` to `model_in/<ext>.py` if it exists, so a new reader needs no
edit to `__init__.py`. Readers raise `ReadError` with one plain sentence, never a traceback.

Quick check from a terminal (run in `tools\`): `python -m model_in MODEL...` prints each model's counts, the
reader's notes and any validate() problems (exit 1 if a model can't be read or isn't clean).

## The Scene (`scene.py`)

- **Space:** Y up, metres, right-handed. `meta["forward"]` says where the model faces (`+Z` for glTF, VRM 1.0 and
  PMX, `-Z` for VRM 0.x); nothing is turned.
- **Mesh** (one per material part): `positions`, `normals`, `uvs` (a list of UV sets; v = 0 at the top of the
  image, like glTF), `colors` (RGBA 0..1), `joints` (4 bone indices into the scene skeleton, not per skin),
  `weights` (top 4, renormalised, sum 1), `triangles` (32-bit indices, no limit), `material`.
- **Skeleton:** `bones`, parents before children. Rest pose as local TRS (quaternion x,y,z,w) and an inverse bind
  matrix (column-major 4x4, like glTF). **bind = rest**: readers move skinned vertices to the rest pose.
- **Material:** linear `base_color`; `textures` {slot: texture index} for base, normal, emissive,
  metallic_roughness, occlusion, shade; `texcoords` {slot: UV set}; `alpha_mode`, `alpha_cutoff`, `double_sided`,
  `emissive`, `metallic`, `roughness`; toon sources set `shader` ("mtoon", "mmd") and `shade_color`.
- **Texture:** `name`, `mime`, `data` (bytes) or `path` (a file next to the model); `read_bytes()`.
- **meta:** `source_format`, `units`, `up`, `forward`, `licence` (or None), `notes`, and per-format extras
  (`humanoid`, `morphs`, `vrm`, `thumbnail_texture`, ...).
- **validate():** index ranges (triangles, joints, materials, textures, humanoid), weights sum ~1 and not
  negative, finite numbers, parent order, unit quaternions, list lengths.
- **Shared helpers:** `top4`, `skin_to_rest`, `smooth_normals`, `read_file` (a missing file -> `ReadError`),
  `check_finite` (NaN/infinity -> `ReadError`), and the matrix maths (`mat_mul`, `mat_inverse`, `mat_from_trs`,
  `trs_from_mat`, ...).

## Status

Groundwork. The readers are written and tested on tiny made-up models only; nothing in the launcher, modman,
charsel or the game calls them, and no model has been tried in game. The only real file read so far is one VRM
(read locally, counts only). Next (stage 3 of the plan): a later stage takes the Scene, retargets its skeleton to
Link's bones, and writes the game's model and texture files.

## Rules every reader follows

These are the same in every reader, and `tests/test_model_in_common.py` checks them across readers with one
triangle written as OBJ, STL, COLLADA and glTF:

- **Space:** Y up, metres, right-handed. A Z-up file (STL, OBJ with `up="Z"`, COLLADA `Z_UP`) is turned with a
  real rotation, (x, y, z) -> (x, z, -y), never a mirror, so a Blender model that faces -Y ends up facing +Z.
  FBX follows the axes its file names (see its section). PMX is mirrored on Z (it is left-handed) and its
  winding is reversed to match.
- **Front faces:** counter-clockwise, and the vertex normals point the same way. A mirrored transform (negative
  scale) flips the winding back.
- **UVs:** v = 0 is the top of the picture (glTF). OBJ, COLLADA and FBX store it the other way and are flipped;
  PMX and glTF already match.
- **Skin:** 4 bones per vertex, the 4 heaviest kept, weights sum to 1. Joint numbers point into the one scene
  skeleton. Bones list parents before children, and bind = rest (skinned vertices are moved to the rest pose).
- **Units of each format:** glTF/VRM metres; OBJ metres unless `scale=`; STL millimetres unless `unit=`;
  COLLADA its `<unit meter>`; PMX 1 unit = 8 cm; FBX its UnitScaleFactor.
- **Sharing:** `meta["share"]` is "private" for every model from every reader. A licence the file carries
  (`meta["licence"]`, e.g. VRM meta) is kept for the player to see; nothing is shared because of it alone.
- **Facing:** `meta["forward"]` is "+Z" unless the format says otherwise (VRM 0.x faces -Z). OBJ, STL and COLLADA
  carry no facing, so "+Z" there is a guess.

## Broken files

`model_in.load(path)` never shows a traceback. It gives one `ReadError` sentence when:

- the file is missing, is a folder, has no extension, or its kind has no reader;
- the reader finds the file broken (each reader checks counts, offsets and indices against the file's size, so
  a lying count is refused at once instead of filling memory or looping);
- the file is nested too deeply (deep JSON or XML), or too big for the PC;
- any vertex or bone number is NaN or infinite (`scene.check_finite()`);
- anything else goes wrong inside a reader (a bug, or a file stranger than the tests): one sentence for the
  player, with the real error kept attached (`__cause__`) for us.

Every reader's own `read(path)` also turns a missing or locked file into a `ReadError` (`scene.read_file()`;
FBX goes through `load()` for that). Limits on top of the size checks: a glTF accessor with no data behind it
may hold at most about 33 million numbers; a COLLADA scene may grow to at most 50,000 nodes through
`instance_node`, and an `instance_node` that loops back on itself is cut (with a note); a concave face with more
than 128 corners (OBJ, COLLADA, FBX) is split as a fan instead of by ear clipping, which would take hours on a
huge face.

Open: a texture or buffer path may point outside the model's folder (`../textures/skin.png` is common, but so is
an absolute path from the artist's PC). Readers follow it as written; the later stage should warn before it packs
a picture from outside the model's folder into a mod someone might share.

## glTF 2.0, GLB and VRM (`gltf.py`)

`gltf.read(path, fix_bind=True, scene=None)`, written from the public Khronos glTF 2.0 spec and the public VRM 0.x
and VRM 1.0 (VRMC_vrm) specs. Tests: `tests/test_model_in_gltf.py` (tiny made-up models built in the test).

- **Files:** `.gltf` with external or `data:` buffers and pictures, `.glb`, `.vrm`. Online (http) links are
  refused, never fetched. Draco/meshopt compression is refused in one sentence.
- **Accessors:** every component type, normalized ints, byteStride, matrix column padding, sparse accessors,
  accessors with no data (zeros).
- **Meshes:** one per primitive. Triangles, strips and fans (degenerate strip joins dropped); points and lines
  dropped with a note. Primitives that share one vertex pool (VRoid does) keep only their own vertices.
  Unskinned parts move to world space (a mirror flips the winding); a part hanging under a bone is stuck to that
  bone. Missing normals are made.
- **Skeleton:** every skin joint, every VRM humanoid node and their ancestors. If the file's bind pose isn't its
  rest pose, skinned vertices (and morph deltas) move to the rest pose (`fix_bind=False` keeps the file's).
- **Weights:** JOINTS_0/1 + WEIGHTS_0/1; the same bone twice counts once; top 4 kept, renormalised; unweighted
  vertices follow the skin's first joint (noted).
- **Morphs:** kept in `meta["morphs"]` (mesh, name, position and normal deltas), not applied.
- **Materials:** PBR factors and textures, alpha, double-sided, KHR_materials_unlit,
  KHR_materials_emissive_strength. KHR_texture_transform is kept in `material.extras["texture_transform"]` and
  noted, not applied.
- **VRM humanoid:** `meta["humanoid"]` = {VRM bone name: bone index} (VRM 0.x list and VRM 1.0 dict).
- **VRM licence:** `meta["licence"]`: title, authors, allowed_users, commercial, redistribution, modification,
  credit, licence name and URL, the raw meta, and `may_share` (True only when sharing a changed copy is allowed:
  VRM 1.0 `allowRedistribution` + `allowModificationRedistribution`; VRM 0.x a CC licence without ND). Missing
  fields take the spec's private defaults. A plain glTF's `asset.copyright` is kept with `may_share` False.
- **MToon:** VRM 1.0 `VRMC_materials_mtoon` and VRM 0.x `materialProperties`: shader "mtoon", `shade_color`,
  the shade/main/normal/emission textures, VRM 0.x blend and cull modes.
- **VRM extras:** `meta["vrm"]` (version, expressions, spring bone roots) and `meta["thumbnail_texture"]`.

**Checked offline, 2026-11-18:** the tests above, plus one local read of a VRoid VRM 1.0 avatar (counts only,
nothing kept): 19 parts, 25,967 vertices, 37,711 triangles, 263 bones, 54 humanoid bones, 456 morphs, validate()
clean, about 2 seconds.

**Guesses (G), to check later:** VRM 0.x `_Color`/`_ShadeColor` are taken as sRGB and turned linear; `may_share`
reads licences strictly (unknown = private); the user's own VRM must still be hard-flagged private by the target.

## OBJ, STL and COLLADA (`obj.py`, `stl.py`, `dae.py`)

Written from the public format descriptions. Tests: `tests/test_model_in_obj_stl.py`, `tests/test_model_in_dae.py`
(small made-up models only).

### OBJ + MTL (`obj.read(path, up="Y", scale=1.0)`)

- `v` (with the optional `r g b` colour extension), `vt`, `vn`, `f` with every corner form and negative indices;
  `\` line continuation.
- Polygons: a fan when convex, ear clipping when concave (a fan only if clipping fails).
- `o` / `g` start a mesh; `usemtl` splits it (one material per mesh, named `group.material`).
- MTL: `Kd`, `d` / `Tr` (alpha: BLEND below 1, MASK with `map_d`), `Ke`, `map_Kd` (base), `norm` / `map_Bump` /
  `bump` (normal; `norm` wins), `map_Ke` (emissive). Texture options such as `-bm 0.5` are skipped; names may
  contain spaces. Textures are kept as paths next to the model.
- OBJ has no units or axes: read as Y-up metres unless told otherwise. UVs are flipped to glTF (v -> 1 - v).
  Missing normals are computed (smooth).
- `Pr` / `Pm` give roughness / metallic (else roughness from `Ns`, sqrt(2 / (Ns + 2)), and metallic 0).
- Dropped: lines and points, curves and surfaces, smoothing groups, the PBR `map_Pr` / `map_Pm` pictures (glTF
  wants them packed in one picture; that needs pixel work, later).
- A face that points at a missing vertex, a broken number or a file with no faces raises `ReadError` with the line.

### STL (`stl.read(path, unit=0.001, up="Z", smooth=False)`)

- Binary (found by its exact size, even when the header starts with `solid`) and ASCII (several solids = several
  meshes). Binary meshes are named after the file.
- Flat normals from the winding (the stored normal only for zero-area faces). Corners are merged by position
  within a face direction; `smooth=True` merges by position only and averages the normals.
- Default: millimetres, Z up (the 3D-printing habit). Per-face colour bits are ignored.

### COLLADA 1.4 / 1.5 (`dae.read(path, scale=1.0)`)

- `<unit meter>` and `<up_axis>` (X, Y or Z) are converted to metres, Y up.
- Geometry: `triangles`, `polylist`, `polygons` (holes dropped), `trifans`, `tristrips`; normals, every UV set,
  vertex colours. Lines are dropped.
- Materials: profile_COMMON phong / blinn / lambert / constant: diffuse colour or texture, emission, transparency
  (BLEND, or MASK for a textured `transparent`), bump in `<extra>` -> normal, `double_sided` in `<extra>`.
  Textures through `newparam` sampler/surface (1.4), `instance_image` (1.5) or a direct image id; file URIs are
  decoded; embedded `<hex>` images become bytes.
- Scene: node `matrix`, `translate`, `rotate`, `scale` (`lookat`/`skew` dropped), `instance_geometry`,
  `instance_controller`, `instance_node`, `bind_material`. Static meshes are baked into world space (mirrors keep
  their faces the right way round); a static mesh under a joint is bound rigidly to that bone and moved with it
  into the rest pose.
- Skins: `bind_shape_matrix`, joints by sid (under `<skeleton>` first), id or name, `INV_BIND_MATRIX`, and
  `vertex_weights`. One skeleton for the file (every `type="JOINT"` node and every skin joint); each vertex keeps its
  4 heaviest influences, renormalised. The rest pose is taken from the bind matrices, so bind = rest even when the
  file was saved in another pose (a note says so).
- Dropped with a note: morph targets (the base mesh is used), animations, extra vertex colour sets.
- `<contributor>` author / copyright go into `meta["licence"]` like glTF's copyright (`known` and `may_share`
  False); OBJ, STL and DAE models are `meta["share"] = "private"` until the user says otherwise.
- Damaged XML, a file that isn't COLLADA, broken numbers or no meshes raise `ReadError` in one sentence.
- Not yet: `bind_vertex_input` (every texture uses UV set 0), `lookat` / `skew`, physics, cameras, lights.

## PMX, MikuMikuDance (`pmx.py`)

`pmx.read(path, scale=0.08)`, written from the public PMX 2.0/2.1 format description (no code from other tools).
Tests: `tests/test_model_in_pmx.py` (tiny made-up PMX files built in the test).

- **Reads:** UTF-16 and UTF-8 files, any index sizes (1, 2 or 4 bytes), vertices, faces, textures, materials,
  bones, morphs. Display frames, rigid bodies, physics joints and soft bodies are counted and dropped.
- **One mesh per material.** `mesh.extras["pmx_vertices"]` says which PMX vertex each one was.
- **Weights:** BDEF1, BDEF2 and BDEF4 as they are; SDEF is read as BDEF2 and QDEF as BDEF4 (a note says how
  many). Top 4 kept, renormalised.
- **Size and axes:** 1 MMD unit is about 8 cm, so everything is scaled by 0.08. MMD is left-handed: Z is flipped
  (the model then faces +Z, like glTF) and the triangle winding is reversed. Rotations (IK limits, bone morphs) turn
  the other way around x and y.
- **Bones:** the Japanese name is the bone name (the MMD name tables use it); the English one is in
  `bone.extras["name_en"]`. Rest = the bone's position minus its parent's (MMD bones have no rest rotation).
  Bones are reordered only when a parent comes after its child (`meta["pmx_bone_order"]`). IK chains go to
  `meta["ik"]`; inherit, fixed axis and local axes stay in `bone.extras`.
- **Materials:** diffuse colour -> base colour (sRGB to linear), the texture -> `base`, "no culling" ->
  double-sided, alpha below 1 -> BLEND, `shader` "mmd". Sphere, toon, edge, specular and ambient stay in
  `material.extras`. Texture paths are found next to the `.pmx` file.
- **Extra UVs** (up to 4) become UV sets 1+ (x, y; the full 4 values in `mesh.extras["pmx_extra_uv"]`). With
  the PMX 2.1 vertex-colour material flag, extra UV 1 becomes the vertex colours (**G**: from the format
  description, not seen in a real file yet).
- **Morphs:** kept in `meta["morphs"]`, not applied. Vertex and UV morphs are listed per mesh and vertex.
- **Licence:** PMX has no licence fields. The model comments (Japanese and English), where authors write their
  terms, are in `meta["licence_text"]`; `meta["licence"]` stays None and `meta["share"]` is "private" until the
  user says the model may be shared.
- **Not read:** PMD (the older format: save it as PMX in PMX Editor first).

## FBX, binary and ASCII (`fbx.py`)

`fbx.read(path)`, written from the public FBX layout (no FBX SDK, no code from other tools), standard library only
(`struct`, `zlib`). Tests: `tests/test_model_in_fbx.py` (tiny FBX files built in the test: binary 7400 and 7500
layouts, raw and zlib arrays, and ASCII).

- **Files:** binary FBX 7.x (32-bit node offsets before 7500, 64-bit from 7500; arrays raw or zlib) and ASCII FBX
  7.x. FBX 6.x and older are refused with a "re-export" sentence.
- **Axes and units:** `GlobalSettings` UpAxis/FrontAxis/CoordAxis (+ signs) and UnitScaleFactor (cm per unit) are
  turned into Y up, +Z front, metres. A left-handed file is mirrored and its winding flipped. `meta["axes"]` keeps
  what the file said.
- **Meshes:** `Vertices` + `PolygonVertexIndex` (polygons: fan when convex, ear clipping when concave, fan above
  128 corners). Normal, UV (every set) and colour layers with ByPolygonVertex, ByVertice, ByPolygon or AllSame and
  Direct or IndexToDirect. One Scene mesh per material (`LayerElementMaterial`). UVs are flipped to the glTF
  convention. Missing normals are computed (a note says so). Meshes are baked into world space.
- **Nodes:** Lcl Translation/Rotation/Scaling, PreRotation, PostRotation, rotation/scaling pivots and offsets,
  all six Euler RotationOrders (spheric is read as XYZ, with a note), Geometric Translation/Rotation/Scaling.
  The hierarchy comes from `Connections` (OO, OP).
- **Skins:** Skin + Cluster deformers: `Indexes`, `Weights`, `TransformLink` (bone world at bind) and `Transform`
  (stored relative to the bone, so the mesh's bind world is TransformLink x Transform). The skeleton is every
  cluster bone, every LimbNode and their parents. Bones get their rest pose from TransformLink, so bind = rest
  even when the file was saved in another pose. Top 4 weights, renormalised. A static mesh under a bone (a sword
  in a hand) is bound rigidly to that bone.
- **Materials:** DiffuseColor x DiffuseFactor, Opacity (or TransparencyFactor x TransparentColor) -> BLEND,
  EmissiveColor x EmissiveFactor. Textures on DiffuseColor -> `base`, NormalMap/Bump -> `normal`, EmissiveColor
  -> `emissive`; embedded `Video` Content becomes texture bytes, else RelativeFilename / FileName is looked up
  next to the model.
- **Dropped with a note:** animations (`meta["animations"]` lists the takes and counts the curves; nothing is
  read), blend shapes, cameras, lights, curves and NURBS, edges and smoothing groups, InheritType Rrs.
- **Broken files:** every offset, count and array size is checked against the file (and zlib unpacking against a
  size budget), so a broken or hostile file gives one `ReadError` sentence and never hangs.
- **Not tried yet:** a real exported file (Blender, Maya, Mixamo, 3ds Max); everything above is checked against
  the hand-built test files only (**G** until then).

## Weapon target (`tools/model_targets/weapon.py`)

Converter stage 1: a sword-like rigid model in any format above -> a one-handed weapon mod, built on the player's PC
from their own game files. Tests: `tests/test_model_targets_weapon.py` (a made-up GLB and OBJ sword and
test_ak47's made-up game, with a stats file added to its donor pack).

    python tools/model_targets/weapon.py build <model> --name "My Katana" --attack 30 --donor Weapon_Sword_047 \
        --out <folder> [--desc ...] [--length 1.1] [--durability 40] [--mesh NAME ...] [--game <Savage Wind folder>]
        [--share <folder>]

- **Meshes:** every mesh with triangles, or only the `--mesh` ones. One rigid vertex list (skins are ignored, a
  note says so). Each material's base colour picture (or its colour, linear -> sRGB) is one atlas slot (at most 8),
  as `ak47_build.gather` does for GLB. Parts with open edges or double-sided materials also get back faces.
- **Orientation:** the longest side is the blade, the next-longest the edge. The grip is the end nearest a bone or
  empty named like `grip`, `handle` or `hilt`; with none, the end nearest the model's origin. Then
  `ak47_build.to_weapon` turns it, scales it to `--length` metres (default: the donor's length) and puts the
  grip end in the donor's hilt. The grip marker only picks the end; a pommel behind it is not moved forward yet.
- **Mod:** the donor's model, textures and icon in their own formats (`ak47_build.build_model`, `make_textures`,
  `build_icon`); the donor's actor pack with our model list, actor link and stats file
  (`dragonbreath_build.actor_pack`; attack, its twin value and `--durability` set, the rest stays the donor's);
  actor info (`attackPower`), the `IsGet_` flag, name/description texts in the donor's `ActorType/<profile>` file,
  size entries. Output: `<out>/<mod id>/` (default id `weapon.<name>`, actor `Weapon_Sword_<Name>`).
- **Never shared:** the built mod holds parts of the player's game (the donor's pack files), so it is private.
  `--share <folder>` writes a recipe instead (the model file + `recipe.json`: target, model SHA-256, stats, texts,
  licence; no game files), and only when the model may be shared: Scene meta `licence` with `may_share` true, or no
  licence data and meta `share` == "shareable" (the player confirmed). A licence that says no always wins; then the
  mod is built private only and a note says why.
- **Not yet (G):** placing it in the world, two-handed / spear / shield targets, several shapes and materials (the
  plan's stage 0 item 10), a real model checked in the game.
