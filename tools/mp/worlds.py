"""Co-op save folders (docs/coop.md): co-op always plays on COPIES, so nobody's own save is ever written.

    <mp library>/worlds/<world-id>/     a host's world (made once from a copy of the host's newest save)
    <mp library>/joined/<world-id>/     a friend's copy for that world (made once from his own save)
    <mp library>/mp/backups/<folder>/   story backups (tools/mp/story.py)

A copy is the whole save folder (the same layout as Savage Wind's: save_0/<slot>/game_data.sav ...). The source is
only read, and checked byte for byte after the copy. Nothing is copied while a game uses the source or the target.
Because a co-op folder always starts as a copy of a save, the game opens at Continue, never at the intro.
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


def copy_save(src: Path, dest: Path, *, protect=(), processes=None) -> Path:
    """Copy a save folder to a new co-op folder. Refuses: no save in src, dest already there, dest inside src (or
    src inside dest), dest inside a protected folder (your own save folder), or a game using either folder. The
    source is checked byte for byte afterwards."""
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
    why = story.game_running(src, processes)
    if why:
        raise OSError(why)
    before = _digest(src)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".copying")
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(src, tmp)
    if _digest(src) != before or _digest(tmp) != before:
        shutil.rmtree(tmp, ignore_errors=True)
        raise OSError("the copy doesn't match its source; nothing was made")
    story.mark_coop_copy(tmp, str(src))
    tmp.rename(dest)
    return dest


def host_world(src: Path, library: Path, name: str, **kw) -> Path:
    """The host's world folder for `name`: made from `src` the first time, then reused."""
    dest = Path(library) / "worlds" / world_id(name)
    return dest if has_save(dest) else copy_save(src, dest, **kw)


def joined_copy(src: Path, library: Path, world: str, **kw) -> Path:
    """A friend's co-op copy for a host's world: made from his own save the first time, then reused."""
    dest = Path(library) / "joined" / world_id(world)
    return dest if has_save(dest) else copy_save(src, dest, **kw)
