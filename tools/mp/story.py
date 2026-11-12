#!/usr/bin/env python3
"""Story together (docs/coop.md): the host owns the story; a friend's co-op save gets the host's progress ADDED.

    python tools/mp/story.py list  --romfs <your game's romfs> --out <library>/mp/story-list.txt
    python tools/mp/story.py show  --list <story-list.txt> --save <a co-op save folder>
    python tools/mp/story.py merge --list <story-list.txt> --save <friend's co-op folder> --bits <hex> \
                                   --backups <folder> [--dlc]
    python tools/mp/story.py undo  --save <friend's co-op folder> --backups <folder>

What counts as story: a bool game flag that is saved, never reset (ResetType 0) and starts at 0, in one of these
kinds: main quest steps, Champions' Ballad steps (DLC), Divine Beasts, shrines, towers, memories, paraglider and
runes, cutscenes seen. So adding is always 0 -> 1, and only the game's own story flags are touched. The list is built
on each PC from its own game files (names only; never sent, never in the repo); both sides compare its hash.

Save safety (never break it):
- only co-op copies are written, never your own save folder (the caller picks the folder; the launcher/agent uses
  the multiplayer library); a whole-folder backup is made before the first merge of a session; `undo` restores it;
- a merge only ever changes 0 to 1, in place (the file size never changes), writes `game_data.sav.new`, reads it back,
  checks that only those values changed, then renames it over;
- nothing is written while a game uses the folder (Savage Wind's `save.lock`, or a running savagewind.exe);
- only a folder marked as a co-op copy (COPY_MARK, written when the copy is made) is ever merged or restored.
"""
from __future__ import annotations

import argparse
import hashlib
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

# Kinds, in list order. `dlc` kinds are shared only when both players have the DLC.
_SLATE = ("IsGet_PlayerStole2|IsGet_Obj_(RemoteBomb|RemoteBombCube|Magnetglove|StopTimer|IceMaker|Camera|"
          "SheikSensor|PictureBook|RemoteBombLv2|StopTimerLv2|SheikSensorLv2|SheikPadLv2)")
KINDS = (
    ("main", "main quests", r"(FirstTower|FindDungeon|Find_Impa|Find_4Relic|Wind_Relic|Fire_Relic|GotoZoraVillage|"
                            r"Water_Relic|Drag_Hero|Electric_Relic|CarryingBlueFireEXMini|PictureMemory|"
                            r"FairyFountain|GanonQuest)_\w+|Get_MasterSword_Activated"),
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


def story_bits(save: Path, names: list[str]) -> set[int]:
    """Which listed story flags a save file has set."""
    data = Path(save).read_bytes()
    offs = read_pairs(data)
    out = set()
    for i, name in enumerate(names):
        o = offs.get(name_hash(name))
        if o and len(o) == 1 and struct.unpack_from("<I", data, o[0])[0] == 1:
            out.add(i)
    return out


def merge_file(save: Path, names: list[str], bits: set[int]) -> list[str]:
    """Add the host's story bits to one save file: only 0 -> 1, in place, size unchanged, checked after writing.
    Returns the names added. A flag that isn't there, isn't a single bool value, or is already 1 is left alone."""
    save = Path(save)
    data = save.read_bytes()
    offs = read_pairs(data)
    new = bytearray(data)
    changed, added = [], []
    for i in sorted(bits):
        if not 0 <= i < len(names):
            continue
        o = offs.get(name_hash(names[i]))
        if not o or len(o) != 1:
            continue
        if struct.unpack_from("<I", data, o[0])[0] == 0:
            struct.pack_into("<I", new, o[0], 1)
            changed.append(o[0])
            added.append(names[i])
    if not changed:
        return []
    tmp = save.with_name(save.name + ".new")
    tmp.write_bytes(bytes(new))
    back = tmp.read_bytes()
    allowed = set(changed)
    if len(back) != len(data) or any(
            back[k:k + 4] != data[k:k + 4] for k in range(0, len(data), 4) if k not in allowed) or any(
            struct.unpack_from("<I", back, k)[0] != 1 for k in changed):
        tmp.unlink()
        raise OSError(f"{save}: the written copy doesn't check out; nothing was changed")
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


def is_coop_copy(folder: Path) -> bool:
    return (Path(folder) / COPY_MARK).is_file()


def mark_coop_copy(folder: Path, source: str = "") -> None:
    """Mark a folder as a co-op copy (only for a folder that IS a copy: tools/mp/worlds.py does it)."""
    import json
    (Path(folder) / COPY_MARK).write_text(json.dumps({"coop_copy": True, "from": source}) + "\n", encoding="utf-8")


def _need_copy(folder: Path) -> None:
    if not is_coop_copy(folder):
        raise PermissionError(f"{folder} isn't a co-op copy (no {COPY_MARK}): co-op never writes your own save")


def game_running(folder: Path, processes=None) -> str | None:
    """Why the folder can't be written now, or None. `processes`: names to check (default: ask Windows)."""
    folder = Path(folder)
    for lock in (folder / "save.lock", folder / "save_0" / "save.lock"):
        if lock.exists():
            return f"a game uses this save ({lock.name} is there)"
    if processes is None:
        processes = _running_processes()
    if any(p.lower() == "savagewind.exe" for p in processes):
        return "Savage Wind is running: close the game first"
    return None


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


def backup(folder: Path, backups: Path, keep: int = KEEP_BACKUPS) -> Path:
    """Copy the whole co-op save folder to <backups>/<time>/ and keep the newest `keep` backups."""
    folder, backups = Path(folder), Path(backups)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = backups / stamp
    n = 1
    while dest.exists():
        n += 1
        dest = backups / f"{stamp}-{n}"
    shutil.copytree(folder, dest)
    old = sorted((p for p in backups.iterdir() if p.is_dir()), key=lambda p: p.name)
    for p in old[:-keep] if keep > 0 else []:
        shutil.rmtree(p, ignore_errors=True)
    return dest


def newest_backup(backups: Path) -> Path | None:
    backups = Path(backups)
    if not backups.is_dir():
        return None
    found = sorted((p for p in backups.iterdir() if p.is_dir()), key=lambda p: p.name)
    return found[-1] if found else None


def undo(folder: Path, backups: Path, processes=None) -> Path:
    """Put the newest backup back (byte for byte). Refuses while a game uses the folder, and for a folder that
    isn't a co-op copy."""
    _need_copy(folder)
    why = game_running(folder, processes)
    if why:
        raise OSError(why)
    src = newest_backup(backups)
    if src is None:
        raise FileNotFoundError("no story backup to restore")
    folder = Path(folder)
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


def merge_folder(folder: Path, names: list[str], bits: set[int], backups: Path, *, made_backup: bool = False,
                 processes=None) -> MergeResult:
    """Story at join: back up the friend's co-op folder (once a session: pass made_backup=True after the first), then
    add the host's bits to every numbered slot. Refuses while a game uses the folder, and for a folder that isn't a
    co-op copy (your own save)."""
    _need_copy(folder)
    why = game_running(folder, processes)
    if why:
        raise OSError(why)
    files = slot_files(folder)
    if not files:
        raise FileNotFoundError(f"no {SAVE_NAME} in {folder}")
    dest = None if made_backup else backup(folder, backups)
    return MergeResult(dest, {str(f): merge_file(f, names, bits) for f in files})


# The opt-in question, asked every session (it starts on Yes: it is backed up and undoable).
def story_question(host: str) -> list[str]:
    return [f"Share story progress with {host}?",
            f"Your co-op save gets {host}'s story: towers, shrines, Divine Beasts, quests.",
            "Nothing is taken away. A backup is made first.",
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
    p = sub.add_parser("undo", help="put the newest backup back")
    p.add_argument("--save", required=True, type=Path)
    p.add_argument("--backups", required=True, type=Path)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "list":
            sl = list_from_romfs(a.romfs)
            sl.save(a.out)
            print(f"{len(sl.base)} story flags (+{len(sl.dlc)} DLC) -> {a.out}  hash {sl.base_hash}")
        elif a.cmd == "show":
            names = StoryList.load(a.list).names(a.dlc)
            files = sorted(slot_files(a.save), key=lambda f: f.stat().st_mtime)
            if not files:
                print(f"no {SAVE_NAME} in {a.save}")
                return 1
            bits = story_bits(files[-1], names)
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
            print(f"Restored {undo(a.save, a.backups)}")
    except (OSError, ValueError, KeyError) as e:
        print(f"Stopped: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
