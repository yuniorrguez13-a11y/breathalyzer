"""Base security for player-hosted sessions (docs/coop.md): the host alone controls the shared world.

    room codes      3 words from WORDS (256 simple words), shown big, picked with word wheels; never in discovery
    host secret     <library>/mp/host.json: 32 random bytes that never leave the PC; the host's own client proves it
                    over 127.0.0.1, and only that connection is the host
    player key      <library>/mp/player.json: 128 random bits per install; only a fingerprint is ever sent
    bans            <library>/mp/bans.json (key fingerprint + address); kicks keep a player out for 10 minutes
    limits          per message kind (KIND_RATES), wrong room codes (5 a minute from one address = 10 minutes out),
                    host-only messages from anyone else (3 in 10 s = kicked)

There are no project servers: everything here runs on the players' own PCs.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import time
from pathlib import Path

# Simple words, easy to read aloud and to tell apart. Index = one byte (check words and room codes use it).
WORDS = (
    "apple arrow autumn badge baker bamboo banjo barn basket beach bean bear bell berry bird blanket "
    "boat bone book boot bottle bread brick bridge brook brush bucket bush butter cabin cactus cake "
    "camel candle canoe canyon cape card carrot castle cat cave cedar chair chalk cheese cherry chess "
    "cliff clock cloud clover coast coat comet coral corn cotton crab crane creek crown cup daisy "
    "deer desert dove dragon drum duck eagle earth echo egg elk ember falcon feather fern field "
    "fig fire fish flag flame flute fog forest fox frog frost garden gate gem giant glove "
    "goat gold grape grass guitar hammer harbor hat hawk hazel heart hill honey horn horse house "
    "ice iris island ivy jade jar jelly kettle key kite koala lake lamp lantern leaf lemon "
    "lily lime lion lizard lotus maple marble meadow melon mint mirror moon moss moth mountain mouse "
    "mule nest night nut oak ocean olive onion orange otter owl paddle palm panda paper peach "
    "pearl pebble pepper piano pine plum pond pony potato pumpkin quail quartz rabbit rain raven reed "
    "rice ring river robin rock rose ruby sail salt sand seal seed shell ship silver sky "
    "sled snail snow sock spark spoon spring star stone storm straw stream sugar summer sun swan "
    "sword table tea thorn tiger toast tomato tower train tree trout tulip turtle valley violet wagon "
    "walnut water wave whale wheat wind window winter wolf wood yarn zebra acorn anchor apron bench "
    "bison cloak comb dune fable glade jewel lark mango mitten nickel opal parrot pansy quilt saddle"
).split()
_WORD_INDEX = {w: i for i, w in enumerate(WORDS)}
CODE_WORDS = 3

# Messages only the host may send. From anyone else the relay drops them; HOST_ONLY_STRIKES in HOST_ONLY_WINDOW
# seconds gets that player kicked.
HOST_ONLY = frozenset({"time", "weather", "story", "rules", "end", "kick", "ban", "code"})
HOST_ONLY_STRIKES = 3
HOST_ONLY_WINDOW = 10.0

# Messages a second per kind and player (bursts up to the same number). Kinds not listed only count against the
# relay's overall limit (30 a second).
KIND_RATES = {"state": 25.0, "story": 2.0, "time": 2.0, "weather": 1.0, "story_offer": 1.0}

WRONG_CODE_TRIES = 5        # wrong room codes from one address in WRONG_CODE_WINDOW seconds ...
WRONG_CODE_WINDOW = 60.0
WRONG_CODE_WAIT = 600.0     # ... make that address wait this long
KICK_WAIT = 600.0           # a kicked player (key and address) can't come back for this long
MAX_PENDING_PER_ADDRESS = 4 # connections from one address that haven't said hello yet

_HEX_RE = re.compile(r"[0-9a-f]+")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")   # virtual-LAN tools (Tailscale, ZeroTier ...) use it


# -- room codes --

def make_code(rng=secrets) -> str:
    """A new room code: CODE_WORDS words from WORDS, joined by spaces ("apple river stone")."""
    return " ".join(rng.choice(WORDS) for _ in range(CODE_WORDS))


def normalize_code(text) -> str | None:
    """A room code as typed or picked ("Apple  River-stone") in its one form ("apple river stone"), or None if it
    isn't CODE_WORDS words from the list."""
    if not isinstance(text, str) or len(text) > 64:
        return None
    words = re.split(r"[\s,.-]+", text.strip().lower())
    if len(words) != CODE_WORDS or any(w not in _WORD_INDEX for w in words):
        return None
    return " ".join(words)


def code_matches(expected: str | None, given) -> bool:
    """True if `given` opens a room whose code is `expected` (no code set: always True). Constant time."""
    if not expected:
        return True
    norm = normalize_code(given) if isinstance(given, str) else None
    return hmac.compare_digest((norm or "").encode(), normalize_code(expected).encode())


# -- the host secret and player keys --

def _load_or_make(path: Path, field: str, nbytes: int) -> str:
    """The hex value `field` in the JSON file at `path`, made (and the file written) the first time."""
    path = Path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get(field)
        if isinstance(value, str) and len(value) == 2 * nbytes and _HEX_RE.fullmatch(value):
            return value
    except (OSError, ValueError, AttributeError):
        pass
    value = secrets.token_hex(nbytes)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({field: value}) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return value


def host_secret(library: Path) -> str:
    """This PC's host secret (64 hex characters) from <library>/mp/host.json. Never sent anywhere."""
    return _load_or_make(Path(library) / "mp" / "host.json", "secret", 32)


def host_proof(secret: str, room: str) -> str:
    """What the host's own client sends in its hello: an HMAC of the room name with the host secret."""
    return hmac.new(bytes.fromhex(secret), ("breathalyzer-host:" + room).encode(), hashlib.sha256).hexdigest()[:32]


def host_proof_matches(secret: str | None, room: str, given) -> bool:
    if not secret or not isinstance(given, str):
        return False
    return hmac.compare_digest(given.encode(), host_proof(secret, room).encode())


def player_key(library: Path) -> str:
    """This install's player key (32 hex characters, 128 bits) from <library>/mp/player.json. Never sent."""
    return _load_or_make(Path(library) / "mp" / "player.json", "key", 16)


def fingerprint(key: str) -> str:
    """The 16-hex-character fingerprint of a player key: the only part that is sent (hello "key")."""
    return hashlib.sha256(("breathalyzer-player:" + key).encode()).hexdigest()[:16]


def valid_fingerprint(v) -> str | None:
    return v if isinstance(v, str) and len(v) == 16 and _HEX_RE.fullmatch(v) else None


def check_words(fp: str, n: int = 4) -> str:
    """Words to compare by eye (the Join list and the host's screen show the same ones): a fake host can't match
    them without the host's key."""
    digest = hashlib.sha256(("breathalyzer-check:" + fp).encode()).digest()
    return " ".join(WORDS[b] for b in digest[:n])


# -- who may connect --

def address_allowed(ip) -> bool:
    """True for addresses on a home network (private, link-local, loopback) or a virtual-LAN tool (100.64.0.0/10).
    The beta is home-network play only; anything else waits for internet play."""
    try:
        addr = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    if addr.is_loopback or addr.is_link_local or addr.is_private:
        return not addr.is_unspecified and not addr.is_multicast
    return addr.version == 4 and addr in _CGNAT


class BanList:
    """Bans (kept in a JSON file) and kicks (10 minutes, in memory). A player is out if his key fingerprint or his
    address matches. Said plainly in the docs: someone on your network can make a new key, so use a room code."""

    def __init__(self, path: Path | None = None, clock=time.monotonic):
        self.path = Path(path) if path else None
        self.clock = clock
        self.bans: list[dict] = []
        self.kicks: list[tuple[float, str | None, str | None]] = []   # (until, key, address)
        if self.path is not None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.bans = [b for b in data.get("bans", []) if isinstance(b, dict)]
            except (OSError, ValueError, AttributeError):
                self.bans = []

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"bans": self.bans}, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def ban(self, key: str | None, address: str | None, name: str = "") -> None:
        self.bans.append({"key": key, "addr": address, "name": name[:20], "at": int(time.time())})
        self._save()

    def unban(self, what: str) -> int:
        """Remove every ban whose key, address or name is `what`. Returns how many went."""
        before = len(self.bans)
        self.bans = [b for b in self.bans if what not in (b.get("key"), b.get("addr"), b.get("name"))]
        if len(self.bans) != before:
            self._save()
        return before - len(self.bans)

    def kick(self, key: str | None, address: str | None) -> None:
        self.kicks.append((self.clock() + KICK_WAIT, key, address))

    def blocked(self, key: str | None, address: str | None) -> str | None:
        """Why this player can't join ("banned", "kicked"), or None."""
        for b in self.bans:
            if (key and b.get("key") == key) or (address and b.get("addr") == address):
                return "banned"
        now = self.clock()
        self.kicks = [k for k in self.kicks if k[0] > now]
        for _, k, a in self.kicks:
            if (key and k == key) or (address and a == address):
                return "kicked"
        return None


class KindLimiter:
    """Token buckets per message kind (KIND_RATES) for one player."""

    def __init__(self, rates: dict | None = None, clock=time.monotonic):
        self.rates = dict(KIND_RATES if rates is None else rates)
        self.clock = clock
        self.buckets: dict[str, list[float]] = {}

    def allow(self, kind: str) -> bool:
        rate = self.rates.get(kind)
        if rate is None:
            return True
        now = self.clock()
        b = self.buckets.setdefault(kind, [rate, now])
        b[0] = min(rate, b[0] + max(0.0, now - b[1]) * rate)
        b[1] = now
        if b[0] >= 1.0:
            b[0] -= 1.0
            return True
        return False


class Strikes:
    """Counts events per key in a sliding window: `hit(key)` is True once there were `limit` in `window` seconds."""

    def __init__(self, limit: int, window: float, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self.times: dict[object, list[float]] = {}

    def hit(self, key) -> bool:
        now = self.clock()
        t = [x for x in self.times.get(key, []) if now - x < self.window]
        t.append(now)
        self.times[key] = t
        return len(t) >= self.limit

    def clear(self, key) -> None:
        self.times.pop(key, None)


class WrongCodeGuard:
    """WRONG_CODE_TRIES wrong room codes from one address in WRONG_CODE_WINDOW seconds = it waits WRONG_CODE_WAIT."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.strikes = Strikes(WRONG_CODE_TRIES, WRONG_CODE_WINDOW, clock)
        self.until: dict[str, float] = {}

    def blocked(self, address: str) -> bool:
        return self.until.get(address, 0.0) > self.clock()

    def wrong(self, address: str) -> None:
        if self.strikes.hit(address):
            self.until[address] = self.clock() + WRONG_CODE_WAIT
            self.strikes.clear(address)
