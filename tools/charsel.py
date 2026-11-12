#!/usr/bin/env python3
"""Who am I: pick who your friends see you as, from a wheel (docs/charsel.md). Each PC builds the bodies itself, from
its own game files; only the character's id goes over the network.

    python tools/charsel.py list                      the characters on the wheel, by group, and how each looks
    python tools/charsel.py build [ID ...]            build the character packs into out\\charsel-packs (a RomFS mod
                                                      folder: mod.toml, romfs\\, charsel.json) from your own game files
        --romfs DIR      the game's extracted romfs (default: Documents\\SavageWind\\Savage Wind\\extracted\\romfs)
        --out DIR        where the mod folder goes (default out\\charsel-packs; never inside the game's files)
        --library DIR    your mod library (default: next to Savage Wind): finds the Linkle mod there (read only)
        --slot ID=ACTOR  put a character on another free actor name (if the in-game check says one doesn't load)
        --model-copies   also Link and Linkle side by side (renamed model copies; not yet tried in the game)
        --monster-link-clips   monsters climb, glide and swim with Link's clips (not yet tried in the game)
    python tools/charsel.py check [--out DIR]         read every built pack back, no game: sorted animation lists, one
                                                      clip each, sizes, the actor link
    python tools/charsel.py wheel                     the Who am I wheel in a window (arrow keys or a controller)
    python tools/charsel.py pick ID                   the same choice without a window
    python tools/charsel.py where                     your choice and where it is kept

A character pack is a tiny actor pack (14-19 KB unpacked, 6-8 KB packed) that names the game's own model and
animation files: the donor's
model list, an animation list with one small animation set per ghost name (the 33 names of the Link
body, tools/mp/ghost_link_pack.py), no AI (the cutscene dummy's), no physics. People's models use Link's bone names, so
Link's clips fill every move they don't have (through the game's own retarget); monsters use their own clips.
Each pack goes on a free actor name the game knows but never uses (a cutscene name with no pack), so the ghost mod
spawns it with `ghost spawn <name>`. Nothing from the game is in this repo: recipes are names only. Keep the output
in out\\ (never committed) or your own mod library, and never share it: your friend builds their own. The build
leaves built.txt next to mod.toml, so Export as .breath (tools/breath.py) refuses to share it.

What this PC shows for a friend's pick comes only from the characters its game really gets: the library copy of
this mod (modman add out\\charsel-packs), enabled, with its pack in the deployed overlay (here_catalog). Anything
else is the Link body.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

VERSION = "0.1.0"
MOD_ID = "botw-mod-loader.characters"
DEFAULT_OUT = REPO / "out" / "charsel-packs"
DEFAULT_CACHE = REPO / "out" / "charsel-cache.json"
DEFAULT_ROMFS = Path.home() / "Documents" / "SavageWind" / "Savage Wind" / "extracted" / "romfs"
DEFAULT_LIBRARY = Path.home() / "Documents" / "SavageWind" / "modloader"
CATALOG = "charsel.json"          # in the built mod folder: which characters are built, on which actor name
STATE = "charsel.json"            # in the launcher's folder (%LOCALAPPDATA%\Breathalyzer): your choice
ENV_HOME = "BREATHALYZER_HOME"    # (the launcher's: tests point it elsewhere)
LINK_ACTOR = "Dm_Npc_Sheiker_Searcher_M_001"   # the Link body (ghost_link_pack.LINK_ACTOR): charsel never writes it
TEMPLATE = LINK_ACTOR             # its actor link and cutscene-dummy AI program come from the game's own pack
PLAYER_ANIM = "Player_Animation"  # Link's animation file (always loaded, in TitleBG)
PACK_LIMIT = 64 * 1024            # bytes unpacked: ours are 14-19 KB (the Link body's size-table entry is 348,448)
BUILT_MARK = "built.txt"          # next to mod.toml: made from the game's files (tools/breath.py BUILT_MARKS: never
#                                   exported as a .breath)
BUILT_TEXT = ("Built on this PC from its own game files (tools/charsel.py). Never share it: each player builds their "
              "own.\n")
MAX_ID = 24
_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,23}")

# ------------------------------------------------------------------------------------------------ the characters

# The character's own clips for some of the 33 ghost names (the Link body's; ghost_link_pack.ANIMS). Clip names were
# checked against the user's own files by out/research/charsel_pack_proto.py (24 recipes: every clip found, no clash).
UMII_M = (("Wait", "Default_Wait"), ("Walk", "Default_Move_Walk"), ("Run", "Common_Move_Run"),
          ("RunGear2", "Common_Move_Run_Gear2"), ("SquatWait", "Common_Crouch"), ("DamageS", "Common_Stagger_F_L"))
UMII_W = tuple((n, "Common_Move_Escape" if n == "RunGear2" else c) for n, c in UMII_M)
BOKO = (("Wait", "Wait"), ("Walk", "Walk"), ("Run", "Run"), ("RunGear2", "Run"), ("Jump", "Jump_Start"),
        ("Fall", "Jump_Loop"), ("Land", "Jump_End"), ("SquatWait", "Wait_Squat_Loop"), ("CutS1", "Attack_Small"),
        ("CutS2", "Attack_Small_Two"), ("CutS3", "Attack_Small"), ("CutSF", "Attack_Big_Quick"),
        ("CutDash", "Attack_Run_Sword"), ("CutJumpSt", "Attack_Big_Quick"), ("CutCharge", "Attack_Big_Quick"),
        ("CutS1_Lsword", "Attack_Side_Lsword"), ("CutSF_Lsword", "Attack_Side_Lsword"),
        ("CutS1_Spear", "Attack_Small_Spear"), ("CutS2_Spear", "Attack_Small_Spear"), ("CutSF_Spear", "Attack_Run_Spear"),
        ("BowCharge", "Attack_Bow_Start"), ("BowShoot", "Attack_Bow_End"), ("GuardJust", "Guard_Just"),
        ("DamageS", "Damage_Small"), ("Dead", "Down_Wait"), ("WeaponEquipOn", "Weapon_Catch"))
MOBLIN = (("Wait", "Wait"), ("Walk", "Walk"), ("Run", "Run"), ("RunGear2", "Run"), ("Jump", "Jump_Start"),
          ("Fall", "Jump_Loop"), ("Land", "Jump_End"), ("SquatWait", "Wait_Squat_Loop"), ("CutS1", "Attack_Small"),
          ("CutS2", "Attack_Small_Quick"), ("CutS3", "Attack_Side"), ("CutSF", "Attack_Center"),
          ("CutDash", "Attack_Step"), ("CutJumpSt", "Attack_Center"), ("CutCharge", "Attack_Double_Start"),
          ("CutS1_Lsword", "Attack_Two_Lsword"), ("CutSF_Lsword", "Attack_Two_Lsword"), ("CutS1_Spear", "Attack_Small"),
          ("CutS2_Spear", "Attack_Small_Quick"), ("CutSF_Spear", "Attack_Center"), ("BowCharge", "Attack_Bow_Start"),
          ("BowShoot", "Attack_Bow_End"), ("GuardJust", "Guard_Just_Shield"), ("DamageS", "Damage_Sword"),
          ("Dead", "Down_Wait"), ("WeaponEquipOn", "Weapon_Catch_R"))


def _with(base: tuple, **kw) -> tuple:
    d = dict(base)
    d.update(kw)
    return tuple(d.items())


@dataclass(frozen=True)
class Recipe:
    """One character. Names only (the game's own file and clip names), like ghost_link_pack.ANIMS: no game data."""
    id: str
    name: str                 # on the wheel (up to 20 characters)
    cat: str                  # its group on the wheel
    kind: str                 # "pack": our pack from a donor; "link": the Link body; "linkle": a player-model mod
    donor: str = ""           # the actor whose model list (and Mii-style look file) we use
    anims: tuple = ()         # animation files, in order (the first with a clip is used [G])
    own: tuple = ()           # (ghost name, clip): the character's own clips
    rest: str = "link"        # every other ghost name: "link" (Link's clip through the retarget) or "speed" (none)
    load_mb: float = 0.0      # models, textures and animation files the game loads to show it (study 1, [V])
    aliases: tuple = ()       # other words for `iam` and `charsel pick`
    looks: str = ""           # one honest line: what looks right and what doesn't
    v: int = 1                # bump when the recipe changes: a build keeps an older pack only while its v matches
    #                           (it also travels in the look message, for the log; receivers don't compare it)


RECIPES: dict[str, Recipe] = {r.id: r for r in (
    Recipe("link", "Link", "Hylians", "link", aliases=("hero",),
           looks="Works in the game (the Link body). Bald and in underwear: hair and clothes are armor actors."),
    Recipe("linkle", "Linkle", "Hylians", "linkle", aliases=("linkel",),
           looks="Needs the Linkle mod on your friend's PC too; without it they see Link."),
    Recipe("zelda", "Zelda", "Hylians", "pack", "Dm_Npc_Zelda_Sibyl",
           (("UMii_Common_Body_W_Animation", True), (PLAYER_ANIM, True)), UMII_W, load_mb=10.6, aliases=("princess",),
           looks="Her own stand, walk, run; Link's moves for the rest. The dress is stiff."),
    Recipe("hylian", "Hylian", "Hylians", "pack", "Npc_HatenoVillage004",
           (("UMii_Common_Body_M_Animation", True), (PLAYER_ANIM, True)), UMII_M, load_mb=11.5,
           aliases=("hateno", "villager"), looks="A Hateno villager: his own walk, Link's moves for the rest."),
    Recipe("sheikah", "Paya (Sheikah)", "Races", "pack", "Npc_Kakariko002",
           (("UMii_Common_Body_W_Animation", True), (PLAYER_ANIM, True)), UMII_W, load_mb=6.4, aliases=("paya",),
           looks="Her own stand, walk, run; Link's moves for the rest."),
    Recipe("gerudo", "Urbosa (Gerudo)", "Races", "pack", "Dm_Npc_Gerdo_Hero",
           (("UMii_Gerudo_BodyC_W_Animation", True), ("UMii_Common_Body_W_Animation", True), (PLAYER_ANIM, True)),
           UMII_W, load_mb=16.2, aliases=("urbosa",),
           looks="Tall: the heaviest one (16 MB). Link's moves may sink her hips a little [G]."),
    Recipe("goron", "Daruk (Goron)", "Races", "pack", "Dm_Npc_Goron_Hero",
           (("UMii_Goron_BodyC_M_Animation", True), (PLAYER_ANIM, True)),
           _with(UMII_M, RunGear2="Common_Move_Ball"), load_mb=10.9, aliases=("daruk",),
           looks="Sprints as a rolling ball. Link's climb and glide on a Goron body may look odd [G]."),
    Recipe("rito", "Revali (Rito)", "Races", "pack", "Dm_Npc_Rito_Hero",
           (("UMii_Rito_Body_M_Animation", True), ("UMii_Common_Body_M_Animation", True),
            ("Npc_Rito_Teba_Animation", True), (PLAYER_ANIM, True)),
           _with(UMII_M, ParashawlGlide="Npc_Rito_Teba_BattleFly"), load_mb=10.6, aliases=("revali",),
           looks="Glides with real wings (a Rito flying clip)."),
    Recipe("zora", "Mipha (Zora)", "Races", "pack", "Dm_Npc_Zora_Hero",
           (("UMii_Common_Body_W_Animation", True), ("UMii_Zora_BodyC_M_Animation", True), (PLAYER_ANIM, True)),
           _with(UMII_W, SwimWait="Common_Swim_Wait", SwimMove="Common_Move_Swim_Walk",
                 SwimDash="Common_Move_Swim_Run"), load_mb=11.0, aliases=("mipha",),
           looks="Swims with Zora swim clips."),
    Recipe("bokoblin", "Bokoblin", "Monsters", "pack", "Enemy_Bokoblin_Junior",
           (("Enemy_Bokoblin_Animation", False),), BOKO, rest="speed", load_mb=6.2, aliases=("boko",),
           looks="Its own clips (attacks too). No climb, glide or swim pose: it stands or walks there."),
    Recipe("moblin", "Moblin", "Monsters", "pack", "Enemy_Moriblin_Junior",
           (("Enemy_Moriblin_Animation", False),), MOBLIN, rest="speed", load_mb=7.8, aliases=("moriblin",),
           looks="Its own clips. No climb, glide or swim pose. Big: may pop in at the screen's edges."),
)}

CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Hylians", ("link", "linkle", "zelda", "hylian")),
    ("Races", ("sheikah", "gerudo", "goron", "rito", "zora")),
    ("Monsters", ("bokoblin", "moblin")),
)

# Free actor names: in the game's ActorInfo, with no pack anywhere in the base game and no size-table entry (cutscene
# names it never spawns; out/research/charsel-ghost-characters.md [V]). The cutscene-class ones first (the same class
# as today's ghost, which works); the NPC-class and tall-box ones are not yet tried in the game [G].
DEMO_SLOTS = ("Dm_Npc_Sheiker_Searcher_M_000", "Dm663_Npc_Assasin_A", "Dm663_Npc_Assasin_B")
NPC_SLOTS = ("Dm_Npc_UMii_Hylia_BodyS_M_001", "Dm_Npc_UMii_Hylia_BodyS_M_002", "Dm_Npc_UMii_Hylia_BodyS_M_003",
             "Dm_Npc_UMii_Hylia_BodyS_M_011", "Dm_Npc_UMii_Hylia_BodyS_M_012", "Dm_Npc_Handmaid_A", "Dm_Npc_Handmaid_B")
TALL_SLOTS = tuple(f"Dm663_Npc_Gerudo_S_{c}" for c in "ABCDEF")
FREE_SLOTS = DEMO_SLOTS + NPC_SLOTS + TALL_SLOTS
SLOTS = {"zelda": DEMO_SLOTS[0], "goron": DEMO_SLOTS[1], "bokoblin": DEMO_SLOTS[2],
         "hylian": NPC_SLOTS[0], "sheikah": NPC_SLOTS[1], "zora": NPC_SLOTS[2], "rito": NPC_SLOTS[3],
         "gerudo": TALL_SLOTS[0], "moblin": TALL_SLOTS[1],
         "linkle": NPC_SLOTS[4], "link": NPC_SLOTS[5]}     # (the last two: only with --model-copies)
SLOT_PROFILE = {**{s: "DemoNPC" for s in DEMO_SLOTS}, **{s: "NPC" for s in NPC_SLOTS + TALL_SLOTS}}


class CharselError(RuntimeError):
    pass


def order() -> list[str]:
    """Every id in wheel order."""
    return [i for _, ids in CATEGORIES for i in ids]


def find_id(text) -> str | None:
    """The character a player means: an id, a name or an alias (any case, spaces or _ for -). None if none."""
    if not isinstance(text, str):
        return None
    t = re.sub(r"[\s_]+", "-", text.strip().lower())
    if t in RECIPES:
        return t
    for r in RECIPES.values():
        if t in r.aliases or t == r.name.lower().replace(" ", "-") or t == r.name.split(" (")[0].lower():
            return r.id
    return None


def valid_id(text) -> str | None:
    """A look id as it may travel: lower-case letters, digits and -, 1-24, starting with a letter or digit."""
    return text if isinstance(text, str) and _ID_RE.fullmatch(text) else None


def look_message(cid: str) -> dict:
    """What goes in the hello's and the `look` message's "look": the id, the recipe version and the wheel name."""
    r = RECIPES.get(cid)
    return {"id": cid, "v": r.v if r else 1, "name": (r.name if r else cid)[:28]}


def display(look: dict | None) -> str:
    """A look's name for a person (its own name if it came with one, else the id)."""
    if not isinstance(look, dict):
        return "Link"
    r = RECIPES.get(look.get("id"))
    return r.name if r else str(look.get("name") or look.get("id") or "?")[:28]


# ------------------------------------------------------------------------------------------------ your choice

def home_dir(env=None) -> Path:
    """The launcher's folder (BREATHALYZER_HOME, else %LOCALAPPDATA%\\Breathalyzer), as tools/launcher.py has it."""
    env = os.environ if env is None else env
    if env.get(ENV_HOME):
        return Path(env[ENV_HOME]).resolve()
    base = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Breathalyzer"


def state_path(env=None) -> Path:
    return home_dir(env) / STATE


def load_choice(path: Path | None = None) -> dict:
    """{"look": id, "packs": folder or None}. A missing or broken file gives Link and no folder."""
    path = Path(path) if path else state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    look = data.get("look") if find_id(data.get("look")) == data.get("look") else None
    packs = data.get("packs") if isinstance(data.get("packs"), str) and data.get("packs") else None
    return {"look": look or "link", "packs": packs}


def save_choice(cid: str, path: Path | None = None, packs: str | None = None) -> Path:
    """Keep your choice (a temp file, then a rename). Other keys in the file are kept."""
    if find_id(cid) != cid:
        raise CharselError(f"no character {cid!r}")
    path = Path(path) if path else state_path()
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        old = old if isinstance(old, dict) else {}
    except (OSError, ValueError):
        old = {}
    old.update(format=1, look=cid)
    if packs:
        old["packs"] = str(packs)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(old, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


# ------------------------------------------------------------------------------------------------ seeing others

def load_catalog(folder) -> dict:
    """The built mod folder's charsel.json ({} when there is none or it can't be read)."""
    if not folder:
        return {}
    p = Path(folder)
    p = p if p.suffix == ".json" else p / CATALOG
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and isinstance(data.get("built"), dict) else {}


def overlay_dir(library=None) -> Path:
    """Where modman deploy (and Play) puts the RomFS files the game reads (library\\deploy\\overlay)."""
    return Path(library or DEFAULT_LIBRARY) / "deploy" / "overlay"


def deployed_catalog(library=None, overlay=None) -> dict:
    """The characters this PC's game really gets: the catalog of this mod's library copy while it is enabled (modman
    add out\\charsel-packs), keeping only the characters whose pack is in the deployed overlay
    (Actor\\Pack\\<free name>.sbactorpack). {} when there is none: every look is then the Link body. The catalog
    says where it came from ("where") and what it left out ("not_deployed")."""
    lib = Path(library or DEFAULT_LIBRARY)
    ov = Path(overlay) if overlay else overlay_dir(lib)
    for m in reversed(library_mods(lib)):            # (the last enabled copy wins, as in the deploy)
        cat = load_catalog(m["folder"])
        if cat.get("mod") != MOD_ID:
            continue
        built, gone = {}, []
        for cid, info in cat["built"].items():
            slot = info.get("slot") if isinstance(info, dict) else None
            pack = ov / "Actor" / "Pack" / f"{slot}.sbactorpack" if isinstance(slot, str) else None
            if slot in FREE_SLOTS and pack is not None and pack.is_file():
                built[cid] = info
            else:
                gone.append(cid)
        return dict(cat, built=built, not_deployed=gone, where=str(m["folder"]))
    return {}


def here_catalog(folder=None, library=None) -> tuple[dict, str]:
    """(the catalog to show looks with, where it is from). A folder you name (--ghost-looks DIR, --out) is taken as
    it is; otherwise only what this PC's game really gets (deployed_catalog)."""
    if folder:
        return load_catalog(folder), str(folder)
    cat = deployed_catalog(library)
    if not cat:
        return {}, "none in your game (build, add to your mods, then Play or deploy)"
    return cat, cat["where"]


class Looks:
    """How this PC shows other players' looks: an id from the network becomes an actor name only through this PC's
    own catalog (what it built, on which free name), never raw. Anything else is the Link body, with a reason."""

    def __init__(self, catalog: dict | None = None, fallback: str = LINK_ACTOR):
        catalog = catalog or {}
        self.fallback = fallback
        self.slots: dict[str, str] = {}
        for cid, info in (catalog.get("built") or {}).items():
            slot = info.get("slot") if isinstance(info, dict) else None
            if cid in RECIPES and isinstance(slot, str) and slot in FREE_SLOTS:
                self.slots[cid] = slot
        lk = catalog.get("linkle") if isinstance(catalog.get("linkle"), dict) else {}
        self.linkle_body = lk.get("how") == "link-body"   # Linkle mod here: the Link body *is* Linkle
        self.failed: set[str] = set()                      # slots that didn't load in this session

    def actor(self, look) -> tuple[str, str]:
        """(the actor name to spawn, "" or why it is the Link body instead)."""
        cid = look.get("id") if isinstance(look, dict) else None
        cid = cid.split(":", 1)[1] if isinstance(cid, str) and cid.startswith("built:") else cid
        if cid is None or cid == "link":
            slot = self.slots.get("link")
            return (slot, "") if slot and slot not in self.failed else (self.fallback, "")
        r = RECIPES.get(cid)
        if r is None:
            return self.fallback, "unknown here (a newer Breathalyzer?)"
        slot = self.slots.get(cid)
        if slot and slot in self.failed:
            return self.fallback, "didn't load in this game"
        if slot:
            return slot, ""
        if r.kind == "linkle":
            return (self.fallback, "") if self.linkle_body else (self.fallback, "the Linkle mod isn't installed here")
        return self.fallback, "not built here"

    def mark_failed(self, slot: str) -> bool:
        """The mod gave up spawning this slot: show the Link body instead for the rest of the session."""
        if slot == self.fallback or slot in self.failed:
            return False
        self.failed.add(slot)
        return True

    def ids(self) -> list[str]:
        """What this PC can pick from (the wheel's order): everything; a friend who lacks one sees the Link body."""
        return order()


# ------------------------------------------------------------------------------------------------ the wheel

class Wheel:
    """Who am I, as a state machine (no window): the launcher's window, `charsel wheel` and the in-game screen draw its
    lines() or slices() and send it keys. Left/Right: the next character around the wheel; Up/Down: the next group;
    Enter: pick (returns the id); Esc: back (returns ""). A controller's d-pad and A/B map to the same."""

    def __init__(self, current: str = "link", built: dict | None = None):
        self.ids = order()
        self.built = built if built is not None else None   # catalog "built" (None: don't say what is built)
        self.linkle = None
        self.pos = self.ids.index(current) if current in self.ids else 0
        self.start = self.ids[self.pos]
        self.done: str | None = None

    @property
    def cid(self) -> str:
        return self.ids[self.pos]

    def cat(self) -> str:
        return RECIPES[self.cid].cat

    def key(self, k: str) -> str | None:
        """One key: 'left' 'right' 'up' 'down' 'enter' 'esc'. Returns the id on Enter, "" on Esc, else None."""
        n = len(self.ids)
        if k == "right":
            self.pos = (self.pos + 1) % n
        elif k == "left":
            self.pos = (self.pos - 1) % n
        elif k in ("down", "up"):
            names = [c for c, _ in CATEGORIES]
            i = (names.index(self.cat()) + (1 if k == "down" else -1)) % len(names)
            self.pos = self.ids.index(CATEGORIES[i][1][0])
        elif k == "enter":
            self.done = self.cid
            return self.cid
        elif k == "esc":
            self.done = ""
            return ""
        return None

    def status(self, cid: str | None = None) -> str:
        """Ready / Build first / Needs a mod (the plan's three words), for this PC."""
        r = RECIPES[cid or self.cid]
        if r.kind == "link":
            return "Ready"
        if r.kind == "linkle":
            return "Ready" if self.linkle else "Needs a mod"
        if self.built is None:
            return ""
        return "Ready" if r.id in self.built else "Build first"

    def lines(self) -> list[str]:
        r = RECIPES[self.cid]
        groups = "   ".join(f"[{c.upper()}]" if c == r.cat else c for c, _ in CATEGORIES)
        st = self.status()
        out = ["Who am I?", f"  {groups}", f"  <  {r.name}  >" + (f"   {st}" if st else ""),
               f"  Others see you as: {r.name.split(' (')[0]}. You play as: Link."]
        if r.looks:
            out.append(f"  {r.looks}")
        out.append("  Left/Right: character   Up/Down: group   Enter: OK   Esc: back")
        return out

    def slices(self) -> list[dict]:
        """The wheel for a drawing: one slice per character, in order, with its angle span (degrees, 0 = up,
        clockwise), group and whether it is the chosen one."""
        n = len(self.ids)
        step = 360.0 / n
        return [{"id": cid, "name": RECIPES[cid].name.split(" (")[0], "cat": RECIPES[cid].cat,
                 "start": i * step - step / 2, "extent": step, "on": i == self.pos} for i, cid in enumerate(self.ids)]


# XInput (a controller): the d-pad or the left stick move, A picks, B goes back. Pure mapping here; the window polls.
XI_UP, XI_DOWN, XI_LEFT, XI_RIGHT, XI_A, XI_B = 0x0001, 0x0002, 0x0004, 0x0008, 0x1000, 0x2000
STICK_DEAD = 16000
PAD_KEYS = ((XI_UP, "up"), (XI_DOWN, "down"), (XI_LEFT, "left"), (XI_RIGHT, "right"), (XI_A, "enter"), (XI_B, "esc"))


def pad_buttons(buttons: int, lx: int = 0, ly: int = 0) -> int:
    """The pad's buttons with the left stick folded into the d-pad bits (past STICK_DEAD)."""
    b = buttons & 0xFFFF
    if lx <= -STICK_DEAD:
        b |= XI_LEFT
    elif lx >= STICK_DEAD:
        b |= XI_RIGHT
    if ly >= STICK_DEAD:
        b |= XI_UP
    elif ly <= -STICK_DEAD:
        b |= XI_DOWN
    return b


def pad_keys(prev: int, now: int) -> list[str]:
    """The keys for buttons that went down since the last poll."""
    pressed = now & ~prev
    return [k for bit, k in PAD_KEYS if pressed & bit]


class Pad:
    """The first XInput controller, polled (Windows; does nothing elsewhere or without a controller)."""

    def __init__(self):
        self.dll = None
        self.prev = 0
        if sys.platform != "win32":
            return
        import ctypes
        for name in ("xinput1_4", "xinput9_1_0", "xinput1_3"):
            try:
                self.dll = getattr(ctypes.windll, name)
                break
            except OSError:
                continue
        self._buf = ctypes.create_string_buffer(16) if self.dll else None

    def keys(self) -> list[str]:
        if self.dll is None:
            return []
        import struct
        try:
            if self.dll.XInputGetState(0, self._buf) != 0:
                self.prev = 0
                return []
        except OSError:
            self.dll = None
            return []
        _pkt, buttons, _lt, _rt, lx, ly = struct.unpack_from("<IHBBhh", self._buf.raw)
        now = pad_buttons(buttons, lx, ly)
        out, self.prev = pad_keys(self.prev, now), now
        return out


TK_KEYS = {"Left": "left", "Right": "right", "Up": "up", "Down": "down", "Return": "enter", "KP_Enter": "enter",
           "Escape": "esc", "BackSpace": "esc", "space": "enter"}
CAT_COLORS = {"Hylians": "#237032", "Races": "#1971c2", "Monsters": "#c92a2a"}   # (white text: 5:1 or more)
MIN_PT = 18                       # the smallest text on the wheel (points)


def screen_scale(root) -> float:
    """How much bigger than 96 dpi this screen draws (1.0-3.0): the canvas is in pixels, fonts are in points, so a
    window made DPI aware (modman_gui.dpi_aware) scales the canvas by this to keep the labels inside their slices."""
    try:
        return max(1.0, min(3.0, float(root.winfo_fpixels("1i")) / 96.0))
    except Exception:   # (no display)
        return 1.0


class WheelWindow:
    """The wheel drawn on a Tk canvas: one slice per character around a ring (its group's colour, the chosen one
    lit), the chosen name big in the middle ("Daruk (Goron)"), its group and status, and under the wheel who others
    see, the honest looks line and the hints. Big text (at least MIN_PT points; the player's eyes come first). scale:
    screen_scale(root) when the window is DPI aware; the wheel never grows taller than the screen."""

    def __init__(self, root, wheel: Wheel, size: int = 640, font: int = 26, on_done=None, pad: Pad | None = None,
                 scale: float = 1.0):
        import tkinter as tk
        self.root, self.wheel, self.font, self.on_done = root, wheel, font, on_done
        self.pad = pad
        self.px = scale * 96 / 72                    # pixels per point
        self.small = max(MIN_PT, font * 2 // 3)
        self.text_h = int(self.small * self.px * 8.5)   # who + looks (up to 3 lines) + hints, with gaps
        try:
            room = int(root.winfo_screenheight()) - self.text_h - 120
        except Exception:   # (no display)
            room = 10_000
        self.size = max(360, min(int(size * scale), room))
        root.configure(bg="#101418")
        self.canvas = tk.Canvas(root, width=self.size, height=self.size + self.text_h, bg="#101418",
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        root.bind("<Key>", self._on_key)
        self.draw()

    def _on_key(self, ev) -> None:
        k = TK_KEYS.get(ev.keysym)
        if k:
            self.press(k)

    def press(self, k: str) -> None:
        got = self.wheel.key(k)
        self.draw()
        if got is not None and self.on_done is not None:
            self.on_done(got)

    def poll_pad(self) -> None:
        if self.pad is not None:
            for k in self.pad.keys():
                self.press(k)
            if self.wheel.done is None:
                self.root.after(50, self.poll_pad)

    def _fit(self, text: str, pt: int, width: float, bold: bool = True) -> tuple:
        """The biggest font from pt down to MIN_PT that fits text in width pixels."""
        try:
            import tkinter.font as tkfont
            while pt > MIN_PT and tkfont.Font(root=self.root, family="Segoe UI", size=pt,
                                              weight="bold" if bold else "normal").measure(text) > width:
                pt -= 1
        except Exception:   # (no display: the asked size)
            pass
        return ("Segoe UI", pt, "bold") if bold else ("Segoe UI", pt)

    def draw(self) -> None:
        import math
        c, s, f, sm, px = self.canvas, self.size, self.font, self.small, self.px
        c.delete("all")
        mid, r_out, r_in = s / 2, s / 2 - 10, s / 4
        rr = (r_out + r_in) / 2
        slices = self.wheel.slices()
        chord = 2 * rr * math.sin(math.radians(slices[0]["extent"] / 2)) - 8 if slices else s
        for sl in slices:
            color = CAT_COLORS.get(sl["cat"], "#495057")
            # (Tk arcs: 0 degrees is 3 o'clock, counter-clockwise; ours: 0 is 12 o'clock, clockwise)
            start = 90 - sl["start"] - sl["extent"]
            c.create_arc(mid - r_out, mid - r_out, mid + r_out, mid + r_out, start=start, extent=sl["extent"],
                         fill="#ffd43b" if sl["on"] else color, outline="#101418", width=3, tags=("slice", sl["id"]))
            a = math.radians(sl["start"] + sl["extent"] / 2)
            c.create_text(mid + rr * math.sin(a), mid - rr * math.cos(a), text=sl["name"],
                          fill="#000000" if sl["on"] else "#ffffff", font=self._fit(sl["name"], sm, chord),
                          tags=("label", sl["id"]))
        c.create_oval(mid - r_in, mid - r_in, mid + r_in, mid + r_in, fill="#101418", outline="#101418")
        r = RECIPES[self.wheel.cid]
        st = self.wheel.status()
        c.create_text(mid, mid - f * px * 0.55, text=r.name, fill="#ffffff", font=self._fit(r.name, f, 2 * r_in - 16),
                      tags=("name",))
        c.create_text(mid, mid + sm * px * 0.9, text=r.cat + (f"  -  {st}" if st else ""), fill="#ffffff",
                      font=self._fit(r.cat + (f"  -  {st}" if st else ""), sm, 2 * r_in - 24, bold=False),
                      tags=("cat",))
        y = s + sm * px * 0.3
        c.create_text(mid, y, text=f"Others see you as: {r.name}", fill="#ffffff", anchor="n",
                      font=("Segoe UI", sm, "bold"), width=s - 16, tags=("who",))
        y += sm * px * 1.6
        if r.looks:
            c.create_text(mid, y, text=r.looks, fill="#f1f3f5", anchor="n", font=("Segoe UI", sm), width=s - 16,
                          justify="center", tags=("looks",))
        c.create_text(mid, s + self.text_h - sm * px * 0.3, text="Left/Right: character   Up/Down: group   "
                      "Enter: OK   Esc: back", fill="#dee2e6", anchor="s", font=("Segoe UI", sm), width=s - 16,
                      justify="center", tags=("hint",))


def choose_window(current: str = "link", built: dict | None = None, linkle: bool | None = None,
                  timeout_s: float | None = None) -> str | None:
    """Show the wheel in a window and return the picked id (None: closed or Esc). Opens a window: never in tests."""
    import tkinter as tk
    root = tk.Tk()
    root.title("Who am I")
    wheel = Wheel(current, built)
    wheel.linkle = linkle
    picked: list[str | None] = [None]

    def done(cid: str) -> None:
        picked[0] = cid or None
        root.quit()

    win = WheelWindow(root, wheel, on_done=done, pad=Pad(), scale=screen_scale(root))
    win.poll_pad()
    if timeout_s:
        root.after(int(timeout_s * 1000), root.quit)
    root.protocol("WM_DELETE_WINDOW", root.quit)
    try:
        root.mainloop()
    finally:
        root.destroy()
    return picked[0]


# ------------------------------------------------------------------------------------------------ the library

def library_mods(library) -> list[dict]:
    """The enabled mods of a modman library, in order (later wins): {"id", "name", "version", "folder"}."""
    try:
        cfg = json.loads((Path(library) / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    out = []
    for m in cfg.get("mods") or [] if isinstance(cfg, dict) else []:
        if isinstance(m, dict) and m.get("enabled") and isinstance(m.get("id"), str):
            out.append({"id": m["id"], "name": str(m.get("name") or m["id"]), "version": str(m.get("version") or ""),
                        "folder": Path(library) / "mods" / m["id"]})
    return out


ROMFS_TOPS = ("romfs", "01007EF00011E000/romfs", "atmosphere/contents/01007EF00011E000/romfs", "")


def _link_model_in(folder: Path) -> tuple[Path, str] | None:
    """Where a mod folder replaces Link's model: (Pack/TitleBG.pack, member) or (Model/Link.sbfres, "")."""
    from mp import ghost_link_pack as glp
    for top in ROMFS_TOPS:
        base = folder / top if top else folder
        loose = base / "Model" / "Link.sbfres"
        if loose.is_file():
            return loose, ""
        title = base / "Pack" / "TitleBG.pack"
        if title.is_file():
            try:
                if glp._sarc_member(title, "Model/Link.sbfres") is not None:
                    return title, "Model/Link.sbfres"
            except OSError:
                pass
    return None


def player_model_mod(library) -> dict | None:
    """The enabled mod that replaces Link's model (the last one wins), or None: {"id", "name", "version", "linkle",
    "where", "member", "fp"} (fp: the first 16 hex of the model file's sha256). Reads only."""
    found = None
    for m in library_mods(library):
        hit = _link_model_in(m["folder"])
        if hit:
            found = dict(m, where=str(hit[0]), member=hit[1])
    if found is None:
        return None
    data = _read_link_model(Path(found["where"]), found["member"])
    found["fp"] = hashlib.sha256(data).hexdigest()[:16] if data else ""
    found["linkle"] = "linkle" in (found["id"] + " " + found["name"]).lower()
    found["folder"] = str(found["folder"])
    return found


def _read_link_model(where: Path, member: str, tex: bool = False) -> bytes | None:
    from mp import ghost_link_pack as glp
    name = "Model/Link.Tex.sbfres" if tex else "Model/Link.sbfres"
    if not member:
        p = where.with_name("Link.Tex.sbfres") if tex else where
        return p.read_bytes() if p.is_file() else None
    return glp._sarc_member(where, name)


# ------------------------------------------------------------------------------------------------ the game's files

def _unyaz(d: bytes) -> bytes:
    from wiiu_convert import yaz0_decompress
    return yaz0_decompress(d)[0] if d[:4] == b"Yaz0" else d


def bfres_clips(data: bytes) -> list[str]:
    """The skeletal clip names of a Switch .bfres (its dictionary order)."""
    import struct
    from wiiu_model import dicts, strpool
    data = _unyaz(data)
    if data[:4] != b"FRES":
        raise CharselError("not a Switch .bfres")
    counts = struct.unpack_from("<7H", data, 0xBC)
    if not counts[1]:
        return []
    _, keys = dicts.read_nx_dict(data, struct.unpack_from("<Q", data, 0x40)[0])
    return [strpool.nx_string(data, keys[i]) for i in range(counts[1])]


def model_file(romfs: Path, name: str) -> bytes | None:
    """Model/<name>.sbfres from the game's files (loose, or inside Pack/TitleBG.pack)."""
    from mp import ghost_link_pack as glp
    p = Path(romfs) / "Model" / f"{name}.sbfres"
    if p.is_file():
        return p.read_bytes()
    t = Path(romfs) / "Pack" / "TitleBG.pack"
    return glp._sarc_member(t, f"Model/{name}.sbfres") if t.is_file() else None


class ClipCache:
    """Clip names per animation file, kept in a small JSON file (names only) keyed by the file's size and time, so a
    second build doesn't unpack the files again."""

    def __init__(self, romfs: Path, path: Path | None = None):
        self.romfs, self.path = Path(romfs), Path(path) if path else None
        self.data: dict = {}
        if self.path and self.path.is_file():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.data = {}

    def clips(self, name: str) -> set[str] | None:
        p = self.romfs / "Model" / f"{name}.sbfres"
        key = f"{name}|{p.stat().st_size}|{int(p.stat().st_mtime)}" if p.is_file() else f"{name}|titlebg"
        if key not in self.data:
            d = model_file(self.romfs, name)
            if d is None:
                return None
            self.data[key] = bfres_clips(d)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.data), encoding="utf-8")
        return set(self.data[key])


def read_actorinfo(romfs: Path) -> dict[str, dict]:
    """{actor name: {"profile", "instSize"}} for the free names only, from the game's own ActorInfo."""
    import bcml_data
    doc = bcml_data.byml_load((Path(romfs) / "Actor" / "ActorInfo.product.sbyml").read_bytes())
    out = {}
    for a in doc.get("Actors", []):
        name = str(a.get("name", ""))
        if name in FREE_SLOTS:
            out[name] = {"profile": str(a.get("profile", "")), "instSize": int(a.get("instSize", 0) or 0)}
    return out


def check_slot(romfs: Path, slot: str, info: dict | None) -> tuple[str, list[str]]:
    """(the slot's profile, problems): it must be a free name of the game's: in ActorInfo with a profile we use, a
    heap of at least 500 KB, and no pack in the game's files."""
    from mp import ghost_link_pack as glp
    bad = []
    profile = SLOT_PROFILE.get(slot, "DemoNPC")
    if info is not None:
        a = info.get(slot)
        if a is None:
            bad.append(f"{slot} isn't in the game's ActorInfo")
        else:
            profile = a["profile"] or profile
            if profile not in ("DemoNPC", "NPC"):
                bad.append(f"{slot} is a {profile}, not a person")
            if a["instSize"] < 500_000:
                bad.append(f"{slot} has a heap of {a['instSize']:,} bytes (under 500 KB)")
    try:
        glp.find_pack(Path(romfs), slot)
        bad.append(f"{slot} has a pack in the game: it isn't free")
    except glp.PackError:
        pass
    return profile, bad


# ------------------------------------------------------------------------------------------------ making a pack

def ours(cid: str) -> str:
    """Our file names inside a pack (model list, animation list, animation sets): SwChar_<id>."""
    return "SwChar_" + cid.replace("-", "_")


def clip_map(r: Recipe, rest: str | None = None) -> dict[str, str]:
    """{ghost name: clip}: the character's own, then (rest "link") Link's clip for every other ghost name."""
    from mp import ghost_link_pack as glp
    link = {n: c for n, c, _f in glp.ANIMS}
    unknown = [n for n, _ in r.own if n not in link]
    if unknown:
        raise CharselError(f"{r.id}: {', '.join(unknown)} isn't a ghost name")
    m = dict(link) if (rest or r.rest) == "link" or r.kind != "pack" else {}
    m.update(r.own)
    return m


def anim_files(r: Recipe, rest: str | None = None) -> list[tuple[str, bool]]:
    files = list(r.anims)
    if (rest or r.rest) == "link" and all(f != PLAYER_ANIM for f, _ in files):
        files.append((PLAYER_ANIM, True))
    if (rest or r.rest) != "link":
        files = [(f, rt) for f, rt in files if f != PLAYER_ANIM]
    return files


def resolve(r: Recipe, clips_of, rest: str | None = None) -> dict:
    """Where each clip comes from: {"own", "link", "missing", "clash"}. clips_of(file) gives a file's clip names, or
    None to trust it (Link's file: its 33 clips play on the Link body in the game [V]; trusted for those only)."""
    from mp import ghost_link_pack as glp
    link_clips = {c for _n, c, _f in glp.ANIMS}
    m = clip_map(r, rest)
    files = anim_files(r, rest)
    own = dict(r.own)
    sets = {f: clips_of(f) for f, _ in files}
    res = {"own": {}, "link": {}, "missing": [], "clash": []}
    for name, clip in m.items():
        hit = None
        for f, _ in files:
            s = sets[f]
            if (s is None and name not in own and clip in link_clips) or (s is not None and clip in s):
                hit = f
                break
        if hit is None:
            res["missing"].append(f"{name}->{clip}")
            continue
        if name not in own and hit != PLAYER_ANIM:
            res["clash"].append(f"{clip} is found first in {hit}")
        (res["link"] if hit == PLAYER_ANIM else res["own"])[name] = clip
    return res


def make_pack(r: Recipe, slot: str, profile: str, template: dict[str, bytes], donor: dict[str, bytes],
              rest: str | None = None) -> dict[str, bytes]:
    """The files of a character's pack, from the template's pack (the game's cutscene Sheikah man: actor link, AI
    program) and the donor's (its model list, its animation list's flags, its look file)."""
    import aamp
    from mp import ghost_link_pack as glp
    if r.kind != "pack":
        raise CharselError(f"{r.id} has no pack of its own")
    _, dlt = glp.link_target(donor, r.donor)
    d_model = glp.user(dlt, "ModelUser")
    if d_model.startswith("SwChar_") or d_model == glp.OURS:
        raise CharselError(f"{r.donor}'s pack is already ours: point --romfs at the game's own files")
    ml_name = f"Actor/ModelList/{d_model}.bmodellist"
    if ml_name not in donor:
        raise CharselError(f"{r.donor}'s pack has no {ml_name}")
    ml = aamp.load(donor[ml_name])
    md = ml.list("ModelData")
    umii = not (md and md.lists)                   # no ModelData: a body put together by the Mii-style look file
    d_umii = dlt.get_value("UMiiUser") or "Dummy"
    if umii and (d_umii == "Dummy" or f"Actor/UMii/{d_umii}.bumii" not in donor):
        raise CharselError(f"{r.donor} has no model data and no look file")
    # the donor's own animation list says which of its files it retargets: we do the same for those files
    flags: dict[str, bool] = {}
    d_as = dlt.get_value("ASUser")
    if isinstance(d_as, str) and f"Actor/ASList/{d_as}.baslist" in donor:
        add = aamp.load(donor[f"Actor/ASList/{d_as}.baslist"]).list("AddReses")
        for o in (add.objects.values() if add else ()):
            a = o.get_value("Anim")
            if isinstance(a, str):
                flags[a] = o.get_value("RetargetModel") not in (None, "")
    files = [(anim, flags.get(anim, rt)) for anim, rt in anim_files(r, rest)]
    look = (d_umii, donor[f"Actor/UMii/{d_umii}.bumii"]) if umii else None
    return _pack_files(r.id, slot, profile, template, donor[ml_name], look, files, clip_map(r, rest))


def _pack_files(cid: str, slot: str, profile: str, template: dict[str, bytes], model_list: bytes,
                look: tuple[str, bytes] | None, files: list[tuple[str, bool]], mapping: dict[str, str]
                ) -> dict[str, bytes]:
    """The pack: the template's actor link with our users (no AI but the cutscene dummy's, no physics), the files the
    kept users need, our model list, the look file (bodies the game puts together from one), our animation list
    (files, sorted names, blend frames) and one animation set per name. Every name of ours is SwChar_<id>, so two
    packs never share a path with different contents."""
    import aamp
    from aamp import F32, STRING32, STRING64, PList, PObject, Param, name_hash
    from mp import ghost_link_pack as glp
    if slot == LINK_ACTOR or slot not in FREE_SLOTS:
        raise CharselError(f"{slot} isn't one of the free names")
    al_root, lt = glp.link_target(template, TEMPLATE)
    if glp.user(lt, "ModelUser") == glp.OURS or glp.user(lt, "ModelUser").startswith("SwChar_"):
        raise CharselError(f"{TEMPLATE}'s pack is already ours: point --romfs at the game's own files")
    tpl_as = glp.user(lt, "ASUser")
    if f"Actor/ASList/{tpl_as}.baslist" not in template:
        raise CharselError(f"{TEMPLATE}'s pack has no animation list")
    tpl_list = aamp.load(template[f"Actor/ASList/{tpl_as}.baslist"])
    tmpl = next((o for o in tpl_list.list("AddReses").objects.values()
                 if o.get(name_hash("RetargetModel")) is not None), None)
    if tmpl is None:
        raise CharselError(f"{TEMPLATE}'s animation list has no retarget entry to copy")
    name = ours(cid)
    users = {"ModelUser": name, "ASUser": name, "PhysicsUser": "Dummy", "AnimationInfo": "Dummy",
             "UMiiUser": look[0] if look else "Dummy", "AIProgramUser": "DemoDummy", "ProfileUser": profile}
    for k, v in users.items():
        p = lt.get(name_hash(k))
        if p is None:
            raise CharselError(f"the actor link has no {k}")
        lt[name_hash(k)] = Param(p.type, v)
    out: dict[str, bytes] = {f"Actor/ActorLink/{slot}.bxml": aamp.dump(al_root)}
    for n, b in template.items():   # (what the kept users need: the AI program, as the Link body does)
        if not n.startswith(glp.DROPPED_FOLDERS) and not n.startswith("Actor/ActorLink/"):
            out[n] = b
    if "Actor/AIProgram/DemoDummy.baiprog" not in out:
        raise CharselError(f"{TEMPLATE}'s pack has no DemoDummy AI program")
    out[f"Actor/ModelList/{name}.bmodellist"] = model_list
    if look:
        out[f"Actor/UMii/{look[0]}.bumii"] = look[1]
    add = PList()
    for i, (anim, rt) in enumerate(files):
        o = PObject()
        o[name_hash("Anim")] = Param(tmpl[name_hash("Anim")].type, anim)
        if rt:
            o[name_hash("RetargetModel")] = Param(tmpl[name_hash("RetargetModel")].type, "Skeleton")
        add.objects[name_hash(f"AddRes_{i}")] = o
    names = sorted(mapping, key=str.encode)        # the game binary-searches this list: keep it sorted
    defines = PList()
    for i, n in enumerate(names):
        o = PObject()
        o.set("Name", STRING64, n)
        o.set("Filename", STRING64, f"{name}_{n}")
        defines.objects[name_hash(f"ASDefine_{i}")] = o
    frames = {n: f for n, _c, f in glp.ANIMS}
    cf = PList()
    for i, pre in enumerate(names):   # blend frames between sets, as the Link body's list (ghost_link_pack.as_list)
        posts = PList()
        targets = [""] + sorted((p for p in glp.LOCOMOTION if p != pre and p in mapping), key=str.encode)
        for k, post in enumerate(targets):
            o = PObject()
            o.set("Name", STRING32, post)
            o.set("Frame", F32, 6.0 if post == "" else glp.blend_frames(pre, post, frames))
            o[0xCF996FB7] = Param(F32, -1.0)
            posts.objects[name_hash(f"CFPost_{k}")] = o
        pre_o = PObject()
        pre_o.set("Name", STRING32, pre)
        cf.lists[name_hash(f"CFDefine_{i}")] = PList(lists={name_hash("CFPosts"): posts},
                                                    objects={name_hash("CFPre"): pre_o})
    out[f"Actor/ASList/{name}.baslist"] = aamp.dump(PList(lists={name_hash("AddReses"): add,
                                                                 name_hash("ASDefines"): defines,
                                                                 name_hash("CFDefines"): cf}))
    for n, clip in mapping.items():
        out[f"Actor/AS/{name}_{n}.bas"] = glp.as_file(clip)
    return out


def make_copy_pack(cid: str, slot: str, profile: str, template: dict[str, bytes], pause: dict[str, bytes]
                   ) -> dict[str, bytes]:
    """--model-copies: a Link-skeleton body under its own model name (Model/SwChar_<id>.sbfres, a renamed copy of
    the model file): the Link body's model list (the pause-menu Link's, with the Sheikah man's animation slots) with
    that folder, Link's own clips (no retarget: the same skeleton), on a free name."""
    import aamp
    from aamp import Param, name_hash
    from mp import ghost_link_pack as glp
    if cid not in ("link", "linkle") or slot not in FREE_SLOTS:
        raise CharselError(f"no model copy for {cid} on {slot}")
    _, tlt = glp.link_target(template, TEMPLATE)
    _, plt = glp.link_target(pause, glp.DONOR_LINK)
    tml = f"Actor/ModelList/{glp.user(tlt, 'ModelUser')}.bmodellist"
    pml = f"Actor/ModelList/{glp.user(plt, 'ModelUser')}.bmodellist"
    if tml not in template or pml not in pause:
        raise CharselError("a model list is missing")
    ml = aamp.load(glp.model_list(aamp.load(pause[pml]), aamp.load(template[tml]), "sheikah"))
    base = ml.list("ModelData").list("ModelData_0").object("Base")
    f = base.get(name_hash("Folder"))
    base[name_hash("Folder")] = Param(f.type, ours(cid))
    mapping = {n: c for n, c, _f in glp.ANIMS}
    return _pack_files(cid, slot, profile, template, aamp.dump(ml), None, [(PLAYER_ANIM, False)], mapping)


def read_back(data: bytes) -> dict:
    """A built pack read back: its actor link's users, the animation names (in file order) and each one's clip, the
    animation files and their retarget, its sizes."""
    import aamp
    from mp import ghost_link_pack as glp
    from wiiu_convert import yaz0_decompress
    raw = yaz0_decompress(data)[0] if data[:4] == b"Yaz0" else data
    files = glp.read_pack(data)
    links = [n for n in files if n.startswith("Actor/ActorLink/")]
    if len(links) != 1:
        raise CharselError(f"{len(links)} actor links in the pack")
    slot = links[0].rsplit("/", 1)[1][:-5]
    _, lt = glp.link_target(files, slot)
    users = {k: lt.get_value(k) for k in ("ProfileUser", "ModelUser", "ASUser", "PhysicsUser", "AIProgramUser",
                                          "UMiiUser", "AnimationInfo")}
    asl = aamp.load(files[f"Actor/ASList/{users['ASUser']}.baslist"])
    names, clips, missing = [], {}, []
    for o in asl.list("ASDefines").objects.values():
        n, fn = o.get_value("Name"), o.get_value("Filename")
        names.append(n)
        bas = files.get(f"Actor/AS/{fn}.bas")
        if bas is None:
            missing.append(n)
            continue
        clips[n] = aamp.load(bas).list("Elements").list("Element0").object("Parameters").get_value("FileName")
    adds = [(o.get_value("Anim"), o.get_value("RetargetModel")) for o in asl.list("AddReses").objects.values()]
    ml = files.get(f"Actor/ModelList/{users['ModelUser']}.bmodellist")
    folder = None
    if ml:
        md = aamp.load(ml).list("ModelData")
        if md and md.lists:
            folder = next(iter(md.lists.values())).object("Base").get_value("Folder")
    return {"slot": slot, "users": users, "names": names, "clips": clips, "missing_sets": missing, "adds": adds,
            "folder": folder, "files": len(files), "unpacked": len(raw), "packed": len(data)}


def check_pack(data: bytes, expect_slot: str | None = None) -> list[str]:
    """What is wrong with a built pack ([] = nothing)."""
    try:
        r = read_back(data)
    except Exception as e:   # (any read failure is the answer)
        return [f"doesn't read back: {type(e).__name__}: {e}"]
    bad = []
    if expect_slot and r["slot"] != expect_slot:
        bad.append(f"actor link for {r['slot']}, expected {expect_slot}")
    if r["names"] != sorted(r["names"], key=str.encode):
        bad.append("animation names not sorted (the game binary-searches them)")
    if len(set(r["names"])) != len(r["names"]):
        bad.append("an animation name twice")
    if r["missing_sets"]:
        bad.append(f"no animation set for {', '.join(r['missing_sets'])}")
    if any(not c for c in r["clips"].values()):
        bad.append("an animation set without a clip")
    u = r["users"]
    if u["AIProgramUser"] != "DemoDummy":
        bad.append(f"AI program {u['AIProgramUser']}, not the cutscene dummy")
    if u["PhysicsUser"] != "Dummy":
        bad.append(f"physics {u['PhysicsUser']}")
    if u["ProfileUser"] not in ("DemoNPC", "NPC"):
        bad.append(f"profile {u['ProfileUser']}")
    if u["ProfileUser"] != SLOT_PROFILE.get(r["slot"], u["ProfileUser"]):
        bad.append(f"profile {u['ProfileUser']} on a {SLOT_PROFILE.get(r['slot'])} name")
    if r["unpacked"] >= PACK_LIMIT:
        bad.append(f"{r['unpacked']:,} bytes unpacked (limit {PACK_LIMIT:,})")
    if not r["adds"]:
        bad.append("no animation files")
    return bad


# ------------------------------------------------------------------------------------------------ building

MOD_TOML = f'''# Characters for multiplayer ("Who am I", docs/charsel.md). Built on your PC from your own game files by
# tools/charsel.py: never share or commit this folder. Your friend builds their own.
[mod]
id = "{MOD_ID}"
name = "Characters for multiplayer (Who am I)"
version = "{VERSION}"
authors = ["giruka"]
description = "Small actor packs that let the ghost mod show other players as Zelda, a Goron, a Bokoblin and more, on cutscene names the game never uses. Built from your own game files. Not yet tried in the game."
game_versions = ["1.9.0"]

[data]
romfs = "romfs"
'''


def parse_slots(items) -> dict[str, str]:
    """--slot ID=ACTOR, checked: a known id and one of the free names."""
    out = {}
    for it in items or []:
        cid, _, slot = str(it).partition("=")
        cid = find_id(cid)
        if cid is None or slot not in FREE_SLOTS:
            raise CharselError(f"bad --slot {it!r}: ID=ACTOR with ACTOR one of {', '.join(FREE_SLOTS)}")
        out[cid] = slot
    return out


def build(romfs=DEFAULT_ROMFS, out=DEFAULT_OUT, ids=None, *, library=None, slots=None, model_copies=False,
          monster_link=False, cache=DEFAULT_CACHE, say=print) -> dict:
    """Build the mod folder (mod.toml, romfs\\Actor\\Pack\\<free name>.sbactorpack, charsel.json). Returns the catalog."""
    from mp import ghost_link_pack as glp
    from wiiu_convert import FormatError
    import aamp
    romfs, out = Path(romfs).resolve(), Path(out).resolve()
    if out == romfs or romfs in out.parents or out in romfs.parents:
        raise CharselError("--out must be a folder of your own, not inside the game's files")
    if not (romfs / "Actor" / "Pack").is_dir():
        raise CharselError(f"{romfs} isn't the game's extracted romfs (no Actor\\Pack)")
    all_ids = not ids
    ids = list(dict.fromkeys(find_id(i) or i for i in (ids or order())))
    bad = [i for i in ids if i not in RECIPES]
    if bad:
        raise CharselError(f"no character {', '.join(bad)} (charsel list)")
    # a build of some characters keeps the others from the last build (same recipe version, pack still there)
    old = load_catalog(out)
    keep: dict[str, dict] = {}
    for cid, info in ((old.get("built") or {}).items() if old.get("mod") == MOD_ID and not all_ids else ()):
        r = RECIPES.get(cid)
        slot = info.get("slot") if isinstance(info, dict) else None
        if (r is not None and cid not in ids and slot in FREE_SLOTS and info.get("v") == r.v
                and (out / "romfs" / "Actor" / "Pack" / f"{slot}.sbactorpack").is_file()):
            keep[cid] = info
    table = dict(SLOTS)
    table.update(slots or {})
    on: dict[str, list[str]] = {}
    for cid in ids:   # (Link and Linkle get a name of their own only with --model-copies)
        if cid in table and (RECIPES[cid].kind == "pack" or model_copies):
            on.setdefault(table[cid], []).append(cid)
    for cid, info in keep.items():
        on.setdefault(info["slot"], []).append(cid)
    clash = {s: c for s, c in on.items() if len(c) > 1}
    if clash:
        raise CharselError("two characters on the same actor name: " + "; ".join(
            f"{' and '.join(c)} on {s}" for s, c in clash.items()) + " (move one with --slot, or build both)")
    notes: list[str] = []
    if keep:
        notes.append(f"Kept from the last build: {', '.join(keep)} (charsel build with no names rebuilds all).")
    try:
        ainfo = read_actorinfo(romfs)
    except Exception as e:   # (bcml_data is another job's; without it the free names aren't checked again)
        ainfo = None
        notes.append(f"The game's ActorInfo wasn't read ({type(e).__name__}): free names not checked again.")
    try:
        template = glp.read_pack(glp.find_pack(romfs, TEMPLATE))
        pause = glp.read_pack(glp.find_pack(romfs, glp.DONOR_LINK)) if model_copies else None
    except (glp.PackError, FormatError, aamp.AampError, OSError) as e:
        raise CharselError(f"a game file didn't read as expected: {e}") from None
    clips = ClipCache(romfs, cache)
    pm = player_model_mod(library) if library else None
    catalog = {"format": 1, "builder": VERSION, "mod": MOD_ID, "built": dict(keep), "kept": sorted(keep),
               "skipped": {k: v for k, v in (old.get("skipped") or {}).items()
                           if not all_ids and k in RECIPES and k not in ids and k not in keep and isinstance(v, str)},
               "notes": notes, "linkle": {"how": "missing"}, "link_modded": bool(pm),
               "model_copies": bool(model_copies) or any(RECIPES[k].kind != "pack" for k in keep),
               "monster_link_clips": bool(monster_link)}
    if pm:
        catalog["player_model"] = {k: pm[k] for k in ("id", "name", "version", "fp", "linkle")}
        if pm["linkle"]:
            catalog["linkle"] = {"how": "link-body", "mod": pm["name"], "version": pm["version"], "fp": pm["fp"]}
        else:
            notes.append(f"{pm['name']} replaces Link's model here: the Link body shows it, not Link.")
    packs: dict[str, bytes] = {}
    models: dict[str, bytes] = {}
    for cid in ids:
        r = RECIPES[cid]
        rest = None
        try:
            if r.kind == "pack":
                rest = "link" if (monster_link and r.cat == "Monsters") else None
                slot = table[cid]
                profile, problems = check_slot(romfs, slot, ainfo)
                if problems:
                    raise CharselError("; ".join(problems))
                donor = glp.read_pack(glp.find_pack(romfs, r.donor))
                res = resolve(r, lambda f: None if f == PLAYER_ANIM else clips.clips(f), rest)
                if res["missing"] or res["clash"]:
                    raise CharselError(f"clips missing {res['missing']} clash {res['clash']}")
                files = make_pack(r, slot, profile, template, donor, rest)
            elif model_copies and (cid == "linkle" and pm and pm["linkle"] or cid == "link" and pm):
                slot = table[cid]
                profile, problems = check_slot(romfs, slot, ainfo)
                if problems:
                    raise CharselError("; ".join(problems))
                if cid == "linkle":
                    src = Path(pm["where"]), pm["member"]
                    model, tex = _read_link_model(*src), _read_link_model(*src, tex=True)
                else:
                    model, tex = model_file(romfs, "Link"), model_file(romfs, "Link.Tex")
                if not model or not tex:
                    raise CharselError("Link's model or textures not found")
                models[f"Model/{ours(cid)}.sbfres"], models[f"Model/{ours(cid)}.Tex.sbfres"] = model, tex
                files = make_copy_pack(cid, slot, profile, template, pause)
                res = {"own": {}, "link": {n: c for n, c, _ in glp.ANIMS}}
            else:
                if cid == "linkle" and catalog["linkle"]["how"] == "missing":
                    catalog["skipped"][cid] = "no Linkle mod in your library: friends who pick Linkle look like Link"
                continue
        except (CharselError, glp.PackError, FormatError, aamp.AampError, OSError) as e:
            catalog["skipped"][cid] = str(e)[:300]
            say(f"  {r.name}: skipped ({e})")
            continue
        data, unpacked = glp.pack_bytes(files)
        problems = check_pack(data, slot)
        if problems:
            catalog["skipped"][cid] = "; ".join(problems)
            say(f"  {r.name}: skipped ({'; '.join(problems)})")
            continue
        packs[slot] = data
        catalog["built"][cid] = {"slot": slot, "profile": profile, "v": r.v, "name": r.name, "files": len(files),
                                 "unpacked": unpacked, "packed": len(data), "own": len(res["own"]),
                                 "link": len(res["link"]), "names": len(res["own"]) + len(res["link"]),
                                 "rest": (rest or r.rest) if r.kind == "pack" else "link", "load_mb": r.load_mb}
        say(f"  {r.name:16} on {slot:31} {len(files):3} files, {unpacked:6,} B unpacked, own clips "
            f"{len(res['own']):2}, Link's {len(res['link']):2}")
    # write: our files in the romfs folder (the kept characters' stay; every other pack or model of ours goes), the
    # catalog, and built.txt (made from the game's files: never exported as a .breath)
    top = out / "romfs"
    keep_slots = {info["slot"] for info in keep.values()}
    keep_models = {ours(cid) for cid in keep}
    if top.is_dir():
        for p in sorted(top.rglob("*"), reverse=True):
            if p.is_file() and (p.name.endswith(".sbactorpack") and p.stem in FREE_SLOTS and p.stem not in keep_slots
                                or p.name.startswith("SwChar_") and p.name.split(".")[0] not in keep_models):
                p.unlink()
    for rel, data in [(f"Actor/Pack/{slot}.sbactorpack", d) for slot, d in packs.items()] + list(models.items()):
        t = top / rel
        t.parent.mkdir(parents=True, exist_ok=True)
        if t.exists():
            t.unlink()       # (a new file, never written through a hard link the library may share)
        t.write_bytes(data)
    out.mkdir(parents=True, exist_ok=True)
    (out / BUILT_MARK).write_text(BUILT_TEXT, encoding="utf-8")
    (out / "mod.toml").write_text(MOD_TOML, encoding="utf-8")
    (out / CATALOG).write_text(json.dumps(catalog, indent=1) + "\n", encoding="utf-8")
    return catalog


def check(out=DEFAULT_OUT) -> dict:
    """Read every built pack back (no game): {id: problems}, plus the catalog's own consistency."""
    out = Path(out)
    cat = load_catalog(out)
    if not cat:
        raise CharselError(f"no {CATALOG} in {out}: build first")
    res = {}
    for cid, info in cat["built"].items():
        p = out / "romfs" / "Actor" / "Pack" / f"{info.get('slot')}.sbactorpack"
        if not p.is_file():
            res[cid] = [f"{p.name} is missing"]
            continue
        bad = check_pack(p.read_bytes(), info.get("slot"))
        if not bad:
            rb = read_back(p.read_bytes())
            if len(rb["names"]) != info.get("names"):
                bad.append(f"{len(rb['names'])} names, the catalog says {info.get('names')}")
            if rb["users"]["ModelUser"] != ours(cid) and RECIPES[cid].kind == "pack":
                bad.append(f"model list {rb['users']['ModelUser']}")
        res[cid] = bad
    return res


# ------------------------------------------------------------------------------------------------ command line

def _print_list(cat: dict | None = None) -> None:
    built = (cat or {}).get("built") or {}
    for group, ids in CATEGORIES:
        print(group)
        for cid in ids:
            r = RECIPES[cid]
            st = "built" if cid in built else ("Link body" if r.kind == "link" else "")
            print(f"  {cid:9} {r.name:16} {st:9} {r.looks}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Who am I: character packs and the wheel (docs/charsel.md).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="the characters on the wheel").add_argument("--out", type=Path, default=DEFAULT_OUT)
    b = sub.add_parser("build", help="build the character packs from your own game files")
    b.add_argument("ids", nargs="*", help="characters (default: all)")
    b.add_argument("--romfs", type=Path, default=DEFAULT_ROMFS)
    b.add_argument("--out", type=Path, default=DEFAULT_OUT)
    b.add_argument("--library", type=Path, default=DEFAULT_LIBRARY if DEFAULT_LIBRARY.is_dir() else None)
    b.add_argument("--slot", action="append", metavar="ID=ACTOR")
    b.add_argument("--model-copies", action="store_true")
    b.add_argument("--monster-link-clips", action="store_true")
    b.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    c = sub.add_parser("check", help="read every built pack back")
    c.add_argument("--out", type=Path, default=DEFAULT_OUT)
    w = sub.add_parser("wheel", help="pick who you are on the wheel (a window)")
    w.add_argument("--state", type=Path)
    w.add_argument("--out", type=Path, help="the built characters' folder (default: found)")
    p = sub.add_parser("pick", help="pick who you are without a window")
    p.add_argument("id")
    p.add_argument("--state", type=Path)
    sub.add_parser("where", help="your choice and where it is kept").add_argument("--state", type=Path)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "list":
            _print_list(load_catalog(args.out))
            return 0
        if args.cmd == "build":
            print(f"Building from {args.romfs} into {args.out}:")
            cat = build(args.romfs, args.out, args.ids, library=args.library, slots=parse_slots(args.slot),
                        model_copies=args.model_copies, monster_link=args.monster_link_clips, cache=args.cache)
            print(f"Built {len(cat['built']) - len(cat['kept'])}, kept {len(cat['kept'])}, skipped "
                  f"{len(cat['skipped'])}. Linkle: {cat['linkle']['how']}.")
            print(f"Next: python tools\\modman.py add {args.out} (once), then Play or deploy: only deployed "
                  "characters are shown.")
            for cid, why in cat["skipped"].items():
                print(f"  skipped {cid}: {why}")
            for n in cat["notes"]:
                print(f"  note: {n}")
            return 0 if cat["built"] else 1
        if args.cmd == "check":
            res = check(args.out)
            for cid, bad in res.items():
                print(f"  {cid:9} {'OK' if not bad else 'BAD: ' + '; '.join(bad)}")
            return 0 if all(not b for b in res.values()) else 1
        if args.cmd == "pick":
            cid = find_id(args.id)
            if cid is None:
                print(f"No character {args.id!r}. Pick one of: {', '.join(order())}")
                return 1
            save_choice(cid, args.state)
            print(f"Done. Others see you as {RECIPES[cid].name.split(' (')[0]}.")
            return 0
        if args.cmd == "where":
            path = args.state or state_path()
            ch = load_choice(path)
            print(f"You: {RECIPES[ch['look']].name} ({path})")
            return 0
        ch = load_choice(args.state)
        cat, _where = here_catalog(args.out or ch["packs"])
        got = choose_window(ch["look"], cat.get("built") if cat else None,
                            (cat.get("linkle") or {}).get("how") == "link-body" if cat else None)
        if got:
            save_choice(got, args.state)
            print(f"Done. Others see you as {RECIPES[got].name.split(' (')[0]}.")
        return 0
    except CharselError as e:
        print(f"charsel: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
