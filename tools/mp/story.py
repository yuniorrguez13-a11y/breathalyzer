#!/usr/bin/env python3
"""Story together (docs/coop.md): the host owns the story; a friend's co-op save gets the host's progress ADDED.

    python tools/mp/story.py list  --romfs <your game's romfs> --out <library>/mp/story-list.txt
    python tools/mp/story.py show  --list <story-list.txt> --save <a co-op save folder>
    python tools/mp/story.py merge --list <story-list.txt> --save <friend's co-op folder> --bits <hex> \
                                   --backups <folder> [--dlc]
    python tools/mp/story.py undo  --save <friend's co-op folder> --backups <folder> [--before-coop]

What counts as story: a bool game flag that is saved, never reset (ResetType 0) and starts at 0, in one of these
kinds: main quest steps, Champions' Ballad steps (DLC), Divine Beasts, shrines, towers, memories, paraglider and
runes, cutscenes seen. So adding is always 0 -> 1, and only the game's own story flags are touched. The list is built
on each PC from its own game files (names only; never sent, never in the repo); both sides compare its hash.

What a friend gets (settle()): whole units only. A Divine Beast's flags once it is freed (Clear_Remains<X>); a main
quest's steps once it is finished (until then only <quest>_Activated); never a step that hands out a reward
(NOT_SHARED); nothing in HELD (shrine clears and runes: held back until a game check shows they are safe).

Save safety (never break it):
- only co-op copies are written, never your own save folder (the caller picks the folder; the launcher/agent uses
  the multiplayer library); the first merge into a copy keeps a `before-coop` backup that is never rotated away,
  later merges that add something keep a timestamped one (newest KEEP_BACKUPS); `undo` restores either, after
  backing up what it replaces;
- a merge only ever changes 0 to 1, in place (the file size never changes): every slot's `game_data.sav.new` is
  written, flushed to disk and read back first; only when all check out are they renamed over (else none is);
- nothing is written while a game uses the folder: Savage Wind's own lock (`<folder>.lock`, NEXT TO the folder,
  held by a running game) is taken for the whole backup + merge (+ undo, + copy), and a running savagewind.exe is a
  second guard;
- only a folder marked as a co-op copy (COPY_MARK, written when the copy is made) is ever merged or restored, and a
  copy bound to one host (its key fingerprint) is never merged with another host's story.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

SAVE_NAME = "game_data.sav"
SAVE_MAGIC = 0x4730
HEADER = 12          # u32 magic, u32 0xffffffff, u32 1
LIST_FILE = "story-list.txt"
COPY_MARK = "breathalyzer-coop.json"   # in every co-op copy's top folder (tools/mp/worlds.py writes it)
KEEP_BACKUPS = 10
KEEP_UNDONE = 3
BEFORE_COOP = "before-coop"            # the first backup of a copy: never rotated away
UNDONE = "undone"                      # what an undo replaced
_STAMP_RE = re.compile(r"\d{8}-\d{6}(-\d+)?")

# Kinds, in list order. `dlc` kinds are shared only when both players have the DLC.
_SLATE = ("IsGet_PlayerStole2|IsGet_Obj_(RemoteBomb|RemoteBombCube|Magnetglove|StopTimer|IceMaker|Camera|"
          "SheikSensor|PictureBook|RemoteBombLv2|StopTimerLv2|SheikSensorLv2|SheikPadLv2)")
_QUESTS = (r"FirstTower|FindDungeon|Find_Impa|Find_4Relic|Wind_Relic|Fire_Relic|GotoZoraVillage|Water_Relic|"
           r"Drag_Hero|Electric_Relic|CarryingBlueFireEXMini|PictureMemory|FairyFountain|GanonQuest")
KINDS = (
    ("main", "main quests", rf"({_QUESTS})_\w+|Get_MasterSword_Activated"),
    ("ballad", "Champions' Ballad", r"BalladOfHero(es|Gerudo|Goron|Zora|Rito)_\w+"),
    ("beasts", "Divine Beasts", r"(Clear|Enter|FirstMessage)_Remains(Wind|Fire|Water|Electric)|"
                                r"(Appear|Die)_PGanon(Wind|Fire|Water|Electric)"),
    ("shrines", "shrines", r"(Open|Enter|Clear)_Dungeon\d{3}"),
    ("towers", "towers", r"MapTower_\d\d|MapTower_DemoFirst|MapTower_02_Info|MapTower_07_Demo"),
    ("memories", "memories", r"IsGet_MemoryPhoto_\d{3}"),
    ("slate", "paraglider and runes", _SLATE),
    ("cutscenes", "cutscenes seen", r"IsPlayed_Demo\d+_\d+"),
)
_KIND_RE = [(k, re.compile(p)) for k, _, p in KINDS]
KIND_LABEL = {k: label for k, label, _ in KINDS}
NEVER = frozenset({"Get_MasterSword_Finish"})   # the guest's sword stays in its pedestal (an item, his own)
FIRST_DLC_SHRINE = 120

# -- what a friend may get (settle) --
# A step that hands out a reward: marking it done would cost the friend that reward in his copy (items aren't shared).
NOT_SHARED = frozenset({"PictureMemory_GetShirt"})   # (Impa gives the Champion's Tunic at this step)
# Held back until a game check shows they are safe (docs/coop.md "Held back"):
HELD = (
    re.compile(r"Clear_Dungeon\d{3}"),   # a cleared shrine's monk may not give his Spirit Orb any more
    re.compile(r"IsGet_Obj_\w+"),        # runes: if they are items, a set flag would stop the guest getting them
)
# Kinds a friend's running game may get live (coop_story.c allows only these too); the rest waits for the next join.
LIVE_KINDS = frozenset({"towers", "memories"})
_BEAST_RE = re.compile(r"(?:Clear|Enter|FirstMessage)_Remains(Wind|Fire|Water|Electric)|"
                       r"(?:Appear|Die)_PGanon(Wind|Fire|Water|Electric)")
_QUEST_RE = re.compile(rf"({_QUESTS}|BalladOfHero(?:es|Gerudo|Goron|Zora|Rito))_(\w+)")
_QUEST_DONE = ("Finish", "Finished")
_FREED_STEP_RE = re.compile(r"Find_4Relic_(1st|2nd|3rd|4th)Clear")   # "N beasts freed": a fact, not a mid-step


def kind_of(name: str) -> str | None:
    """The story kind of a flag name, or None if it isn't story."""
    if name in NEVER:
        return None
    for kind, rx in _KIND_RE:
        if rx.fullmatch(name):
            return kind
    return None


def is_dlc(name: str) -> bool:
    if kind_of(name) == "ballad":
        return True
    m = re.fullmatch(r"(Open|Enter|Clear)_Dungeon(\d{3})", name)
    return bool(m) and int(m.group(2)) >= FIRST_DLC_SHRINE


def name_hash(name: str) -> int:
    """The number the save file (and the game's flag manager) uses for a flag name: plain CRC-32."""
    return zlib.crc32(name.encode("ascii")) & 0xffffffff


def held(name: str) -> bool:
    return name in NOT_SHARED or any(rx.fullmatch(name) for rx in HELD)


def settle(names: list[str], bits) -> set[int]:
    """The host's bits a friend may get: settled units only.
    - a Divine Beast's flags (entered, first message, Blight seen or beaten) only once that beast is freed
      (Clear_Remains<X>): a host caught inside a beast or mid-battle gives nothing of it yet;
    - a main quest's (and a Ballad's) in-between steps only once it is finished (<quest>_Finish or _Finished); until
      then only <quest>_Activated. Find_4Relic's "Nth beast freed" steps go with the freed beasts;
    - never NOT_SHARED, nothing HELD."""
    have = {names[i]: i for i in bits if isinstance(i, int) and 0 <= i < len(names)}
    out = set()
    for name, i in have.items():
        if held(name):
            continue
        m = _BEAST_RE.fullmatch(name)
        if m:
            if f"Clear_Remains{m.group(1) or m.group(2)}" in have:
                out.add(i)
            continue
        m = _QUEST_RE.fullmatch(name)
        if m and not _FREED_STEP_RE.fullmatch(name):
            quest, step = m.groups()
            if step != "Activated" and not any(f"{quest}_{d}" in have for d in _QUEST_DONE):
                continue
        out.add(i)
    return out


def live_ok(name: str) -> bool:
    """May this flag be added to a friend's RUNNING game (towers and memories only)?"""
    return kind_of(name) in LIVE_KINDS and not held(name)


# -- the list --

@dataclass
class StoryList:
    base: list[str]
    dlc: list[str] = field(default_factory=list)

    def names(self, dlc: bool) -> list[str]:
        return self.base + (self.dlc if dlc else [])

    @staticmethod
    def _hash(names: list[str]) -> str:
        return hashlib.sha256("\n".join(names).encode()).hexdigest()[:8]

    @property
    def base_hash(self) -> str:
        return self._hash(self.base)

    @property
    def dlc_hash(self) -> str | None:
        return self._hash(self.dlc) if self.dlc else None

    def list_hash(self, dlc: bool) -> str:
        """The hash both sides compare: the base list's, or base + DLC's when the DLC is shared."""
        return self.base_hash if not dlc else self._hash(self.names(True))

    def hello(self) -> dict:
        """The hello's "story" field."""
        return {"v": 1, "base": self.base_hash, "dlc": self.dlc_hash}

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Story list (names only), built from this PC's own game files by tools/mp/story.py"]
        lines += [f"base {n}" for n in self.base] + [f"dlc {n}" for n in self.dlc]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "StoryList":
        base, dlc = [], []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            part, _, name = line.strip().partition(" ")
            if part in ("base", "dlc") and kind_of(name) and re.fullmatch(r"\w{1,64}", name):
                (base if part == "base" else dlc).append(name)
        return cls(base, dlc)


def order(names) -> list[str]:
    """Story names in list order: by kind, then by name."""
    rank = {k: i for i, (k, _, _) in enumerate(KINDS)}
    return sorted(set(names), key=lambda n: (rank[kind_of(n)], n))


def make_list(flags, saved_hashes=None, main_steps=None) -> StoryList:
    """The story list from game-data rows: an iterable of (name, type, row) with type 'bool_data' and the row's
    'ResetType', 'InitValue', 'IsSave'. `saved_hashes` (the save format's hashes): only flags kept in the save.
    `main_steps` (the main quests' step flags from the game's quest pack): main quest and Ballad flags must be one."""
    keep = []
    for name, typ, row in flags:
        kind = kind_of(name)
        if typ != "bool_data" or kind is None:
            continue
        if main_steps is not None and kind in ("main", "ballad") and name not in main_steps:
            continue
        if row.get("ResetType") != 0 or row.get("InitValue") or row.get("IsSave") is False:
            continue
        if saved_hashes is not None and name_hash(name) not in saved_hashes:
            continue
        keep.append(name)
    names = order(keep)
    return StoryList([n for n in names if not is_dlc(n)], [n for n in names if is_dlc(n)])


def list_from_romfs(romfs: Path) -> StoryList:
    """Read Bootup.pack's game data and save format from the player's own game (read only)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import wiiu_convert as W   # (the repo's SARC / Yaz0 / BYML readers)

    def val(x):
        return x[1] if isinstance(x, tuple) and len(x) == 2 and isinstance(x[0], str) else x

    def unyaz(d):
        return W.yaz0_decompress(d)[0] if d[:4] == b"Yaz0" else d

    boot = dict(W.sarc_files((Path(romfs) / "Pack" / "Bootup.pack").read_bytes()))
    rows = []
    for _, d in W.sarc_files(unyaz(boot["GameData/gamedata.ssarc"])):
        for typ, entries in W.byml_load(d).items():
            for r in entries:
                rows.append((str(val(r.get("DataName"))), typ, {k: val(v) for k, v in r.items()}))
    saved = set()
    for _, d in W.sarc_files(unyaz(boot["GameData/savedataformat.ssarc"])):
        fl = W.byml_load(d).get("file_list")
        if fl and fl[0].get("file_name") == SAVE_NAME:
            saved.update(val(e.get("HashValue")) & 0xffffffff for e in fl[1])
    title = dict(W.sarc_files((Path(romfs) / "Pack" / "TitleBG.pack").read_bytes()))
    steps = set()
    for q in W.byml_load(unyaz(title["Quest/QuestProduct.sbquestpack"])):
        if val(q.get("Type")) == "Main":
            steps.update(str(val(s.get("NextFlag"))) for s in (val(q.get("Steps")) or []))
    return make_list(rows, saved, steps)


# -- bitsets (the "story" line: 2 hex characters a byte, bit i = byte i // 8, bit i % 8 from the low end) --

def encode_bits(indexes, n: int) -> str:
    out = bytearray((n + 7) // 8)
    for i in indexes:
        if not 0 <= i < n:
            raise ValueError(f"bit {i} outside 0..{n - 1}")
        out[i >> 3] |= 1 << (i & 7)
    return out.hex()


def decode_bits(text, n: int) -> set[int] | None:
    """The set bits of a story line, or None if it isn't exactly the right length of lowercase hex, or sets a bit
    past n (a line for another list)."""
    if not isinstance(text, str) or len(text) != 2 * ((n + 7) // 8) or not re.fullmatch(r"[0-9a-f]*", text):
        return None
    data = bytes.fromhex(text)
    bits = {i for i in range(len(data) * 8) if data[i >> 3] >> (i & 7) & 1}
    return None if any(i >= n for i in bits) else bits


# -- save files --

def read_pairs(data: bytes) -> dict[int, list[int]]:
    """Flag hash -> offsets of its values. Raises ValueError if this isn't a game_data.sav."""
    if len(data) < HEADER + 8 or struct.unpack_from("<I", data, 0)[0] != SAVE_MAGIC:
        raise ValueError("not a game_data.sav")
    offs: dict[int, list[int]] = {}
    for off in range(HEADER, len(data) - 7, 8):
        offs.setdefault(struct.unpack_from("<I", data, off)[0], []).append(off + 4)
    return offs


def bits_of(data: bytes, names: list[str]) -> set[int]:
    offs = read_pairs(data)
    out = set()
    for i, name in enumerate(names):
        o = offs.get(name_hash(name))
        if o and len(o) == 1 and struct.unpack_from("<I", data, o[0])[0] == 1:
            out.add(i)
    return out


def story_bits(save: Path, names: list[str]) -> set[int]:
    """Which listed story flags a save file has set."""
    return bits_of(Path(save).read_bytes(), names)


def plan_file(data: bytes, names: list[str], bits) -> tuple[bytes, list[str], list[int]]:
    """What a merge would make of one save file's bytes: (new bytes, names added, offsets changed). Only the settled
    host bits (settle()), only 0 -> 1, only a flag that is there once; nothing else moves."""
    offs = read_pairs(data)
    new = bytearray(data)
    changed, added = [], []
    for i in sorted(settle(names, bits)):
        o = offs.get(name_hash(names[i]))
        if not o or len(o) != 1:
            continue
        if struct.unpack_from("<I", data, o[0])[0] == 0:
            struct.pack_into("<I", new, o[0], 1)
            changed.append(o[0])
            added.append(names[i])
    return bytes(new), added, changed


def _write_checked(path: Path, old: bytes, new: bytes, changed: list[int]) -> Path:
    """Write `new` to <path>.new, flush it to disk, read it back and check that only `changed` moved (to 1)."""
    tmp = path.with_name(path.name + ".new")
    with open(tmp, "wb") as f:
        f.write(new)
        f.flush()
        os.fsync(f.fileno())
    back = tmp.read_bytes()
    allowed = set(changed)
    if len(back) != len(old) or any(
            back[k:k + 4] != old[k:k + 4] for k in range(0, len(old), 4) if k not in allowed) or any(
            struct.unpack_from("<I", back, k)[0] != 1 for k in changed):
        raise OSError(f"{path}: the written copy doesn't check out; nothing was changed")
    return tmp


def merge_file(save: Path, names: list[str], bits) -> list[str]:
    """Add the host's settled story bits to one save file (in place, size unchanged, checked after writing). Returns
    the names added. A flag that isn't there, isn't a single bool value, or is already 1 is left alone. (No lock:
    merge_folder is the safe way in; this is for one file the caller already holds.)"""
    save = Path(save)
    data = save.read_bytes()
    new, added, changed = plan_file(data, names, bits)
    if not changed:
        return []
    tmp = save.with_name(save.name + ".new")
    try:
        _write_checked(save, data, new, changed)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, save)
    return added


def slot_files(folder: Path) -> list[Path]:
    """Every game_data.sav in a save folder: <folder>/<n>/ or <folder>/save_0/<n>/ (numbered slots only)."""
    folder = Path(folder)
    out = []
    for root in (folder, folder / "save_0"):
        if root.is_dir():
            out += sorted(p / SAVE_NAME for p in root.iterdir() if p.name.isdigit() and (p / SAVE_NAME).is_file())
    return out


def newest_slot(folder: Path) -> Path | None:
    files = slot_files(folder)
    return max(files, key=lambda p: p.stat().st_mtime_ns) if files else None


# -- the co-op copy mark --

def read_mark(folder: Path) -> dict:
    try:
        mark = json.loads((Path(folder) / COPY_MARK).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return mark if isinstance(mark, dict) else {}


def _write_mark(folder: Path, mark: dict) -> None:
    path = Path(folder) / COPY_MARK
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(mark) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def is_coop_copy(folder: Path) -> bool:
    return read_mark(folder).get("coop_copy") is True


def mark_coop_copy(folder: Path, source: str = "", check: str | None = None) -> None:
    """Mark a folder as a co-op copy (only for a folder that IS a copy: tools/mp/worlds.py does it). `check`: the
    host's check words when the copy is made for one host's world."""
    mark = {"coop_copy": True, "from": source}
    if check:
        mark["check"] = check
    _write_mark(folder, mark)


def _need_copy(folder: Path) -> None:
    if not is_coop_copy(folder):
        raise PermissionError(f"{folder} isn't a co-op copy (no {COPY_MARK}): co-op never writes your own save")


class WrongHost(ValueError):
    """This co-op copy belongs to another host's world."""


def _check_host(folder: Path, host_key: str | None, check_words=None) -> dict:
    """The copy's mark, after checking it is for this host (its key fingerprint, and the check words it was made
    for). A copy not bound yet is bound to this host (the caller writes the mark)."""
    mark = read_mark(folder)
    if host_key is None:
        return mark
    if mark.get("host") not in (None, host_key):
        raise WrongHost(f"{Path(folder).name} is the co-op copy of another host's world: its story isn't added")
    if check_words is not None and mark.get("check") not in (None, check_words(host_key)):
        raise WrongHost(f"{Path(folder).name} was made for another host (other check words): its story isn't added")
    return mark


# -- the game's lock on a save folder --

class GameRunning(OSError):
    """A game uses (or may use) this save folder: nothing was written."""


def lock_path(folder: Path) -> Path:
    """Savage Wind's lock file for a save folder: the folder's path plus ".lock", NEXT TO the folder (the game holds
    it with LockFileEx, byte 0, for as long as it runs; the file stays after the game ends)."""
    return Path(os.path.abspath(str(folder)).rstrip("\\/") + ".lock")


class SaveLock:
    """Take Savage Wind's own lock on a save folder without waiting (the same byte the game locks). Busy: a game
    uses the folder (GameRunning). Held until release(); the game can't take it meanwhile. create=False: never make
    the lock file (a folder that is only read: no file means no game has locked it)."""

    def __init__(self, folder: Path, create: bool = True):
        self.path = lock_path(folder)
        self.create = create
        self.fd: int | None = None

    def acquire(self) -> "SaveLock":
        flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
        if self.create:
            flags |= os.O_CREAT
        try:
            fd = os.open(self.path, flags, 0o644)
        except FileNotFoundError:
            if self.create:
                raise
            return self   # (no lock file, and not ours to make)
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise GameRunning(f"a game uses this save ({self.path.name} is locked): close the game first") from None
        self.fd = fd
        return self

    def release(self) -> None:
        if self.fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


@contextlib.contextmanager
def hold(folder: Path, processes=None, create: bool = True):
    """Hold a save folder for writing (or a safe copy): its lock taken, and no savagewind.exe running (a second
    guard; `processes`: names to check, default ask Windows, [] = the lock alone). Raises GameRunning."""
    with SaveLock(folder, create=create):
        if processes is None:
            processes = _running_processes()
        if any(p.lower() == "savagewind.exe" for p in processes):
            raise GameRunning("Savage Wind is running: close the game first")
        yield


def game_running(folder: Path, processes=None) -> str | None:
    """Why the folder can't be written now, or None (a quick look: the writers hold the lock themselves)."""
    try:
        with hold(folder, processes, create=False):
            return None
    except GameRunning as e:
        return str(e)


def _running_processes() -> list[str]:
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh", "/fi", "IMAGENAME eq savagewind.exe"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return ["savagewind.exe"]   # (can't tell: act as if it runs)
    return [line.split(",")[0].strip('"') for line in out.splitlines() if line.startswith('"')]


# -- backups --

def _stamped(backups: Path) -> list[Path]:
    backups = Path(backups)
    if not backups.is_dir():
        return []
    return sorted((p for p in backups.iterdir() if p.is_dir() and _STAMP_RE.fullmatch(p.name)), key=lambda p: p.name)


def backup(folder: Path, backups: Path, keep: int = KEEP_BACKUPS) -> Path:
    """Copy the whole co-op save folder to <backups>/<time>/ and keep the newest `keep` timestamped backups (the
    `before-coop` one is never counted or removed)."""
    folder, backups = Path(folder), Path(backups)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = backups / stamp
    n = 1
    while dest.exists():
        n += 1
        dest = backups / f"{stamp}-{n}"
    shutil.copytree(folder, dest)
    old = _stamped(backups)
    for p in old[:-keep] if keep > 0 else []:
        shutil.rmtree(p, ignore_errors=True)
    return dest


def before_coop(backups: Path) -> Path | None:
    p = Path(backups) / BEFORE_COOP
    return p if p.is_dir() else None


def newest_backup(backups: Path) -> Path | None:
    """The backup from before the last merge that added something (or the before-coop one)."""
    found = _stamped(backups)
    return found[-1] if found else before_coop(backups)


def undo(folder: Path, backups: Path, processes=None, which: str = "last") -> Path:
    """Put a backup back (byte for byte): which="last" (from before the last join that added story) or
    "before-coop" (from before the first). What it replaces is backed up first (<backups>/undone/). Refuses while a
    game uses the folder, for a folder that isn't a co-op copy, and for a backup of another copy."""
    _need_copy(folder)
    if which not in ("last", BEFORE_COOP):
        raise ValueError("undo: which is 'last' or 'before-coop'")
    folder = Path(folder)
    with hold(folder, processes):
        src = before_coop(backups) if which == BEFORE_COOP else newest_backup(backups)
        if src is None:
            raise FileNotFoundError("no story backup to restore")
        mine, theirs = read_mark(folder), read_mark(src)
        if theirs.get("coop_copy") is not True or theirs.get("from") != mine.get("from") or \
                (theirs.get("host") and mine.get("host") and theirs["host"] != mine["host"]):
            raise ValueError(f"{src} is a backup of another co-op copy: nothing was changed")
        backup(folder, Path(backups) / UNDONE, keep=KEEP_UNDONE)
        tmp = folder.with_name(folder.name + ".undo-tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(src, tmp)
        old = folder.with_name(folder.name + ".undo-old")
        if old.exists():
            shutil.rmtree(old)
        os.replace(folder, old)
        os.replace(tmp, folder)
        shutil.rmtree(old, ignore_errors=True)
    return src


# -- the merge --

@dataclass
class MergeResult:
    backup: Path | None
    added: dict[str, list[str]]      # slot file -> names added

    def summary(self, host: str = "the host") -> str:
        names = sorted({n for v in self.added.values() for n in v})
        if not names:
            return f"Your co-op save already has {host}'s story."
        counts: dict[str, int] = {}
        for n in names:
            counts[kind_of(n)] = counts.get(kind_of(n), 0) + 1
        parts = [f"{c} {KIND_LABEL[k]}" for k, c in counts.items()]
        return f"{host}'s story added: " + ", ".join(parts) + "."


def merge_folder(folder: Path, names: list[str], bits, backups: Path, *, made_backup: bool = False,
                 processes=None, host_key: str | None = None, check_words=None) -> MergeResult:
    """Story at join: add the host's settled bits to every numbered slot of a friend's co-op copy.

    Holds the game's lock on the folder the whole time (GameRunning if a game uses it). Nothing to add: nothing is
    written, no backup. Else: the first merge ever keeps <backups>/before-coop/; a later one makes a timestamped
    backup (once a session: pass made_backup=True after the first). Every slot's .new is written and checked before
    any is renamed. `host_key`: the host's key fingerprint; a copy bound to another host is refused (WrongHost), an
    unbound one is bound to this host."""
    _need_copy(folder)
    folder = Path(folder)
    with hold(folder, processes):
        mark = _check_host(folder, host_key, check_words)
        files = slot_files(folder)
        if not files:
            raise FileNotFoundError(f"no {SAVE_NAME} in {folder}")
        for f in files:   # (a .new left by a crash is never used)
            f.with_name(f.name + ".new").unlink(missing_ok=True)
        plans = []
        added: dict[str, list[str]] = {}
        for f in files:
            data = f.read_bytes()
            new, names_added, changed = plan_file(data, names, bits)
            added[str(f)] = names_added
            if changed:
                plans.append((f, data, new, changed))
        if host_key is not None and mark.get("host") is None:
            mark["host"] = host_key
            _write_mark(folder, mark)
        if not plans:
            return MergeResult(None, added)
        dest = None
        if before_coop(backups) is None:
            Path(backups).mkdir(parents=True, exist_ok=True)
            dest = Path(backups) / BEFORE_COOP
            tmp = dest.with_name(BEFORE_COOP + ".copying")
            if tmp.exists():
                shutil.rmtree(tmp)
            shutil.copytree(folder, tmp)
            os.replace(tmp, dest)
        elif not made_backup:
            dest = backup(folder, backups)
        written = []
        try:
            for f, data, new, changed in plans:
                written.append(_write_checked(f, data, new, changed))
        except BaseException:
            for t in written + [f.with_name(f.name + ".new") for f, *_ in plans]:
                Path(t).unlink(missing_ok=True)
            raise
        try:
            for (f, *_), tmp in zip(plans, written):
                os.replace(tmp, f)   # (each slot is whole either way: merged, or as it was)
        finally:
            for tmp in written:
                Path(tmp).unlink(missing_ok=True)
        return MergeResult(dest, added)


# The opt-in question, asked every session (it starts on Yes: it is backed up and undoable).
def story_question(host: str) -> list[str]:
    return [f"Share story progress with {host}?",
            f"Your co-op save gets {host}'s towers, quests, Divine Beasts and shrine warps.",
            "Nothing is taken away. A backup is made first; you can undo it.",
            "Freed Divine Beasts give you no Heart Container in this copy.",
            "[ Yes, share (Y / Enter) ]      [ No (N / Esc) ]"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Story together: build the story list, add a host's story to a co-op "
                                             "save copy (backup first), undo.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list", help="build the story list from your own game files (names only)")
    p.add_argument("--romfs", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p = sub.add_parser("show", help="the story line of a save folder's newest slot")
    p.add_argument("--list", required=True, type=Path)
    p.add_argument("--save", required=True, type=Path)
    p.add_argument("--dlc", action="store_true")
    p = sub.add_parser("merge", help="add a host's story line to every slot of a co-op save copy")
    p.add_argument("--list", required=True, type=Path)
    p.add_argument("--save", required=True, type=Path)
    p.add_argument("--bits", required=True)
    p.add_argument("--backups", required=True, type=Path)
    p.add_argument("--dlc", action="store_true")
    p = sub.add_parser("undo", help="put a backup back (default: from before the last join that added story)")
    p.add_argument("--save", required=True, type=Path)
    p.add_argument("--backups", required=True, type=Path)
    p.add_argument("--before-coop", action="store_true", help="the backup from before the first co-op story")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "list":
            sl = list_from_romfs(a.romfs)
            sl.save(a.out)
            print(f"{len(sl.base)} story flags (+{len(sl.dlc)} DLC) -> {a.out}  hash {sl.base_hash}")
        elif a.cmd == "show":
            names = StoryList.load(a.list).names(a.dlc)
            newest = newest_slot(a.save)
            if newest is None:
                print(f"no {SAVE_NAME} in {a.save}")
                return 1
            bits = story_bits(newest, names)
            print(f"{len(bits)} of {len(names)} story flags set; line:")
            print(encode_bits(bits, len(names)))
        elif a.cmd == "merge":
            names = StoryList.load(a.list).names(a.dlc)
            bits = decode_bits(a.bits.strip().lower(), len(names))
            if bits is None:
                print("That story line doesn't fit this story list (another game version or DLC?). Nothing changed.")
                return 1
            res = merge_folder(a.save, names, bits, a.backups)
            print(res.summary())
            print(f"Backup: {res.backup}")
        elif a.cmd == "undo":
            print(f"Restored {undo(a.save, a.backups, which=BEFORE_COOP if a.before_coop else 'last')}")
    except (OSError, ValueError, KeyError) as e:
        print(f"Stopped: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
