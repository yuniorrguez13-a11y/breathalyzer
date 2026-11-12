#!/usr/bin/env python3
"""Relay for the multiplayer proof of concept: passes each player's position to the others in the same room.

    python tools/mp/relay.py                       listen on 0.0.0.0:7788
    python tools/mp/relay.py --port 9000 --room-cap 4

JSON lines over TCP (see tools/mp/__init__.py). A state is passed on with its known fields only (mp.clean_state:
t x y z yaw stamina place, and the optional speed, anim, attack, weapon, shield and bow; a bad optional field is
dropped on its own). Nothing is saved; Ctrl+C stops it and tells every client.
Limits per client: 10 s to say hello, 30 messages a second (more are dropped; much more gets the client cut off), 4 KB per line,
8 players per room. Names and rooms are checked.

Co-op and base security (docs/coop.md; tools/mp/session.py, security.py), used when the host's agent runs the relay:
- `host_secret`: only the connection on 127.0.0.1 whose hello carries the matching `host_proof` is the room's host
  (checked before the ban list). time, weather, story, rules, kick, ban, end and code are host-only: from anyone else
  they are dropped, and 3 in 10 s get that player cut off and kicked. Every relayed co-op line carries "from" (the
  sender's id), written by the relay.
- `room_code`: 3 words. With a code set, every new connection first gets {"type":"challenge","nonce":..}; the hello
  answers with "cnonce" and "code_proof" (security.code_proof: the code is never sent) and the welcome carries
  "code_ok" (security.code_answer) so the friend knows this relay has the code. A plain "code" (older clients) still
  works. A hello with no code is told "this world needs a room code" (no strike); wrong ones are refused, 5 a minute
  from one address make it wait 10 minutes.
- `bans` (security.BanList): banned or kicked keys and addresses can't join (never loopback by address). A kick or
  ban closes every connection of that player in every room (same key, same address, or on this PC the same name).
  `lan_only`: home-network addresses only.
- Per kind: state 25/s (new clients), story 2/s, time 2/s, weather 1/s, story_offer 1/s; 4 not-yet-hello connections
  per address. A room with a host (co-op) takes no states (ghosts use their own room). A friend's `story_offer`
  reaches the host only if the host's rules allow it (off by default). Cut off for flooding = kicked too.
- A client without "features" in its hello is a visitor: states, join and leave only, never time or story.
- Who am I (feature "look", docs/charsel.md): a hello's "look" (session.clean_look) is kept and shown to the others
  that speak "look" (welcome peers, join); a `look` message changes it, at most one every session.LOOK_EVERY s (3/4
  of it is enough here: jitter), and
  goes to those others as {"type":"look","id","name","look"}. Only an id, a version and a name: never game data.
Without host_secret and room_code it works as before: anyone who can reach the port can join.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mp import (DEFAULT_RELAY_PORT, MAX_LINE, PROTOCOL_VERSION, clean_state, decode, encode, finite,  # noqa: E402
                safe_console, valid_name, valid_room)
from mp import security, session  # noqa: E402

DEFAULT_RATE = 30          # messages per second per client
DEFAULT_ROOM_CAP = 8
DEFAULT_MAX_CLIENTS = 64
FLOOD_FACTOR = 5           # cut a client off after this many seconds' worth of dropped messages (decays at the rate)
MAX_BUFFER = 256 * 1024    # bytes waiting to go to one client before it counts as too slow


class _Conn:
    __slots__ = ("id", "name", "room", "bot", "writer", "peer", "tokens", "stamp", "flood", "warned",
                 "state", "state_at", "closing", "joined", "hello_by", "features", "host", "key", "kinds", "ip",
                 "nonce", "look", "look_at")

    def __init__(self, writer, rate, now, hello_by=float("inf")):
        self.writer = writer
        self.peer = writer.get_extra_info("peername")
        self.id = 0
        self.name = self.room = None
        self.bot = False
        self.tokens = float(rate)
        self.stamp = now
        self.flood = 0.0
        self.warned = -1e9
        self.state = None
        self.state_at = 0.0
        self.closing = False
        self.joined = False
        self.hello_by = hello_by   # (event loop time) the hello must have arrived by then
        self.features = None       # None: a visitor (an older client); else the known features of its hello
        self.host = False          # the room's host (proved with the host secret over 127.0.0.1)
        self.key = None            # player key fingerprint (bans)
        self.kinds = None          # security.KindLimiter (per message kind)
        self.ip = self.peer[0] if isinstance(self.peer, tuple) and self.peer else None
        self.nonce = security.new_nonce()   # (the room code challenge)
        self.look = None           # Who am I: the look it picked (session.clean_look), if it speaks "look"
        self.look_at = -1e9        # ... when it last changed it (one per session.LOOK_EVERY s)

    def has(self, feature: str) -> bool:
        return self.features is not None and feature in self.features


class Relay:
    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_RELAY_PORT, *, rate: float = DEFAULT_RATE,
                 max_line: int = MAX_LINE, room_cap: int = DEFAULT_ROOM_CAP, max_clients: int = DEFAULT_MAX_CLIENTS,
                 hello_timeout: float = 10.0, idle_timeout: float = 30.0, clock=time.monotonic, log=print,
                 host_secret: str | None = None, room_code: str | None = None, bans=None,
                 friends_story: bool = False, lan_only: bool = False):
        self.host, self.port = host, port
        self.rate = float(rate)
        self.max_line = max_line
        self.room_cap = room_cap
        self.max_clients = max_clients
        self.hello_timeout = hello_timeout
        self.idle_timeout = idle_timeout
        self.clock = clock
        self.log = log
        self.rooms: dict[str, dict[int, _Conn]] = {}
        self._conns: set[_Conn] = set()
        self._next_id = 1
        self._server = None
        self._done: asyncio.Event | None = None
        self._closed = False
        self.host_secret = host_secret
        self.room_code = security.normalize_code(room_code) if room_code else None
        if room_code and self.room_code is None:
            raise ValueError("a room code is 3 words from the word list")
        self.bans = bans if bans is not None else security.BanList(clock=clock)
        self.lan_only = lan_only
        self.default_rules = {"friends_story": bool(friends_story)}
        self.room_info: dict[str, dict] = {}   # room -> {"host": _Conn|None, "time", "weather", "story", "rules"}
        self._wrong_codes = security.WrongCodeGuard(clock)
        self._host_only = security.Strikes(security.HOST_ONLY_STRIKES, security.HOST_ONLY_WINDOW, clock)
        self.remote_joins = 0   # joins from another PC (the host's agent says when none came: a firewall hint)

    # -- life cycle --

    async def start(self) -> tuple[str, int]:
        """Start listening. Returns the address actually used (port 0 picks a free port)."""
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        addr = self._server.sockets[0].getsockname()
        self.port = addr[1]
        return addr[0], addr[1]

    async def serve_forever(self) -> None:
        """Serve until close() or cancellation (Ctrl+C), then close.

        (Not asyncio's Server.serve_forever: on cancel it waits for every client to hang up before anything else runs.)
        """
        if self._server is None:
            await self.start()
        self._done = asyncio.Event()
        try:
            if not self._closed:
                await self._done.wait()
        finally:
            await self.close()

    async def close(self, reason: str = "relay stopped") -> None:
        """Stop listening, say bye to every client and close their connections."""
        if self._closed:
            return
        self._closed = True
        if self._done is not None:
            self._done.set()
        if self._server is not None:
            self._server.close()
        for conn in list(self._conns):
            self._send(conn, {"type": "bye", "reason": reason})
            self._kick(conn)
        if self._server is not None:
            try:
                await asyncio.wait_for(self._server.wait_closed(), 2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                for conn in list(self._conns):   # (still not gone: drop them hard)
                    if conn.writer.transport is not None:
                        conn.writer.transport.abort()

    def _say(self, text: str) -> None:
        """Log one line. Never raises: a console that can't show a name must not drop that player."""
        try:
            self.log(text)
        except Exception:
            pass

    def player_count(self, room: str | None = None) -> int:
        if room is not None:
            return len(self.rooms.get(room, {}))
        return sum(len(r) for r in self.rooms.values())

    # -- sending --

    def _send(self, conn: _Conn, msg: dict) -> None:
        if conn.closing:
            return
        transport = conn.writer.transport
        if transport is None or transport.is_closing():
            return
        if transport.get_write_buffer_size() > MAX_BUFFER:
            self._say(f"{conn.name or conn.peer} is too slow; disconnecting")
            self._kick(conn)
            return
        conn.writer.write(encode(msg))

    def _fail(self, conn: _Conn, error: str) -> None:
        """Send an error and close the connection."""
        self._send(conn, {"type": "error", "error": error})
        self._kick(conn)

    def _cut_off(self, conn: _Conn, error: str) -> None:
        """Close a misbehaving client and keep it out for a while (a kick: never this PC by address), so it can't
        reconnect at once and do it again."""
        if conn.joined and not conn.host:
            self.bans.kick(conn.key, conn.ip)
        self._fail(conn, error)

    def _kick(self, conn: _Conn) -> None:
        conn.closing = True
        try:
            conn.writer.close()   # (what is buffered still goes out)
        except (OSError, RuntimeError):
            pass

    def _broadcast(self, conn: _Conn, msg: dict) -> None:
        for other in list(self.rooms.get(conn.room, {}).values()):
            if other is not conn:
                self._send(other, msg)

    # -- limits --

    def _allow(self, conn: _Conn) -> bool:
        """Token bucket: `rate` messages a second, bursts up to `rate`. False: drop this message."""
        now = self.clock()
        elapsed = max(0.0, now - conn.stamp)
        conn.stamp = now
        conn.tokens = min(self.rate, conn.tokens + elapsed * self.rate)
        conn.flood = max(0.0, conn.flood - elapsed * self.rate)
        if conn.tokens >= 1.0:
            conn.tokens -= 1.0
            return True
        conn.flood += 1.0
        if conn.flood > FLOOD_FACTOR * self.rate:
            self._say(f"{conn.name or conn.peer} sent far too many messages; disconnecting")
            self._cut_off(conn, "too many messages; disconnected")
        elif now - conn.warned >= 1.0:
            conn.warned = now
            self._send(conn, {"type": "error", "error": f"too many messages (max {self.rate:g} a second); some were dropped"})
        return False

    async def _lines(self, reader: asyncio.StreamReader, conn: _Conn):
        """Yield complete lines. A line over the size limit is skipped (with an error) up to its newline."""
        buf = bytearray()
        skipping = False
        loop = asyncio.get_running_loop()
        while not conn.closing:
            if conn.joined:
                timeout = self.idle_timeout
            else:   # (a deadline, not an idle time: sending a byte now and then doesn't keep a silent client)
                timeout = conn.hello_by - loop.time()
                if timeout <= 0:
                    self._fail(conn, "no hello received")
                    return
            try:
                chunk = await asyncio.wait_for(reader.read(4096), timeout)
            except asyncio.TimeoutError:
                self._fail(conn, "timed out" if conn.joined else "no hello received")
                return
            if not chunk:
                return
            buf += chunk
            while True:
                i = buf.find(b"\n")
                if i < 0:
                    if len(buf) > self.max_line:
                        if not skipping:
                            self._too_long(conn)
                        skipping = True
                        buf.clear()
                    break
                line = bytes(buf[:i])
                del buf[:i + 1]
                if skipping:
                    skipping = False
                    continue
                if len(line) > self.max_line:
                    self._too_long(conn)
                    continue
                yield line.rstrip(b"\r")

    def _too_long(self, conn: _Conn) -> None:
        if self._allow(conn):
            self._send(conn, {"type": "error", "error": f"line too long (max {self.max_line} bytes); dropped"})

    # -- one client --

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = _Conn(writer, self.rate, self.clock(), asyncio.get_running_loop().time() + self.hello_timeout)
        if self._closed or len(self._conns) >= self.max_clients:
            self._fail(conn, "relay full" if not self._closed else "relay stopped")
            return
        if self.lan_only and not security.address_allowed(conn.ip):
            self._fail(conn, "this session is for your home network only (internet play comes with the next update)")
            return
        if self.bans.blocked(None, conn.ip):
            self._fail(conn, "you can't join this session")
            return
        if conn.ip is not None and sum(1 for c in self._conns if c.ip == conn.ip and not c.joined) \
                >= security.MAX_PENDING_PER_ADDRESS:
            self._fail(conn, "too many connections from your address")
            return
        self._conns.add(conn)
        if self.room_code:   # (prove the code without sending it: security.code_proof)
            self._send(conn, {"type": "challenge", "nonce": conn.nonce})
        lines = self._lines(reader, conn)
        try:
            async for line in lines:
                if conn.closing:
                    break
                if not self._allow(conn):
                    continue
                msg = decode(line)
                if msg is None:
                    if not conn.joined:
                        self._fail(conn, "expected a JSON hello")
                        return
                    self._send(conn, {"type": "error", "error": "not a JSON message"})
                    continue
                if not conn.joined:
                    self._hello(conn, msg)
                else:
                    self._message(conn, msg)
        except (ConnectionError, OSError):
            pass
        except asyncio.CancelledError:
            pass
        except Exception as e:   # (a bug must cost one connection, not print a traceback for every client)
            self._say(f"Dropped {conn.name or conn.peer} after an error: {type(e).__name__}: {str(e)[:120]}")
        finally:
            await lines.aclose()
            self._leave(conn)
            self._conns.discard(conn)
            self._kick(conn)

    def _hello(self, conn: _Conn, msg: dict) -> None:
        if msg.get("type") != "hello":
            self._fail(conn, "say hello first")
            return
        if msg.get("version") != PROTOCOL_VERSION:
            self._fail(conn, f"version mismatch: this relay speaks version {PROTOCOL_VERSION}")
            return
        name = valid_name(msg.get("name"))
        if name is None:
            self._fail(conn, "bad name (1-20 letters, digits, space, _ . ' -)")
            return
        room = valid_room(msg.get("room", "default"))
        if room is None:
            self._fail(conn, "bad room (1-32 of A-Z a-z 0-9 _ -)")
            return
        members = self.rooms.get(room, {})
        if len(members) >= self.room_cap:
            self._fail(conn, f"room {room} is full ({self.room_cap} players)")
            return
        key = security.valid_fingerprint(msg.get("key"))
        info = self.room_info.get(room)
        is_host = (self.host_secret is not None and security.is_loopback(conn.ip)
                   and security.host_proof_matches(self.host_secret, room, msg.get("host_proof"))
                   and (info is None or info.get("host") is None))
        if not is_host and self.bans.blocked(key, conn.ip):   # (the host's own agent: never refused)
            self._fail(conn, "you can't join this session")
            return
        code_ok = None
        if self.room_code and not is_host:
            if self._wrong_codes.blocked(conn.ip):
                self._fail(conn, "too many wrong room codes; try again in 10 minutes")
                return
            proof, cnonce, plain = msg.get("code_proof"), msg.get("cnonce"), msg.get("code")
            if proof is None and not plain:
                self._fail(conn, "this world needs a room code")   # (no strike: nothing was tried)
                return
            if proof is not None:
                ok = security.proof_matches(self.room_code, conn.nonce, cnonce, proof)
                if ok:
                    code_ok = security.code_answer(self.room_code, conn.nonce, cnonce)
            else:   # (an older client sends the code itself)
                ok = security.code_matches(self.room_code, plain)
            if not ok:
                self._wrong_codes.wrong(conn.ip)
                self._fail(conn, "wrong room code")
                return
        self.rooms[room] = members
        if info is None:
            info = self.room_info[room] = {"host": None, "time": None, "weather": None, "story": None,
                                           "rules": dict(self.default_rules)}
        conn.id = self._next_id
        self._next_id += 1
        conn.name, conn.room, conn.bot, conn.joined = name, room, bool(msg.get("bot")), True
        conn.features = session.clean_features(msg.get("features"))
        conn.look = session.clean_look(msg.get("look")) if conn.has("look") else None
        conn.key = key
        conn.kinds = security.KindLimiter(clock=self.clock)
        if is_host:
            conn.host = True
            info["host"] = conn
        now = self.clock()
        peers = []
        for other in members.values():
            p = {"id": other.id, "name": other.name, "bot": other.bot}
            if conn.features is not None:
                p["features"] = other.features
                p["host"] = other.host
            if conn.has("look") and other.look is not None:
                p["look"] = other.look
            if other.state is not None:
                p["state"] = dict(other.state, age=round(max(0.0, now - other.state_at), 3))
            peers.append(p)
        members[conn.id] = conn
        welcome = {"type": "welcome", "id": conn.id, "name": name, "room": room,
                   "version": PROTOCOL_VERSION, "room_cap": self.room_cap, "peers": peers}
        if conn.features is not None:
            host = info.get("host")
            welcome.update(features=list(session.FEATURES), host=host.id if host is not None else None,
                           host_key=host.key if host is not None else None,
                           rules=dict(info["rules"]), you_host=conn.host, code=bool(self.room_code))
            if code_ok is not None:
                welcome["code_ok"] = code_ok
            for kind in ("time", "weather", "story"):
                if info.get(kind) is not None and conn.has(session.FEATURE_OF[kind]):
                    welcome[kind] = info[kind]
        if not security.is_loopback(conn.ip):
            self.remote_joins += 1
        self._send(conn, welcome)
        join = {"type": "join", "id": conn.id, "name": name, "bot": conn.bot}
        for other in list(members.values()):
            if other is not conn:
                line = dict(join, features=conn.features, host=conn.host) if other.features is not None else join
                if other.has("look") and conn.look is not None:
                    line = dict(line, look=conn.look)
                self._send(other, line)
        self._say(f"{name} joined room {room} ({len(members)} player{'s' if len(members) != 1 else ''})")

    def _message(self, conn: _Conn, msg: dict) -> None:
        kind = msg["type"]
        if kind in security.HOST_ONLY or kind == "story_offer":
            self._coop(conn, kind, msg)
            return
        if kind == "state":
            if conn.features is not None and conn.kinds is not None and not conn.kinds.allow("state"):
                return
            info = self.room_info.get(conn.room)
            if info is not None and info.get("host") is not None:
                return   # (a co-op room: no ghosts there, so a state flood can't slow the agents down)
            state = clean_state(msg)
            if state is None:
                self._send(conn, {"type": "error", "error": "bad state (needs finite t, x, y, z)"})
                return
            conn.state, conn.state_at = state, self.clock()
            self._broadcast(conn, dict(state, type="state", id=conn.id, name=conn.name))
        elif kind == "ping":
            t = msg.get("t")
            self._send(conn, {"type": "pong", "t": t if finite(t) is not None else None})   # (NaN can't be sent)
        elif kind == "bye":
            self._kick(conn)
        elif kind == "look" and conn.has("look"):
            self._look(conn, msg)
        else:
            self._send(conn, {"type": "error", "error": f"unknown message type {kind[:20]!r}"})

    # -- Who am I (docs/charsel.md) --

    def _look(self, conn: _Conn, msg: dict) -> None:
        """A new look: checked, at most one every LOOK_EVERY s (with LOOK_SLACK for the network's jitter: a client
        waits the whole LOOK_EVERY by its own clock), kept for late joiners, passed to the others that speak "look"."""
        now = self.clock()
        if now - conn.look_at < session.LOOK_EVERY * session.LOOK_SLACK:
            self._send(conn, {"type": "error", "error": f"look changes too often (one per {session.LOOK_EVERY:g} s)"})
            return
        look = session.clean_look(msg.get("look"))
        if look is None:
            self._send(conn, {"type": "error", "error": "bad look"})
            return
        conn.look, conn.look_at = look, now
        self._broadcast_feature(conn, "look", {"type": "look", "id": conn.id, "name": conn.name, "look": look})

    # -- co-op (host-only kinds and story offers) --

    def _coop(self, conn: _Conn, kind: str, msg: dict) -> None:
        info = self.room_info.get(conn.room)
        if info is None:
            return
        if kind in security.HOST_ONLY and not conn.host:
            if self._host_only.hit(conn):
                self._say(f"{conn.name} sent host-only messages; disconnecting")
                self._cut_off(conn, "sent host-only messages; disconnected")
            else:
                self._send(conn, {"type": "error", "error": f"only the host can send {kind!r}"})
            return
        if conn.kinds is not None and not conn.kinds.allow(kind):
            return
        sender = {"from": conn.id, "id": conn.id, "name": conn.name}
        if kind in ("time", "weather", "story", "story_offer"):
            if kind == "time":
                clean = session.clean_time(msg)
            elif kind == "weather":
                clean = session.clean_weather(msg)
            else:
                clean = session.clean_story(msg, offer=kind == "story_offer")
            if clean is None:
                self._send(conn, {"type": "error", "error": f"bad {kind}"})
                return
            line = dict(clean, type=kind, **sender)
            if kind == "story_offer":
                host = info.get("host")
                if not conn.has("story1") or host is None or host is conn:
                    return
                if not info["rules"].get("friends_story"):
                    self._send(conn, {"type": "error", "error": "the host doesn't take friends' story progress"})
                    return
                self._send(host, line)
                return
            info[kind] = line
            self._broadcast_feature(conn, session.FEATURE_OF[kind], line)
        elif kind == "rules":
            clean = session.clean_rules(msg)
            if clean is None:
                self._send(conn, {"type": "error", "error": "bad rules"})
                return
            info["rules"].update(clean)
            self._broadcast_feature(conn, "coop1", dict(info["rules"], type="rules", **sender))
        elif kind in ("kick", "ban"):
            target = self.rooms.get(conn.room, {}).get(session.clean_target(msg) or 0)
            if target is None or target is conn or target.host:
                self._send(conn, {"type": "error", "error": f"no such player to {kind}"})
                return
            self.remove(target, ban=kind == "ban", by=conn.name)
        elif kind == "code":
            code = msg.get("code")
            norm = security.normalize_code(code) if code else None
            if code and norm is None:
                self._send(conn, {"type": "error", "error": "a room code is 3 words from the word list"})
                return
            self.room_code = norm
        elif kind == "end":
            self._say(f"{conn.name} ended the session")
            for other in list(self.rooms.get(conn.room, {}).values()):
                if other is not conn:
                    self._send(other, {"type": "end", **sender})
                    self._send(other, {"type": "bye", "reason": f"{conn.name} ended the session"})
                    self._kick(other)

    # -- the host's kick and ban (by message, or from the host's agent in this process) --

    def _same_player(self, a: _Conn, b: _Conn) -> bool:
        """b is a connection of the player a: the same key, the same address (another PC), or on this PC (where
        every player shares 127.0.0.1) the same name."""
        if b.host:
            return False
        if a.key and b.key == a.key:
            return True
        if a.ip and b.ip == a.ip:
            return not security.is_loopback(a.ip) or (a.name is not None and b.name == a.name)
        return False

    def remove(self, target: _Conn, ban: bool = False, by: str = "the host") -> int:
        """Kick (10 minutes) or ban a player: every connection of his in every room is closed (his ghost connection
        too). Never the host; never this PC by address. Returns how many connections were closed."""
        if target.host:
            return 0
        done = "banned" if ban else "kicked"
        if ban:
            self.bans.ban(target.key, target.ip, target.name or "")
        else:
            self.bans.kick(target.key, target.ip)
        closed = 0
        for other in list(self._conns):
            if other is target or self._same_player(target, other):
                self._send(other, {"type": "bye", "reason": f"{done} by the host"})
                self._kick(other)
                closed += 1
        self._say(f"{by} {done} {target.name}")
        return closed

    def find_player(self, name_or_id, room: str | None = None) -> _Conn | None:
        """A joined player by id or (case-insensitive) name; the host never."""
        for members in ([self.rooms.get(room, {})] if room else list(self.rooms.values())):
            for c in list(members.values()):
                if c.host:
                    continue
                if (isinstance(name_or_id, int) and c.id == name_or_id) or \
                        (isinstance(name_or_id, str) and c.name and c.name.lower() == name_or_id.strip().lower()):
                    return c
        return None

    def players(self) -> list[dict]:
        """Everyone joined, for the host's `players` command."""
        return [{"id": c.id, "name": c.name, "room": c.room, "host": c.host, "address": c.ip}
                for members in self.rooms.values() for c in members.values()]

    def _broadcast_feature(self, conn: _Conn, feature: str, msg: dict) -> None:
        """Send to the others in the room that speak `feature` (never to visitors)."""
        for other in list(self.rooms.get(conn.room, {}).values()):
            if other is not conn and other.has(feature):
                self._send(other, msg)

    def _leave(self, conn: _Conn) -> None:
        self._host_only.clear(conn)
        if not conn.joined:
            return
        conn.joined = False
        info = self.room_info.get(conn.room)
        if info is not None and info.get("host") is conn:
            info["host"] = None
        members = self.rooms.get(conn.room)
        if members is None or members.get(conn.id) is not conn:
            return
        del members[conn.id]
        for other in list(members.values()):
            self._send(other, {"type": "leave", "id": conn.id, "name": conn.name})
        if not members:
            del self.rooms[conn.room]
            self.room_info.pop(conn.room, None)
        self._say(f"{conn.name} left room {conn.room} ({len(members)} left)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Relay for the multiplayer proof of concept (positions only).")
    ap.add_argument("--host", default="0.0.0.0", help="address to listen on (default 0.0.0.0: every network)")
    ap.add_argument("--port", type=int, default=DEFAULT_RELAY_PORT, help=f"TCP port (default {DEFAULT_RELAY_PORT})")
    ap.add_argument("--room-cap", type=int, default=DEFAULT_ROOM_CAP, help="players per room (default 8)")
    ap.add_argument("--rate", type=float, default=DEFAULT_RATE, help="messages per second per client (default 30)")
    ap.add_argument("--max-clients", type=int, default=DEFAULT_MAX_CLIENTS, help="connections in all (default 64)")
    args = ap.parse_args(argv)
    safe_console()
    if not 0 < args.port < 65536 or args.room_cap < 1 or args.rate <= 0 or args.max_clients < 1:
        ap.error("port, room cap, rate and max clients must be positive (port below 65536)")

    async def run():
        relay = Relay(args.host, args.port, rate=args.rate, room_cap=args.room_cap, max_clients=args.max_clients)
        try:
            host, port = await relay.start()
        except OSError as e:
            print(f"Can't listen on {args.host}:{args.port}: {e.strerror or e}")
            return 1
        print(f"Relay listening on {host}:{port}. Ctrl+C stops it.")
        try:
            await relay.serve_forever()
        finally:
            await relay.close()
            print("Relay stopped.")
        return 0

    try:
        return asyncio.run(run())
    except KeyboardInterrupt:   # (Ctrl+C: run() already closed the relay)
        return 0


if __name__ == "__main__":
    sys.exit(main())
