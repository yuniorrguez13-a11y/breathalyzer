"""Co-op save folders (docs/coop.md): co-op always plays on COPIES, so nobody's own save is ever written.

    <mp library>/worlds/<world-id>/                  a host's world (made once from a copy of the host's newest save)
    <mp library>/joined/<world-id>-<check words>/    a friend's copy for one host's world (made once from his save)
    <mp library>/mp/backups/<folder>/                story backups (tools/mp/story.py)

A friend's copy is keyed by the world name AND the host's check words (from his key), so two hosts called "Ana"
never share a copy; the copy's mark keeps the check words, and the first story merge binds it to that host's key
(story.merge_folder refuses another host's story). A copy is the whole save folder (the same layout as Savage
Wind's: save_0/<slot>/game_data.sav ...). The source is only read (its lock is held, never made), and checked byte
for byte after the copy. Nothing is copied while a game uses the source. Because a co-op folder always starts as a
copy of a save, the game opens at Continue, never at the intro.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from . import story

_ID_RE = re.compile(r"[^A-Za-z0-9_-]+")


def world_id(name: str) -> str:
    """A folder-safe id for a world name ("Ana's world" -> "Anas_world")."""
    wid = _ID_RE.sub("_", name.replace("'", "")).strip("_")[:40]
    return wid or "world"


def _digest(folder: Path) -> dict[str, str]:
    out = {}
    for p in sorted(Path(folder).rglob("*")):
        if p.is_file():
            out[p.relative_to(folder).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def has_save(folder: Path) -> bool:
    return bool(story.slot_files(folder))


def _inside(a: Path, b: Path) -> bool:
    a, b = Path(a).resolve(), Path(b).resolve()
    return a == b or b in a.parents


def copy_save(src: Path, dest: Path, *, protect=(), processes=None, check: str | None = None) -> Path:
    """Copy a save folder to a new co-op folder. Refuses: no save in src, dest already there, dest inside src (or
    src inside dest), dest inside a protected folder (your own save folder), or a game using the source (its lock is
    held during the copy, never made). The source is checked byte for byte afterwards. `check`: the host's check
    words, kept in the copy's mark."""
    src, dest = Path(src), Path(dest)
    if not has_save(src):
        raise FileNotFoundError(f"no save in {src}")
    if dest.exists():
        raise FileExistsError(f"{dest} is already there")
    if _inside(dest, src) or _inside(src, dest):
        raise ValueError("the co-op folder can't be inside the save it copies (or hold it)")
    for p in protect:
        if _inside(dest, p):
            raise ValueError(f"{dest} is inside {p}: co-op never writes there")
    with story.hold(src, processes, create=False):
        before = _digest(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".copying")
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(src, tmp)
        if _digest(src) != before or _digest(tmp) != before:
            shutil.rmtree(tmp, ignore_errors=True)
            raise OSError("the copy doesn't match its source; nothing was made")
    story.mark_coop_copy(tmp, str(src), check=check)
    tmp.rename(dest)
    return dest


def host_world(src: Path, library: Path, name: str, **kw) -> Path:
    """The host's world folder for `name`: made from `src` the first time, then reused."""
    dest = Path(library) / "worlds" / world_id(name)
    return dest if has_save(dest) else copy_save(src, dest, **kw)


def joined_name(world: str, check: str | None = None) -> str:
    """A friend's copy folder name: the world id, plus the host's check words when known ("Anas_world-apple_river_
    stone_moon"), so two hosts with the same world name never share a copy."""
    return world_id(world) + (f"-{world_id(check)}" if check else "")


def joined_copy(src: Path, library: Path, world: str, check: str | None = None, **kw) -> Path:
    """A friend's co-op copy for a host's world: made from his own save the first time, then reused."""
    dest = Path(library) / "joined" / joined_name(world, check)
    return dest if has_save(dest) else copy_save(src, dest, check=check, **kw)
