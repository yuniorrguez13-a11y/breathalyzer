"""The shared session layer (docs/coop.md): one hello for co-op and story, by features, so older clients still work.

    challenge {"type":"challenge","nonce":"<32 hex>"}   the relay's first line when the world has a room code
    hello    {"type":"hello","version":1,"name":"Ben","room":"default",
              "features":["coop1","story1"], "caps":{"ghost":"0.7.0","game":"1.9.0","sw":"0.4.1-alpha.4","dlc":0},
              "story":{"v":1,"base":"9f3a10c2","dlc":null}, "cnonce":"<32 hex>", "code_proof":"<32 hex>",
              "key":"<16 hex>", "host_proof":"<32 hex>" (only the host's own client, over 127.0.0.1)}
              (an older client sends "code":"apple river stone" instead of cnonce + code_proof)
    welcome  adds "features" (the relay's), "host" (the host's id or null), "host_key" (his key fingerprint),
             "rules", "code" (a code is needed), "code_ok" (the relay's proof that it has the code), each peer's
             "features", and for a late joiner the host's last "time", "weather" and "story"

Protocol version stays 1: new things are features. A client without "features" is a visitor (sees ghosts; never gets
time or story). A relay without "features" in its welcome is an older one: ghosts only.

New message kinds (all host-only except story_offer; the relay writes "from", "id" and "name" itself):
    time        {"type":"time","h":21.5}                          hour of day, 0 <= h < 24
    weather     {"type":"weather","w":"<hex>"}                    the climate flags (probe A decides if it's used)
    story       {"type":"story","seq":7,"list":"9f3a10c2","bits":"<hex>","dlc":false}
    story_offer {"type":"story_offer","list":..,"bits":..}        a friend's new bits; only if the host allows it
    rules       {"type":"rules","friends_story":false}
    kick / ban  {"type":"kick","id":4}
    end         {"type":"end"}
"""
from __future__ import annotations

import re

from . import encode, finite

FEATURES = ("coop1", "story1")                  # what this Breathalyzer speaks
KNOWN_FEATURES = frozenset({"coop1", "story1", "look", "mods1"})
FEATURE_OF = {"time": "coop1", "weather": "coop1", "story": "story1", "story_offer": "story1", "rules": "coop1"}
MAX_HELLO = 1024
MAX_STORY_HEX = 512        # 1,075 flags today = 270 characters
MAX_WEATHER_HEX = 512

_FEATURE_RE = re.compile(r"[a-z0-9]{1,16}")
_HASH_RE = re.compile(r"[0-9a-f]{8}")
_HEX_RE = re.compile(r"(?:[0-9a-f]{2})+")
_VER_RE = re.compile(r"[\w.+-]{1,24}")


def clean_features(v) -> list[str] | None:
    """The known features in a hello's list (unknown ones are ignored), or None if there is no list (a visitor)."""
    if not isinstance(v, list):
        return None
    out = []
    for f in v[:16]:
        if isinstance(f, str) and _FEATURE_RE.fullmatch(f) and f in KNOWN_FEATURES and f not in out:
            out.append(f)
    return out


def clean_caps(v) -> dict:
    out = {}
    if isinstance(v, dict):
        for k in ("ghost", "game", "sw"):
            if isinstance(v.get(k), str) and _VER_RE.fullmatch(v[k]):
                out[k] = v[k]
        if isinstance(v.get("dlc"), int) and not isinstance(v.get("dlc"), bool) and 0 <= v["dlc"] <= 9:
            out["dlc"] = v["dlc"]
    return out


def clean_story_hello(v) -> dict | None:
    if not isinstance(v, dict) or v.get("v") != 1 or not isinstance(v.get("base"), str):
        return None
    if not _HASH_RE.fullmatch(v["base"]):
        return None
    dlc = v.get("dlc")
    return {"v": 1, "base": v["base"], "dlc": dlc if isinstance(dlc, str) and _HASH_RE.fullmatch(dlc) else None}


def hello(name: str, room: str, *, features=FEATURES, caps: dict | None = None, story: dict | None = None,
          code: str | None = None, key: str | None = None, host_proof: str | None = None, bot: bool = False,
          extra: dict | None = None) -> dict:
    """A hello. `code`: the room code as text (older relays only: new clients send `extra` = security.code_hello()'s
    proof instead, so the code never goes on the wire)."""
    msg = {"type": "hello", "version": 1, "name": name, "room": room, "features": list(features)}
    if extra:
        msg.update(extra)
    if caps:
        msg["caps"] = caps
    if story:
        msg["story"] = story
    if code:
        msg["code"] = code
    if key:
        msg["key"] = key
    if host_proof:
        msg["host_proof"] = host_proof
    if bot:
        msg["bot"] = True
    if len(encode(msg)) > MAX_HELLO:
        raise ValueError("hello too long")
    return msg


# -- the new kinds, checked (None = drop it) --

def clean_time(msg: dict) -> dict | None:
    h = finite(msg.get("h"))
    return {"h": round(h, 3)} if h is not None and 0.0 <= h < 24.0 else None


def clean_weather(msg: dict) -> dict | None:
    w = msg.get("w")
    if isinstance(w, str) and len(w) <= MAX_WEATHER_HEX and _HEX_RE.fullmatch(w):
        return {"w": w}
    return None


def clean_story(msg: dict, offer: bool = False) -> dict | None:
    """A story line: list hash (8 hex), bits (hex, even length, at most MAX_STORY_HEX). The receiver checks again
    against its own list (exact length, only its own names, only 0 -> 1)."""
    lst, bits = msg.get("list"), msg.get("bits")
    if not (isinstance(lst, str) and _HASH_RE.fullmatch(lst)):
        return None
    if not (isinstance(bits, str) and len(bits) <= MAX_STORY_HEX and _HEX_RE.fullmatch(bits)):
        return None
    out = {"list": lst, "bits": bits, "dlc": msg.get("dlc") is True}
    if not offer:
        seq = msg.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq < 2 ** 31:
            return None
        out["seq"] = seq
    return out


def clean_rules(msg: dict) -> dict | None:
    fs = msg.get("friends_story")
    return {"friends_story": fs} if isinstance(fs, bool) else None


def clean_target(msg: dict) -> int | None:
    t = msg.get("id")
    return t if isinstance(t, int) and not isinstance(t, bool) and t > 0 else None


def older_relay(welcome: dict) -> bool:
    """A welcome without "features": an older relay. Ghosts work; nothing else is shared."""
    return not isinstance(welcome.get("features"), list)
