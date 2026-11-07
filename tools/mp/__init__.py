"""Multiplayer proof of concept ("ghost Link"): share positions between players through a small relay.

    relay.py   the server: python tools/mp/relay.py [--port 7788]
    client.py  one per player, next to their own game: python tools/mp/client.py --relay HOST --name Ana
    bot.py     a fake player for solo tests: python tools/mp/bot.py --relay HOST
    ghost_link_pack.py     the ghost's Link body, built from your own game files (docs/ghost.md)
    ghost_ingame_check.py  the ghost's in-game checks (starts the game; docs/ghost.md)

Everything here is the wire protocol shared by the three: JSON objects, one per line (UTF-8, '\\n'), at most
MAX_LINE bytes per line.

    client -> relay  {"type":"hello","name":"Ana","room":"default","version":1}        first line, once
                     {"type":"state","t":12.5,"x":..,"y":..,"z":..,"yaw":..,"stamina":..,"place":"..","speed":..}
                     {"type":"ping","t":12.5}   {"type":"bye"}
    relay -> client  {"type":"welcome","id":3,"name":..,"room":..,"version":1,"peers":[{"id","name","bot","state"?}]}
                     {"type":"join","id","name","bot"}   {"type":"leave","id","name"}
                     {"type":"state","id","name",...the sender's state...}
                     {"type":"pong","t":12.5}   {"type":"error","error":".."}   {"type":"bye","reason":".."}

`t` is the sender's own clock in seconds (only differences matter). x y z are game units (metres), yaw is the
camera's yaw in degrees (0 looks along +z, 90 along +x), as the game's `camera` command reports it.
`speed` (optional, added 2026-11-07, same protocol version) is the sender's speed on the ground in m/s, measured by
the sender from its own positions over the last 0.4 s (client.SpeedMeter), so network jitter can't change it. Older
clients don't send it and older relays drop it; a receiver without it works the speed out from the samples' own `t`.

Also optional, same protocol version, sent only with client.py --ghost-sync (or --ghost-link) and the ghost mod 0.5.0
(its `ghost self`), not yet tested in the game:
    "anim":"CutS1"    the animation the sender's Link plays (his body's; an attack is kept at least 0.3 s, so a
                      20 Hz sender doesn't miss it): letters, digits and _, 1-31 characters
    "attack":true     anim is an attack (left out otherwise)
    "weapon":"Weapon_Sword_001", "shield":"...", "bow":"..."   the actor names of what he has equipped ("" none):
                      letters, digits and _, up to 63 characters. With --ghost-items (ghost mod 0.6.0) the receiver's
                      ghost holds them; else they are only logged.
    "on_back":4       (0.6.0, optional) which of them are on his back, not in his hands: bit 0 weapon, 1 shield,
                      2 bow (an int from 0 to 7; anything else is dropped). A receiver without it (an older sender)
                      puts the bow on the back, or weapon and shield while the sender's anim is a Bow... name.
A bad one is dropped on its own, never the state. Older relays drop them all; older clients ignore them.
"""
from __future__ import annotations

import json
import math
import re
import sys

PROTOCOL_VERSION = 1
DEFAULT_RELAY_PORT = 7788
DEFAULT_ROOM = "default"
MAX_LINE = 4096          # bytes per line, without the newline
MAX_COORD = 1.0e6        # |x|, |y|, |z| above this is not a place in the game
MAX_PLACE = 160          # the game's place line is at most 159 characters
MAX_SPEED = 1000.0       # m/s: a `speed` above this (or below 0) is dropped
ITEM_KEYS = ("weapon", "shield", "bow")   # optional item names (--ghost-sync)
MAX_ON_BACK = 7                           # "on_back": bit 0 weapon, 1 shield, 2 bow (--ghost-items, ghost mod 0.6.0)

_NAME_RE = re.compile(r"\w[\w .'-]{0,19}")
_ANIM_RE = re.compile(r"[A-Za-z0-9_]{1,31}")
_ITEM_RE = re.compile(r"[A-Za-z0-9_]{0,63}")
_ROOM_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")


def valid_name(name) -> str | None:
    """The player name to use, or None if it isn't allowed (1-20 letters, digits, space, _ . ' -)."""
    if not isinstance(name, str):
        return None
    name = name.strip()
    return name if _NAME_RE.fullmatch(name) and name.isprintable() else None


def valid_room(room) -> str | None:
    """The room name to use, or None (1-32 of A-Z a-z 0-9 _ -)."""
    if not isinstance(room, str):
        return None
    return room if _ROOM_RE.fullmatch(room) else None


def safe_name(text: str, fallback: str = "player") -> str:
    """Turn any text (say a Windows user name) into an allowed player name."""
    text = re.sub(r"[^\w .'-]", "", str(text)).strip()[:20].strip()
    return valid_name(text) or fallback


def _number(v, limit: float | None = None) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    if not math.isfinite(v) or (limit is not None and abs(v) > limit):
        return None
    return v


def clean_state(msg: dict) -> dict | None:
    """Keep only the known state fields, checked. None if the state is unusable (no finite t, x, y, z).

    `speed` is optional and newer than the rest: a bad one (not a number from 0 to MAX_SPEED) is dropped, not the
    whole state, and a state without it has no "speed" key. The same goes for `anim`, `attack` (only true is kept),
    `weapon`, `shield`, `bow` and `on_back` (an int from 0 to 7).
    """
    if not isinstance(msg, dict):
        return None
    out = {}
    for key, limit in (("t", None), ("x", MAX_COORD), ("y", MAX_COORD), ("z", MAX_COORD)):
        v = _number(msg.get(key), limit)
        if v is None:
            return None
        out[key] = v
    for key in ("yaw", "stamina"):
        v = msg.get(key)
        out[key] = None if v is None else _number(v, MAX_COORD)
        if v is not None and out[key] is None:
            return None
    place = msg.get("place")
    if isinstance(place, str) and place.isprintable():
        out["place"] = place[:MAX_PLACE]
    speed = _number(msg.get("speed"), MAX_SPEED)
    if speed is not None and speed >= 0:
        out["speed"] = speed
    anim = msg.get("anim")
    if isinstance(anim, str) and _ANIM_RE.fullmatch(anim):
        out["anim"] = anim
    if msg.get("attack") is True:
        out["attack"] = True
    for key in ITEM_KEYS:
        item = msg.get(key)
        if isinstance(item, str) and _ITEM_RE.fullmatch(item):
            out[key] = item
    on_back = valid_on_back(msg.get("on_back"))
    if on_back is not None:
        out["on_back"] = on_back
    return out


def valid_on_back(v) -> int | None:
    """The state's `on_back` mask (an int from 0 to MAX_ON_BACK, not a bool), or None."""
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= MAX_ON_BACK else None


def valid_anim(name) -> str | None:
    """An animation name as the state's `anim` and the ghost mod take it (letters, digits, _; 1-31), or None."""
    return name if isinstance(name, str) and _ANIM_RE.fullmatch(name) else None


def encode(msg: dict) -> bytes:
    """One protocol line. Raises ValueError for NaN or infinity."""
    return (json.dumps(msg, separators=(",", ":"), allow_nan=False, ensure_ascii=False) + "\n").encode("utf-8")


def decode(line: bytes) -> dict | None:
    """The message on one line, or None if it isn't a JSON object with a string "type"."""
    try:
        msg = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):   # (RecursionError: very deeply nested JSON)
        return None
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        return None
    return msg


def finite(v) -> float | None:
    """v as a float if it is a finite number (not a bool), else None."""
    return _number(v)


def safe_console(*streams) -> None:
    """Make printing never fail on a character the console can't show (it prints \\uXXXX instead).

    Names may hold any letter, but when output goes to a file or a pipe Windows Python writes cp1252, and
    print('Łukasz') would raise UnicodeEncodeError. Default: sys.stdout and sys.stderr.
    """
    for s in streams or (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError, OSError):   # (not a text stream, or already closed)
            pass


def parse_hostport(text: str, default_port: int = DEFAULT_RELAY_PORT) -> tuple[str, int]:
    """'host', 'host:port' or '[v6]:port' -> (host, port)."""
    text = text.strip()
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    elif text.count(":") == 1:
        host, port = text.split(":")
    else:
        host, port = text, ""
    if not host:
        raise ValueError(f"no host in {text!r}")
    if port:
        if not port.isdigit() or not 0 < int(port) < 65536:
            raise ValueError(f"bad port in {text!r}")
        return host, int(port)
    return host, default_port
