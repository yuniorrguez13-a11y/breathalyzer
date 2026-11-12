"""Finding hosts on the home network (docs/coop.md): no typing, no servers.

Join sends a 512-byte query by UDP broadcast to port 7788 each second:
    {"t":"brz?","v":1}  padded with spaces to QUERY_SIZE bytes
A host answers (only addresses on a home network, at most once a second each, never more bytes than it got):
    {"t":"brz","v":1,"name":"Ana","world":"Ana's world","players":2,"max":8,"code":true,"port":7788,
     "ver":"0.7.0","check":"apple river stone moon"}
The room code itself is never in the answer (only whether one is needed). Answers over QUERY_SIZE bytes, bad names
and more than MAX_HOSTS hosts are dropped; a host is gone after GONE_AFTER seconds without an answer.
"""
from __future__ import annotations

import json
import re
import socket
import time

from . import DEFAULT_RELAY_PORT, valid_name
from .security import WORDS, address_allowed

DISCOVERY_PORT = DEFAULT_RELAY_PORT   # (UDP; the relay is TCP on the same number)
QUERY_SIZE = 512
MAX_HOSTS = 16
GONE_AFTER = 6.0
_WORLD_RE = re.compile(r"[\w .'-]{1,32}")
_VER_RE = re.compile(r"[\w.+-]{1,24}")


def make_query() -> bytes:
    q = json.dumps({"t": "brz?", "v": 1}, separators=(",", ":")).encode()
    return q + b" " * (QUERY_SIZE - len(q))


def is_query(data: bytes) -> bool:
    if len(data) != QUERY_SIZE:
        return False
    try:
        msg = json.loads(data.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        return False
    return isinstance(msg, dict) and msg.get("t") == "brz?" and msg.get("v") == 1


def make_answer(info: dict, limit: int = QUERY_SIZE) -> bytes | None:
    """The answer for this host, or None if it would be longer than `limit` (the query's size)."""
    msg = {"t": "brz", "v": 1, "name": info["name"], "world": info.get("world") or f"{info['name']}'s world",
           "players": int(info.get("players", 1)), "max": int(info.get("max", 8)), "code": bool(info.get("code")),
           "port": int(info.get("port", DEFAULT_RELAY_PORT)), "ver": str(info.get("ver", "0")),
           "check": str(info.get("check", ""))}
    data = json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return data if len(data) <= min(limit, QUERY_SIZE) else None


def parse_answer(data: bytes) -> dict | None:
    """A host's answer, checked, or None."""
    if len(data) > QUERY_SIZE:
        return None
    try:
        msg = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(msg, dict) or msg.get("t") != "brz" or msg.get("v") != 1:
        return None
    name = valid_name(msg.get("name"))
    world = msg.get("world")
    if name is None or not (isinstance(world, str) and _WORLD_RE.fullmatch(world)):
        return None
    out = {"name": name, "world": world}
    for key, lo, hi in (("players", 0, 64), ("max", 1, 64), ("port", 1, 65535)):
        v = msg.get(key)
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            return None
        out[key] = v
    if not isinstance(msg.get("code"), bool):
        return None
    out["code"] = msg["code"]
    ver = msg.get("ver")
    out["ver"] = ver if isinstance(ver, str) and _VER_RE.fullmatch(ver) else "?"
    check = msg.get("check")
    words = check.split() if isinstance(check, str) else []
    out["check"] = check if len(words) == 4 and all(w in WORDS for w in words) else ""
    return out


class HostTable:
    """Hosts found so far: at most MAX_HOSTS, each gone GONE_AFTER seconds after its last answer."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.hosts: dict[tuple[str, int], tuple[float, dict]] = {}

    def add(self, address: str, info: dict) -> bool:
        if not address_allowed(address):
            return False
        key = (address, info["port"])
        if key not in self.hosts and len(self.hosts) >= MAX_HOSTS:
            self.expire()
            if len(self.hosts) >= MAX_HOSTS:
                return False
        self.hosts[key] = (self.clock(), dict(info, address=address))
        return True

    def expire(self) -> None:
        now = self.clock()
        for k in [k for k, (t, _) in self.hosts.items() if now - t > GONE_AFTER]:
            del self.hosts[k]

    def list(self) -> list[dict]:
        self.expire()
        return sorted((i for _, i in self.hosts.values()), key=lambda i: (i["world"].lower(), i["address"]))


class Responder:
    """The host's side: answers queries on a UDP socket. `info()` gives the current answer fields."""

    def __init__(self, info, clock=time.monotonic):
        self.info = info
        self.clock = clock
        self.last: dict[str, float] = {}

    def answer_for(self, data: bytes, address: str) -> bytes | None:
        """The bytes to send back for one datagram from `address`, or None (not a query, not a home address,
        answered less than a second ago, or the answer wouldn't fit)."""
        if not is_query(data) or not address_allowed(address):
            return None
        now = self.clock()
        if now - self.last.get(address, -1e9) < 1.0:
            return None
        if len(self.last) > 256:
            self.last = {a: t for a, t in self.last.items() if now - t < 1.0}
        self.last[address] = now
        return make_answer(self.info(), len(data))

    def serve(self, sock: socket.socket, stop) -> None:
        """Answer until stop() is true (sock should have a short timeout)."""
        while not stop():
            try:
                data, (addr, port, *_) = sock.recvfrom(QUERY_SIZE + 1)
            except (socket.timeout, BlockingIOError):
                continue
            except OSError:
                return
            out = self.answer_for(data, addr)
            if out is not None:
                try:
                    sock.sendto(out, (addr, port))
                except OSError:
                    pass


def open_responder_socket(port: int = DISCOVERY_PORT, host: str = "") -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.settimeout(0.25)
    return s


def search(seconds: float = 2.0, port: int = DISCOVERY_PORT, targets=("255.255.255.255",),
           table: HostTable | None = None) -> list[dict]:
    """Broadcast queries each second for `seconds` and collect answers. `targets`: where to send (a unicast address
    works too: the tests use 127.0.0.1)."""
    table = table or HostTable()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind(("", 0))
        s.settimeout(0.2)
        end = time.monotonic() + seconds
        next_query = 0.0
        while time.monotonic() < end:
            if time.monotonic() >= next_query:
                for t in targets:
                    try:
                        s.sendto(make_query(), (t, port))
                    except OSError:
                        pass
                next_query = time.monotonic() + 1.0
            try:
                data, (addr, *_) = s.recvfrom(QUERY_SIZE + 1)
            except (socket.timeout, BlockingIOError):
                continue
            except OSError:
                break
            info = parse_answer(data)
            if info is not None:
                table.add(addr, info)
    finally:
        s.close()
    return table.list()
