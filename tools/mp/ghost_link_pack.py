#!/usr/bin/env python3
"""The Link body for the ghost: an actor pack built on your PC from your own game files. Works in the game
(docs/ghost.md, tenth round: Link's body walks, runs and swings next to you).

    python tools/mp/ghost_link_pack.py build              build it into out\\ghost-link-mod (a RomFS mod folder)
    python tools/mp/ghost_link_pack.py build --romfs "D:\\SavageWind\\Savage Wind\\extracted\\romfs" --out D:\\mods\\ghost-link
    python tools/mp/ghost_link_pack.py names              the animation names it has (our name -> Link's clip)
    python tools/mp/ghost_link_pack.py check out\\ghost-link-mod    read a built pack back and list what is in it

The in-game check `python tools/mp/ghost_ingame_check.py --link` builds it and deploys it to the check's own overlay
(out\\ghost-check-overlay), used only for that game run; it passed there (14 PASS, 1 SKIP). Before you deploy it with
your other mods, keep in mind: the pack replaces the default stand-in's actor
(Dm_Npc_Sheiker_Searcher_M_001, the cutscene copy of a Sheikah man that nothing else in the game uses), so while it
is deployed every ghost is the Link body, plain `--ghost` too, and if the pack crashes the game, every use of the
ghost does until you remove it. Without it the Sheikah man is back. `client.py --ghost-link` uses it, with
animation sync.

What it builds (plan: out/research/ghost-link-plan.md):
- the Sheikah man's actor link, with our model list and animation list, no physics, no Mii look, no animation info;
  his AI program (none: a cutscene dummy) is kept;
- our model list: the pause-menu Link's (Link's model) with the Sheikah man's animation slots (--slots pause keeps
  the pause-menu Link's own, the plan's fallback 8a);
- our animation list: Link's animation file (`Player_Animation`, always loaded) and one small animation set per name
  in ANIMS (one clip each), with blend times between them.
The player's own animation sets are not used: they need inputs only the player code gives. Link looks bald and in
underwear at first (hair and clothes are separate armor actors), and holds nothing.

Every file it writes comes from your own game files at build time; nothing from the game is in this repo. Keep the
output in out\\ (never committed) or another folder of your own.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

_TOOLS = str(Path(__file__).resolve().parent.parent)
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
import aamp  # noqa: E402
from aamp import F32, INT, STRING32, STRING64, PList, PObject, Param, name_hash  # noqa: E402
from wiiu_convert import FormatError, sarc_build, sarc_files, yaz0_compress, yaz0_decompress  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
LINK_ACTOR = "Dm_Npc_Sheiker_Searcher_M_001"   # the name the pack replaces (also client.GHOST_LINK_ACTOR, ghost.c)
DONOR_LINK = "PauseMenuPlayer"                 # the pause-menu Link: Link's model list and animation file
OURS = "SwGhostLink"                           # our model list, animation list and animation set file names
SIZE_LIMIT = 348_448   # bytes: the game's size table entry for this pack (the research read it); ours must stay below
DEFAULT_ROMFS = Path.home() / "Documents" / "SavageWind" / "Savage Wind" / "extracted" / "romfs"
DEFAULT_OUT = REPO / "out" / "ghost-link-mod"
VERSION = "0.1.0"

# (our name, Link's clip in Player_Animation, blend frames into it). Most of our names are the player's own animation
# names (what `ghost self` reads on the other PC), so the ghost replays them as they are. Not the player's (checked
# against his animation list, 399 names): Walk and Run, the ghost mod's own speed rule (the player has one `Move` for
# walking, running and sprinting); RunGear2, only for `ghost anim`; and the *_Lsword and *_Spear swings, which the
# receiving client picks by the sender's weapon (client.WEAPON_ANIMS: the player plays CutS1... whatever he holds).
# Clip names: the research's table (out/research/ghost-link-plan.md); the in-game check plays each one.
ANIMS: tuple[tuple[str, str, float], ...] = (
    ("Wait", "Nml_Wait", 8.0),
    ("Walk", "Nml_Move_Walk", 8.0),
    ("Run", "Nml_Move_Run", 8.0),
    ("RunGear2", "Nml_Move_Dash", 8.0),
    ("CutS1", "Sword_Attack_S1", 3.0),
    ("CutS2", "Sword_Attack_S2", 3.0),
    ("CutS3", "Sword_Attack_S3", 3.0),
    ("CutSF", "Sword_Attack_SF", 3.0),
    ("CutDash", "Sword_Attack_Dash", 3.0),
    ("CutJumpSt", "Sword_Attack_Jump_F_St", 3.0),
    ("CutCharge", "Sword_Attack_Charge", 3.0),
    ("CutS1_Lsword", "Lsword_Attack_S1", 3.0),
    ("CutSF_Lsword", "Lsword_Attack_SF", 3.0),
    ("CutS1_Spear", "Spear_Attack_S1", 3.0),
    ("CutS2_Spear", "Spear_Attack_S2", 3.0),
    ("CutSF_Spear", "Spear_Attack_SF", 3.0),
    ("BowCharge", "Bow_Attack_Charge", 4.0),
    ("BowShoot", "Bow_Attack_Shoot", 2.0),
    ("GuardJust", "Shield_Guard_Just", 2.0),
    ("Jump", "Nml_Wait_Jump", 3.0),
    ("Fall", "Fall", 6.0),
    ("Land", "Nml_Wait_Land", 3.0),
    ("ParashawlGlide", "Float_Float", 6.0),
    ("ClimbWait", "Climb_Wall_Wait_FootL", 6.0),
    ("ClimbMove", "Climb_Wall_Move_U", 6.0),
    ("SwimWait", "Swim_Wait", 8.0),
    ("SwimMove", "Swim_Move_Slow_F", 8.0),
    ("SwimDash", "Swim_Move_Fast_F", 8.0),
    ("SquatWait", "Squat_Wait_FootL", 6.0),
    ("SquatMove", "Squat_Move_Walk", 6.0),
    ("DamageS", "Nml_Damage_S_F", 2.0),
    ("Dead", "Nml_Die", 4.0),
    ("WeaponEquipOn", "Equip_Sword_On_Natural", 4.0),
)
LOCOMOTION = ("Wait", "Walk", "Run", "RunGear2")
# Blend frames between the walk-cycle sets (the Sheikah man's list uses 8 into Wait and 10-15 between Walk and Run)
PAIR_FRAMES = {("Walk", "Run"): 12.0, ("Run", "Walk"): 12.0, ("Run", "RunGear2"): 10.0, ("RunGear2", "Run"): 10.0,
               ("Walk", "Wait"): 10.0, ("Run", "Wait"): 10.0}

# The users of the actor link we change, and the files of the donor's pack we leave out (every other user of the
# Sheikah man's actor link is Dummy, except his AI program, which is copied, and his profile, which has no file).
USERS = {"ModelUser": OURS, "ASUser": OURS, "PhysicsUser": "Dummy", "UMiiUser": "Dummy", "AnimationInfo": "Dummy"}
DROPPED_FOLDERS = ("Actor/ModelList/", "Actor/ASList/", "Actor/AS/", "Actor/Physics/", "Actor/UMii/",
                   "Actor/AnimationInfo/", "Physics/")


class PackError(RuntimeError):
    pass


# ------------------------------------------------------------------------------------------------ reading the game

def read_pack(data: bytes) -> dict[str, bytes]:
    """The files in an actor pack (.sbactorpack: Yaz0 around a SARC)."""
    if data[:4] == b"Yaz0":
        data, _ = yaz0_decompress(data)
    return dict(sarc_files(data))


def _sarc_member(path: Path, name: str) -> bytes | None:
    """One file from an uncompressed SARC on disk (like Pack/TitleBG.pack), reading only its index and that file."""
    with open(path, "rb") as f:
        head = f.read(0x20)
        if head[:4] != b"SARC" or head[6:8] != b"\xff\xfe":
            return None
        doff = struct.unpack_from("<I", head, 0xC)[0]
        count = struct.unpack_from("<H", head, 0x14 + 6)[0]
        f.seek(0)
        index = f.read(doff)
        names_at = 0x14 + 0xC + 16 * count + 8
        for k in range(count):
            _h, attr, start, end = struct.unpack_from("<IIII", index, 0x14 + 0xC + 16 * k)
            at = names_at + (attr & 0xFFFFFF) * 4
            stop = index.find(b"\0", at)
            if attr >> 24 and index[at:stop].decode("utf-8", "replace") == name:
                f.seek(doff + start)
                return f.read(end - start)
    return None


def find_pack(romfs: Path, actor: str) -> bytes:
    """An actor's pack from the game's files: Actor/Pack/<actor>.sbactorpack, or inside Pack/TitleBG.pack."""
    loose = romfs / "Actor" / "Pack" / f"{actor}.sbactorpack"
    if loose.is_file():
        return loose.read_bytes()
    title = romfs / "Pack" / "TitleBG.pack"
    if title.is_file():
        data = _sarc_member(title, f"Actor/Pack/{actor}.sbactorpack")
        if data is not None:
            return data
    raise PackError(f"No pack for {actor} in {romfs} (Actor/Pack or Pack/TitleBG.pack). Is that the game's romfs?")


def link_target(pack: dict[str, bytes], actor: str) -> tuple[PList, PObject]:
    name = f"Actor/ActorLink/{actor}.bxml"
    if name not in pack:
        raise PackError(f"{actor}'s pack has no {name}")
    root = aamp.load(pack[name])
    lt = root.object("LinkTarget")
    if lt is None:
        raise PackError(f"{name} has no LinkTarget")
    return root, lt


def user(lt: PObject, key: str) -> str:
    v = lt.get_value(key)
    if not isinstance(v, str):
        raise PackError(f"the actor link has no {key}")
    return v


# ------------------------------------------------------------------------------------------------ making our files

def as_file(clip: str) -> bytes:
    """An animation set with one clip node (node type 67, the clip by name), as in the game's simplest sets."""
    params = PObject()
    params.set("TypeIndex", INT, 67)
    params.set("FileName", STRING64, clip)
    element = PList(objects={name_hash("Parameters"): params})
    return aamp.dump(PList(lists={name_hash("Elements"): PList(lists={name_hash("Element0"): element})}))


def blend_frames(pre: str, post: str, frames: dict[str, float]) -> float:
    return PAIR_FRAMES.get((pre, post), frames[post])


def as_list(donor: PList, anims=ANIMS) -> bytes:
    """Our animation list: the donor's animation files (Player_Animation), our sets, and blend times (CFDefines:
    for each set we switch from, the frames into each other set; '' = any other)."""
    add = donor.list("AddReses")
    if add is None or not add.objects:
        raise PackError(f"{DONOR_LINK}'s animation list has no AddReses")
    # Sorted by name, like every list of the game's (the Sheikah man's, the player's): the game finds a set by a
    # binary search over this order. Unsorted (the first in-game check, 2026-11-07), only the names the search
    # happened to land on played (ClimbMove), every other one was "not one of its animations".
    anims = sorted(anims, key=lambda a: a[0].encode())
    defines = PList()
    for i, (name, _clip, _f) in enumerate(anims):
        o = PObject()
        o.set("Name", STRING64, name)
        o.set("Filename", STRING64, f"{OURS}_{name}")
        defines.objects[name_hash(f"ASDefine_{i}")] = o
    frames = {name: f for name, _clip, f in anims}
    cf = PList()
    for i, (pre, _clip, _f) in enumerate(anims):
        posts = PList()
        targets = [""] + sorted((p for p in LOCOMOTION if p != pre and p in frames), key=str.encode)
        for k, post in enumerate(targets):
            o = PObject()
            o.set("Name", STRING32, post)
            o.set("Frame", F32, 6.0 if post == "" else blend_frames(pre, post, frames))
            o[0xCF996FB7] = Param(F32, -1.0)       # (always -1 in the game's lists)
            posts.objects[name_hash(f"CFPost_{k}")] = o
        pre_o = PObject()
        pre_o.set("Name", STRING32, pre)
        cf.lists[name_hash(f"CFDefine_{i}")] = PList(lists={name_hash("CFPosts"): posts},
                                                    objects={name_hash("CFPre"): pre_o})
    root = PList(lists={name_hash("AddReses"): add, name_hash("ASDefines"): defines, name_hash("CFDefines"): cf})
    return aamp.dump(root)


def model_list(link_ml: PList, sheikah_ml: PList, slots: str) -> bytes:
    """Link's model list (the pause-menu Link's) with the Sheikah man's animation slots, or its own (slots='pause')."""
    root = PList(lists=dict(link_ml.lists), objects=dict(link_ml.objects))
    key = name_hash("AnmTarget")
    if key not in root.lists:
        raise PackError(f"{DONOR_LINK}'s model list has no AnmTarget")
    if slots == "sheikah":
        if key not in sheikah_ml.lists:
            raise PackError(f"{LINK_ACTOR}'s model list has no AnmTarget")
        root.lists[key] = sheikah_ml.lists[key]
    md = root.list("ModelData")
    folder = md and md.list("ModelData_0") and md.list("ModelData_0").object("Base")
    if not folder or folder.get_value("Folder") != "Link":
        raise PackError(f"{DONOR_LINK}'s model list doesn't use Link's model; is this BotW 1.9.0?")
    return aamp.dump(root)


def build_pack(sheikah: dict[str, bytes], link: dict[str, bytes], slots: str = "sheikah") -> dict[str, bytes]:
    """The files of our pack, from the Sheikah man's pack and the pause-menu Link's."""
    if slots not in ("sheikah", "pause"):
        raise PackError("--slots is sheikah or pause")
    al_root, lt = link_target(sheikah, LINK_ACTOR)
    if user(lt, "ModelUser") == OURS:
        raise PackError(f"{LINK_ACTOR}'s pack is already ours: point --romfs at the game's own files, not a mod folder")
    sk_model, sk_ai = user(lt, "ModelUser"), user(lt, "AIProgramUser")
    _, plt = link_target(link, DONOR_LINK)
    pm_model, pm_as = user(plt, "ModelUser"), user(plt, "ASUser")
    need = {f"Actor/ModelList/{sk_model}.bmodellist": sheikah, f"Actor/ModelList/{pm_model}.bmodellist": link,
            f"Actor/ASList/{pm_as}.baslist": link}
    for name, pack in need.items():
        if name not in pack:
            raise PackError(f"missing {name}")
    for key, value in USERS.items():
        p = lt.get(name_hash(key))
        if p is None:
            raise PackError(f"the actor link has no {key}")
        lt[name_hash(key)] = Param(p.type, value)
    out: dict[str, bytes] = {f"Actor/ActorLink/{LINK_ACTOR}.bxml": aamp.dump(al_root)}
    # what the kept users need: every file of the Sheikah man's pack outside the folders we replace or drop (his AI
    # program, DemoDummy)
    for name, data in sheikah.items():
        if not name.startswith(DROPPED_FOLDERS) and not name.startswith("Actor/ActorLink/"):
            out[name] = data
    if f"Actor/AIProgram/{sk_ai}.baiprog" not in out and sk_ai != "Dummy":
        raise PackError(f"{LINK_ACTOR}'s pack has no AI program {sk_ai}")
    out[f"Actor/ModelList/{OURS}.bmodellist"] = model_list(
        aamp.load(link[f"Actor/ModelList/{pm_model}.bmodellist"]),
        aamp.load(sheikah[f"Actor/ModelList/{sk_model}.bmodellist"]), slots)
    out[f"Actor/ASList/{OURS}.baslist"] = as_list(aamp.load(link[f"Actor/ASList/{pm_as}.baslist"]))
    for name, clip, _f in ANIMS:
        out[f"Actor/AS/{OURS}_{name}.bas"] = as_file(clip)
    return out


def pack_bytes(files: dict[str, bytes]) -> tuple[bytes, int]:
    """(the .sbactorpack: Yaz0 around a SARC, unpacked size)."""
    sarc = sarc_build(sorted(files.items()), data_align=8)
    return yaz0_compress(sarc), len(sarc)


MOD_TOML = f'''# The Link body for the ghost (docs/ghost.md). Built on your PC from your own game files by
# tools/mp/ghost_link_pack.py: never share or commit this folder.
# Checked in the game with python tools/mp/ghost_ingame_check.py --link (docs/ghost.md, tenth round). While it is
# deployed, every ghost is the Link body (plain --ghost too); if it ever crashes the game, remove it.
[mod]
id = "botw-mod-loader.ghost-link-body"
name = "Ghost: Link body (experimental)"
version = "{VERSION}"
authors = ["giruka"]
description = "Replaces the unused cutscene Sheikah man {LINK_ACTOR} with Link's body, for the ghost mod: every ghost then uses it. Checked in game on one PC (ghost in-game check)."
game_versions = ["1.9.0"]

[data]
romfs = "romfs"
'''


NEXT = ("Checked in the game (python tools\\mp\\ghost_ingame_check.py --link deploys it to its own overlay for one "
        "run). While it is deployed with your mods, every ghost is the Link body, and if it ever crashes the game, "
        "every use of the ghost does.")


def build(romfs: Path, out: Path, slots: str = "sheikah", say=print) -> dict:
    """Build the mod folder. Returns a report (pack path, packed and unpacked size, file count)."""
    romfs, out = Path(romfs).resolve(), Path(out).resolve()
    if out == romfs or romfs in out.parents or out in romfs.parents:
        raise PackError("--out must be a folder of your own, not inside the game's files")
    try:
        sheikah = read_pack(find_pack(romfs, LINK_ACTOR))
        if link_target(sheikah, LINK_ACTOR)[1].get_value("ModelUser") == OURS:
            raise PackError(f"{LINK_ACTOR}'s pack in {romfs} is already ours: point --romfs at the game's own files")
        files = build_pack(sheikah, read_pack(find_pack(romfs, DONOR_LINK)), slots)
    except (FormatError, aamp.AampError) as e:
        raise PackError(f"a game file didn't read as expected: {e}") from None
    data, unpacked = pack_bytes(files)
    if unpacked >= SIZE_LIMIT:
        raise PackError(f"the pack is {unpacked} bytes unpacked; the game allows {SIZE_LIMIT} for this name")
    target = out / "romfs" / "Actor" / "Pack" / f"{LINK_ACTOR}.sbactorpack"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    (out / "mod.toml").write_text(MOD_TOML, encoding="utf-8")
    say(f"Built {target} ({len(files)} files, {unpacked:,} bytes unpacked, the game allows {SIZE_LIMIT:,}).")
    return {"pack": str(target), "files": len(files), "unpacked": unpacked, "packed": len(data)}


def check(folder: Path) -> dict:
    """Read a built pack back: its animation names and clips, sizes, the actor link's users."""
    folder = Path(folder)
    target = folder / "romfs" / "Actor" / "Pack" / f"{LINK_ACTOR}.sbactorpack"
    if not target.is_file():
        target = folder if folder.is_file() else target
    if not target.is_file():
        raise PackError(f"no {LINK_ACTOR}.sbactorpack in {folder}")
    raw = target.read_bytes()
    unpacked = len(yaz0_decompress(raw)[0]) if raw[:4] == b"Yaz0" else len(raw)
    files = read_pack(raw)
    _, lt = link_target(files, LINK_ACTOR)
    users = {k: lt.get_value(k) for k in ("ModelUser", "ASUser", "PhysicsUser", "AIProgramUser")}
    asl = aamp.load(files[f"Actor/ASList/{users['ASUser']}.baslist"])
    names = {}
    for o in asl.list("ASDefines").objects.values():
        bas = files.get(f"Actor/AS/{o.get_value('Filename')}.bas")
        clip = None
        if bas:
            el = aamp.load(bas).list("Elements").list("Element0").object("Parameters")
            clip = el.get_value("FileName")
        names[o.get_value("Name")] = clip
    return {"pack": str(target), "unpacked": unpacked, "limit": SIZE_LIMIT, "users": users, "names": names,
            "files": sorted(files)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the Link body for the ghost from your own game files.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build the RomFS mod folder")
    b.add_argument("--romfs", type=Path, default=DEFAULT_ROMFS, help=f"the game's extracted romfs (default {DEFAULT_ROMFS})")
    b.add_argument("--out", type=Path, default=DEFAULT_OUT, help="the mod folder to write (default out\\ghost-link-mod)")
    b.add_argument("--slots", choices=("sheikah", "pause"), default="sheikah",
                   help="animation slots: the Sheikah man's (default) or the pause-menu Link's (the fallback)")
    sub.add_parser("names", help="list the animation names and Link's clips")
    c = sub.add_parser("check", help="read a built pack back")
    c.add_argument("folder", type=Path, nargs="?", default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "names":
            for name, clip, frames in ANIMS:
                print(f"{name:16} {clip:28} blend {frames:g} frames")
            return 0
        if args.cmd == "build":
            build(args.romfs, args.out, args.slots)
            print(NEXT)
            return 0
        r = check(args.folder)
        print(f"{r['pack']}: {r['unpacked']:,} bytes unpacked (the game allows {r['limit']:,}), "
              f"{len(r['files'])} files, users {r['users']}")
        for name, clip in r["names"].items():
            print(f"  {name:16} {clip}")
        return 0
    except (PackError, OSError) as e:
        print(f"ghost_link_pack: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
