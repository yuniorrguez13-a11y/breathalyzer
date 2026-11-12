#!/usr/bin/env python3
"""Co-op agent (docs/coop.md): host a world or join one, with shared time and story together. Home network only.

    python tools/mp/coop_agent.py find                                   hosts on your home network (no typing)
    python tools/mp/coop_agent.py menu --library L --name Ben --own-save S [--game-port 7777]   Host / Join / Solo
    python tools/mp/coop_agent.py copy --own-save S --library L --world "Ana's world" [--check "w w w w"] [--host]
    python tools/mp/coop_agent.py host --library L --name Ana --story-from WORLD [--game-port 7777 | --time 21]
                                       [--code "apple river stone"] [--friends-story]
    python tools/mp/coop_agent.py join --library L --name Ben --save-dir COOP_SAVE [--address IP] [--code ...]
                                       [--yes | --no] [--game-port 7777] [--same-pc]

host: runs the relay on this PC (players connect to it; no project servers), answers Join lists, and is the only
player whose time and story count. The story comes from the host's world save (--story-from: its newest slot,
re-read when the game saves; a read counts only when two in a row agree, so a save being written is never sent).
The hour comes from the host's game (--game-port) or --time (a clock that runs like the game's: the bot as a stand-in
host). Live story reads from the game are off (no safe getter yet): new progress reaches friends when the host's game
saves. Type on the host's terminal: players, kick <name>, ban <name>, unban <name>, code <3 words>, code off, end.
join: finds the host (or --address), asks "Share story progress with <host>?" (Yes by default; --yes/--no for
scripts), backs up your co-op save and adds the host's settled story to it BEFORE your game starts (if your game
already uses the folder: at your next start), then follows the host's hour while you play (--game-port). It
reconnects by itself when the line drops. Only ever adds; never your own save: --save-dir must be a co-op copy.
Checked in the game 2026-11-12 with the bot as host (story at join, time); two PCs with a real host game: not yet.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mp import DEFAULT_RELAY_PORT, MAX_LINE, decode, encode, safe_console, valid_name  # noqa: E402
from mp import coop, discovery, security, session, story  # noqa: E402
from mp.relay import Relay  # noqa: E402

STORY_EVERY = 10.0     # the host sends its story line at least this often (and at once on a change)
PING_EVERY = 10.0      # both sides say something at least this often (the relay drops a client silent for 30 s)
TICK = 1.0             # game talk: once a second
GAME_RETRY = 5.0       # a game that doesn't answer is tried again after this long (not on every call)
STORY_WAIT = 5.0       # a friend waits this long for the host's first story line before his game starts
MERGE_RETRY = 15.0     # story that came while the friend's game ran: tried again this often (added once it's closed)
FIREWALL_AFTER = 60.0  # no friend reached the host in this long: say what to check
RECONNECT = (1.0, 2.0, 5.0, 10.0, 20.0, 30.0)
GIVE_UP_AFTER = 600.0  # a friend's agent stops trying to reach a host that has been gone this long
LINE_LIMIT = 4 * MAX_LINE
ROOM = "coop"
GAME_MINUTES_PER_SECOND = 1.0   # BotW: a day is 24 real minutes
FATAL = ("wrong room code", "needs a room code", "too many wrong room codes", "can't join this session",
         "version mismatch", "bad name", "bad room", "kicked by the host", "banned by the host", "ended the session",
         "doesn't know the room code")
FIREWALL_HINT = ("No friend has reached you yet. If their Join list doesn't show you: on this PC, allow Python "
                 "through Windows Firewall for Private networks (TCP and UDP 7788), and set your Wi-Fi to Private.")


def _fatal(reason) -> bool:
    return isinstance(reason, str) and any(f in reason for f in FATAL)


# -------------------------------------------------------------- stand-ins and the real game

class FakeGame:
    """A stand-in for the game (tests): an hour, a status and story bits."""

    def __init__(self, hour: float | None = 12.0, bits: set[int] | None = None, status: str = "play"):
        self.hour, self.bits, self._status = hour, set(bits or ()), status
        self.set_hours: list[float] = []
        self.added: list[str] = []

    def get_hour(self):
        return self.hour() if callable(self.hour) else self.hour

    def status(self):
        return self._status

    def set_hour(self, h: float) -> None:
        self.hour = h
        self.set_hours.append(h)

    def story_get(self):
        return self.bits

    def story_add(self, bits: set[int], n: int) -> None:
        self.added.append(story.encode_bits(bits, n))
        self.bits |= bits


class ClockHour:
    """An hour that runs like the game's clock (1 game minute a real second): the bot's stand-in for a host's game,
    so a friend's clock isn't pulled back every few seconds."""

    def __init__(self, start: float, clock=time.monotonic):
        self.start, self.clock, self.t0 = float(start) % 24.0, clock, clock()

    def __call__(self) -> float:
        return (self.start + (self.clock() - self.t0) * GAME_MINUTES_PER_SECOND / 60.0) % 24.0


class SaveStory:
    """The host's story from his world's save folder: the newest slot's story bits. Re-read when that file changes;
    a new read is used only when the next read (at least `settle` seconds later) gives the same file state and the
    same bits, so a save the game is still writing is never sent. Never writes anything."""

    def __init__(self, folder: Path, names: list[str], clock=time.monotonic, settle: float = 0.5):
        self.folder, self.names, self.clock, self.settle = Path(folder), names, clock, settle
        self.bits: set[int] | None = None
        self._key = None            # the file state behind self.bits
        self._cand = None           # (key, bits, when) waiting for its second read

    def _read(self):
        newest = story.newest_slot(self.folder)
        if newest is None:
            return None, None
        st = newest.stat()
        key = (str(newest), st.st_mtime_ns, st.st_size)
        if key == self._key:
            return key, self.bits
        bits = story.story_bits(newest, self.names)
        st2 = newest.stat()
        if (st2.st_mtime_ns, st2.st_size) != key[1:]:
            return None, None   # (it changed while being read)
        return key, bits

    def get(self) -> set[int] | None:
        try:
            key, bits = self._read()
        except (OSError, ValueError):
            return self.bits
        if key is None or key == self._key:
            return self.bits
        now = self.clock()
        if self._cand is not None and self._cand[0] == key and self._cand[1] == bits:
            if now - self._cand[2] >= self.settle:
                self.bits, self._key, self._cand = set(bits), key, None
        else:
            self._cand = (key, bits, now)
        return self.bits


def _hour_of(r) -> float | None:
    """The hour in a `time` reply: a number under hour/time/h/value, or an "HH:MM" text (the game answers `hour`,
    seen in probe run B)."""
    if not isinstance(r, dict):
        return None
    for k in ("hour", "time", "h", "value"):
        v = r.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 24:
            return float(v)
        if isinstance(v, str) and ":" in v:
            hh, _, mm = v.partition(":")
            if hh.strip().isdigit() and mm[:2].isdigit():
                return int(hh) % 24 + int(mm[:2]) / 60.0
    return None


class ControlGame:
    """The real game through its control port (SAVAGEWIND_CONTROL). Blocking: the agents call it from a worker
    thread, never on the event loop. Connects when the game answers (it may start after the agent, or restart); a game
    that doesn't answer is tried again only every `retry` seconds, and until then every read is None and every write
    is skipped. Loads the story list into the ghost mod (`coop story load`) whenever the mod has none (a new game)."""

    def __init__(self, port: int, n: int, list_path: Path | None = None, dlc: bool = False, clock=time.monotonic,
                 retry: float = GAME_RETRY):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from control import Control, ControlError
        self._Control, self._errors = Control, (ControlError, OSError)
        self.port, self.n, self.dlc, self.clock, self.retry = port, n, dlc, clock, retry
        self.list_path = Path(list_path).resolve() if list_path else None
        self.c = None
        self.last_error = None
        self.answered = False      # the last call reached the game (a reply or the game's own error)
        self._next_try = 0.0

    def _cmd(self, *words):
        self.answered = False
        if self.c is None and self.clock() < self._next_try:
            return None
        try:
            if self.c is None:
                self.c = self._Control(self.port, timeout=5.0)
            r = self.c.cmd(*words)
            self.answered = True
            return r
        except self._errors as e:
            self.last_error = str(e)
            lost = isinstance(e, OSError) or any(s in str(e) for s in (
                "can't connect", "didn't answer", "closed the connection", "invalid reply"))
            if not lost:
                self.answered = True
                return None   # (the game answered with an error: the connection is fine)
            try:
                if self.c is not None:
                    self.c.close()
            except Exception:   # noqa: BLE001 (closing a dead connection)
                pass
            self.c = None
            self._next_try = self.clock() + self.retry
            return None

    def get_hour(self):
        return _hour_of(self._cmd("time"))

    def status(self):
        """"play" when the ghost mod says the world is up and Link is free (not loading, no cutscene, no pause menu,
        for a moment now: `coop story status` "safe"); "busy" when not; None when the game doesn't answer. An older
        ghost mod without that field, or none: "play" while the game answers (as before)."""
        r = self._cmd("coop", "story", "status")
        if isinstance(r, dict) and isinstance(r.get("safe"), bool):
            return "play" if r["safe"] else "busy"
        return "play" if self.answered else None

    def set_hour(self, h: float) -> None:
        self._cmd("time", "set", f"{h:.3f}")

    def _list_ok(self, r) -> bool:
        """The mod holds our list; if not (a new game, or none yet), load it and say no."""
        if isinstance(r, dict) and r.get("n") == self.n:
            return True
        if self.list_path is not None and isinstance(r, dict):
            self._cmd("coop", "story", "load", str(self.list_path), *(["dlc"] if self.dlc else []))
        return False

    def story_get(self):
        r = self._cmd("coop", "story", "get")
        if not self._list_ok(r) or not r.get("ready"):
            return None
        return story.decode_bits(r.get("bits", ""), self.n)

    def story_add(self, bits: set[int], n: int) -> None:
        if self._list_ok(self._cmd("coop", "story", "status")):
            self._cmd("coop", "story", "add", story.encode_bits(bits, n))


class HostGame:
    """The host's side: the hour (his game through `control`, or a stand-in `hour()`), and the story: his world's
    save (`save`, a SaveStory) united with what his game reads live (none today: no safe getter yet)."""

    def __init__(self, control: ControlGame | None = None, save: SaveStory | None = None, hour=None):
        self.control, self.save, self.hour = control, save, hour

    def get_hour(self):
        if self.control is not None:
            return self.control.get_hour()
        return self.hour() if callable(self.hour) else self.hour

    def status(self):
        return "play"

    def story_get(self):
        live = self.control.story_get() if self.control is not None else None
        saved = self.save.get() if self.save is not None else None
        if live is None and saved is None:
            return None
        return set(live or ()) | set(saved or ())


# -------------------------------------------------------------- the line to the relay

class BadHost(Exception):
    """The host refused us, or answered something that isn't a host's answer."""


class Line:
    """One JSON-lines connection to the relay (lines over LINE_LIMIT bytes are a bad answer, never a crash)."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    @classmethod
    async def open(cls, host: str, port: int, timeout: float = 5.0):
        r, w = await asyncio.wait_for(asyncio.open_connection(host, port, limit=LINE_LIMIT), timeout)
        return cls(r, w)

    def send(self, msg: dict) -> None:
        try:
            if self.writer.transport is not None and not self.writer.transport.is_closing():
                self.writer.write(encode(msg))
        except (OSError, RuntimeError, ValueError):
            pass

    async def recv(self, timeout: float | None = None) -> dict | None:
        """The next message ({} for a line that isn't one), None when the line closed. Raises TimeoutError."""
        try:
            line = await asyncio.wait_for(self.reader.readline(), timeout)
        except ValueError:   # (a line over the limit)
            raise BadHost("bad answer from the host (a line too long)") from None
        except (ConnectionError, OSError):
            return None
        if not line:
            return None
        return decode(line.rstrip(b"\r\n")) or {}

    def close(self) -> None:
        try:
            self.writer.close()
        except (OSError, RuntimeError):
            pass


def _check_welcome(msg: dict) -> dict:
    """A welcome with the fields we use checked (BadHost if they're not what a host sends)."""
    def int_or_none(v):
        return v if v is None or (isinstance(v, int) and not isinstance(v, bool)) else False

    if not isinstance(msg.get("id"), int) or int_or_none(msg.get("host")) is False:
        raise BadHost("bad answer from the host")
    peers = msg.get("peers", [])
    if not isinstance(peers, list):
        raise BadHost("bad answer from the host")
    out = dict(msg)
    out["peers"] = [p for p in peers if isinstance(p, dict) and isinstance(p.get("id"), int)
                    and isinstance(p.get("name"), str)]
    out["host_key"] = security.valid_fingerprint(msg.get("host_key"))
    for k in ("story", "time"):
        if not isinstance(out.get(k), dict):
            out[k] = None
    return out


async def _handshake(line: Line, hello: dict, code: str | None, timeout: float = 5.0) -> dict:
    """Say hello and get the welcome. With a room code: answer the relay's challenge with a proof (the code is never
    sent) and check the welcome's proof that the relay has the code too. Raises BadHost."""
    nonce = None
    if code:
        try:
            first = await line.recv(3.0)
        except asyncio.TimeoutError:
            first = {}
        if first is None:
            raise BadHost("the host closed the connection")
        if first.get("type") == "error":
            raise BadHost(str(first.get("error"))[:200])
        if first.get("type") == "challenge":
            nonce = security.valid_nonce(first.get("nonce"))
    extra, cnonce = security.code_hello(code, nonce)
    line.send(dict(hello, **extra))
    end = time.monotonic() + timeout
    while True:
        try:
            msg = await line.recv(max(0.05, end - time.monotonic()))
        except asyncio.TimeoutError:
            raise BadHost("no answer from the host") from None
        if msg is None:
            raise BadHost("the host closed the connection")
        kind = msg.get("type")
        if kind == "error":
            raise BadHost(str(msg.get("error"))[:200])
        if kind == "welcome":
            break
    welcome = _check_welcome(msg)
    if welcome.get("code") is True and code and not security.answer_matches(code, nonce, cnonce,
                                                                           welcome.get("code_ok")):
        raise BadHost("this host doesn't know the room code: it may not be your friend's world")
    return welcome


async def _pinger(line: Line, every: float) -> None:
    while True:
        await asyncio.sleep(every)
        line.send({"type": "ping"})


# -------------------------------------------------------------- host

def host_command(text: str, relay: Relay, stop: asyncio.Event) -> str:
    """One command typed on the host's terminal. Returns what to show."""
    word, _, rest = text.strip().partition(" ")
    word, rest = word.lower(), rest.strip()
    if word in ("players", "who"):
        rows = [f"{p['name']}{' (you, host)' if p['host'] else ''}  [{p['room']}]" for p in relay.players()]
        return "Players: " + (", ".join(rows) if rows else "none")
    if word in ("kick", "ban") and rest:
        target = relay.find_player(rest)
        if target is None:
            return f"No player called {rest}."
        n = relay.remove(target, ban=word == "ban", by="You")
        return f"{'Banned' if word == 'ban' else 'Kicked (10 minutes)'} {target.name} ({n} connection(s) closed)."
    if word == "unban" and rest:
        n = relay.bans.unban(rest)
        return f"Unbanned {n} entr{'y' if n == 1 else 'ies'} for {rest}."
    if word == "code":
        if rest.lower() == "off":
            relay.room_code = None
            return "Room code off: anyone on your home network can join."
        norm = security.normalize_code(rest)
        if norm is None:
            return "A room code is 3 words from the word list."
        relay.room_code = norm
        return f"Room code: {norm} (for new joins)."
    if word in ("end", "quit", "stop"):
        stop.set()
        return "Ending the session."
    return "Commands: players, kick <name>, ban <name>, unban <name>, code <3 words>, code off, end"


async def host_session(library: Path, name: str, game, story_list: story.StoryList, *, dlc: bool = False,
                       code: str | None = None, port: int = DEFAULT_RELAY_PORT, discovery_port: int | None = None,
                       friends_story: bool = False, stop: asyncio.Event | None = None, log=print,
                       relay_host: str = "0.0.0.0", tick: float = TICK, started=None, commands=None,
                       relay_kw: dict | None = None, ping_every: float = PING_EVERY,
                       firewall_after: float = FIREWALL_AFTER) -> dict:
    """Run the relay, discovery and the host's own connection until `stop` (or `end`). The game is asked on a worker
    thread (a slow or frozen game never stalls the relay). The host's own line pings; if the relay drops it anyway,
    it says hello again with its host proof, or ends the session loudly. `commands`: an asyncio.Queue of typed
    lines (host_command). Returns counters."""
    stop = stop or asyncio.Event()
    secret = security.host_secret(library)
    key = security.player_key(library)
    bans = security.BanList(Path(library) / "mp" / "bans.json")
    relay = Relay(relay_host, port, host_secret=secret, room_code=code, bans=bans, friends_story=friends_story,
                  lan_only=True, log=log, **(relay_kw or {}))
    _, port = await relay.start()
    if started is not None:
        started(port)
    names = story_list.names(dlc)
    list_hash = story_list.list_hash(dlc)
    fp = security.fingerprint(key)
    words = security.check_words(fp)
    stop_disc = threading.Event()
    disc_thread = None
    if discovery_port is not None:
        try:
            sock = discovery.open_responder_socket(discovery_port)
            info = lambda: {"name": name, "players": relay.player_count(ROOM), "max": relay.room_cap,
                            "code": bool(relay.room_code), "port": port, "ver": "0.7.1", "check": words}
            disc_thread = threading.Thread(target=discovery.Responder(info).serve, args=(sock, stop_disc.is_set),
                                           daemon=True)
            disc_thread.start()
        except OSError as e:
            log(f"Join lists can't find this host ({e}); friends can still type your address.")
    hello = session.hello(name, ROOM, story={"v": 1, "base": list_hash, "dlc": None}, key=fp,
                          host_proof=security.host_proof(secret, ROOM))

    async def connect() -> Line:
        ln = await Line.open("127.0.0.1", port)
        try:
            welcome = await _handshake(ln, hello, None)
        except BadHost:
            ln.close()
            raise
        if not welcome.get("you_host"):
            ln.close()
            raise BadHost(f"couldn't become the host: {welcome}")
        return ln

    counts = {"time": 0, "story": 0, "port": port, "rehello": 0, "commands": 0}
    try:
        line = await connect()
    except (BadHost, OSError, asyncio.TimeoutError) as e:
        stop_disc.set()
        await relay.close()
        raise RuntimeError(f"couldn't become the host: {e}") from None
    log(f"Hosting {name}'s world on port {port}. Check words: {words}"
        + (f". Room code: {relay.room_code}" if relay.room_code else ""))
    seq, last_bits, last_story = 0, None, -1e9
    reader = asyncio.create_task(_drain(line, log))
    pinged = time.monotonic()
    hint_at = time.monotonic() + firewall_after if firewall_after else None
    try:
        while not stop.is_set():
            if reader.done():   # (the relay dropped our own line: hello again with the host proof, or end loudly)
                line.close()
                try:
                    line = await connect()
                except (BadHost, OSError, asyncio.TimeoutError) as e:
                    log(f"Lost your own place in the session ({e}). Ending it: start the host again.")
                    counts["lost"] = str(e)
                    break
                counts["rehello"] += 1
                reader = asyncio.create_task(_drain(line, log))
                last_bits = None   # (send the story again at once)
                log("Your own connection to the session dropped and is back.")
            h = await asyncio.to_thread(game.get_hour)
            if h is not None and 0 <= h < 24:
                line.send({"type": "time", "h": round(float(h), 3)})
                counts["time"] += 1
            bits = await asyncio.to_thread(game.story_get)
            now = time.monotonic()
            if bits is not None and (bits != last_bits or now - last_story >= STORY_EVERY):
                if bits != last_bits:
                    seq += 1
                line.send({"type": "story", "seq": seq, "list": list_hash, "bits": story.encode_bits(bits, len(names)),
                           "dlc": dlc})
                last_bits, last_story = set(bits), now
                counts["story"] += 1
            if now - pinged >= ping_every:
                line.send({"type": "ping"})
                pinged = now
            if hint_at is not None and now >= hint_at:
                if relay.remote_joins == 0:
                    log(FIREWALL_HINT)
                hint_at = None
            while commands is not None and not commands.empty():
                log(host_command(commands.get_nowait(), relay, stop))
                counts["commands"] += 1
            try:
                await asyncio.wait_for(stop.wait(), tick)
            except asyncio.TimeoutError:
                pass
    finally:
        line.send({"type": "end"})
        await asyncio.sleep(0.05)
        reader.cancel()
        line.close()
        stop_disc.set()
        if disc_thread:
            disc_thread.join(2)
        await relay.close()
    return counts


async def _drain(line: Line, log) -> None:
    while True:
        try:
            msg = await line.recv()
        except BadHost:
            return
        if msg is None:
            return
        if msg.get("type") == "join":
            log(f"{msg.get('name')} joined" + (" (older Breathalyzer: sees you, nothing else)"
                                                if msg.get("features") is None else ""))
        elif msg.get("type") == "leave":
            log(f"{msg.get('name')} left")


# -------------------------------------------------------------- join

def ask_story(host: str, answer: bool | None) -> bool:
    """The opt-in question, every session. answer: True/False from --yes/--no; None asks (Enter = Yes)."""
    if answer is not None:
        return answer
    print("\n".join(story.story_question(host)))
    try:
        got = input("> ").strip().lower()
    except EOFError:
        return False
    return got in ("", "y", "yes")


def _guest_tick(game, timef: coop.TimeFollower, storyf: coop.StoryFollower, sharing: bool, n: int) -> dict:
    """One round of game talk for a friend (a worker thread): follow the host's hour; with sharing, add the live-safe
    story bits the friend's game lacks (only when it can read its own: no getter today, so never)."""
    did = {"time": 0, "adds": 0}
    status = game.status()
    h = timef.decide(game.get_hour(), status) if status is not None else None
    if h is not None:
        game.set_hour(h)
        did["time"] = 1
    if sharing and status == "play":
        mine = game.story_get()
        if mine is not None:
            add = storyf.to_add(mine)
            if add:
                game.story_add(add, n)
                did["adds"] = 1
    return did


async def join_session(library: Path, name: str, address: str, save_dir: Path, story_list: story.StoryList, *,
                       game=None, dlc: bool = False, code: str | None = None, port: int = DEFAULT_RELAY_PORT,
                       share: bool | None = None, stop: asyncio.Event | None = None, log=print,
                       before_game=None, processes=None, check: str | None = None, reconnect: bool = True,
                       retry=RECONNECT, story_wait: float = STORY_WAIT, tick: float = TICK,
                       ping_every: float = PING_EVERY, give_up_after: float = GIVE_UP_AFTER) -> dict:
    """Join a host. With a Yes: back up `save_dir` and add the host's settled story BEFORE the game starts
    (`before_game()` is called after that, e.g. to start the game); if a game already uses the folder, it's added
    at the next start (tried again while the agent runs). Then follow the host's hour until stop, the host ends, or
    a kick; a dropped line is reconnected with backoff (for up to `give_up_after` seconds). `check`: the host's
    check words from the Join list. Returns what happened."""
    stop = stop or asyncio.Event()
    names = story_list.names(dlc)
    list_hash = story_list.list_hash(dlc)
    key = security.fingerprint(security.player_key(library))
    hello = session.hello(name, ROOM, story={"v": 1, "base": list_hash, "dlc": None}, key=key)
    backups = Path(library) / "mp" / "backups" / Path(save_dir).name
    out = {"merged": None, "backup": None, "times": 0, "adds": 0, "ended": None, "error": None, "joins": 0,
           "story": None}
    timef = coop.TimeFollower(None)
    storyf = coop.StoryFollower(len(names), list_hash, None, names)
    st = {"sharing": None if share is not False else False, "answer": share, "made_backup": False,
          "host_key": None, "host": "the host", "merge_at": 0.0, "said_later": False}
    started = False
    waits = 0
    lost_at = time.monotonic()

    def merge_now() -> str | None:
        """Add the host's story to the co-op copy (a worker thread). Returns what to say, or None."""
        try:
            res = story.merge_folder(save_dir, names, storyf.host_bits, backups, made_backup=st["made_backup"],
                                     processes=processes, host_key=st["host_key"], check_words=security.check_words)
        except story.GameRunning:
            storyf.changed = True
            if st["said_later"]:
                return None
            st["said_later"] = True
            return f"Your game uses the co-op save now: {st['host']}'s new story is added at your next start."
        except story.WrongHost as e:
            st["sharing"] = False
            return str(e)
        storyf.changed = False
        st["said_later"] = False
        if res.backup is not None:
            st["made_backup"] = True
            out["backup"] = res.backup
        out["merged"] = res.summary(st["host"])
        return out["merged"] + (f" (backup: {res.backup})" if res.backup else "")

    async def try_merge() -> None:
        try:
            said = await asyncio.to_thread(merge_now)
        except (OSError, ValueError) as e:
            said = f"Story not added ({e})."
        st["merge_at"] = time.monotonic() + MERGE_RETRY
        if said:
            log(said)

    while not stop.is_set():
        line = None
        try:
            line = await Line.open(address, port)
            welcome = await _handshake(line, hello, code)
        except (OSError, asyncio.TimeoutError, BadHost) as e:
            if line is not None:
                line.close()
            why = str(e) if isinstance(e, BadHost) else f"can't reach the host at {address}:{port}"
            if out["joins"] == 0 or _fatal(why) or not reconnect:
                out["error"] = why
                return out
            if time.monotonic() - lost_at > give_up_after:
                out["ended"] = f"lost {st['host']} ({why})"
                log(f"Gave up: {out['ended']}.")
                return out
            await _wait(stop, retry[min(waits, len(retry) - 1)])
            waits += 1
            continue
        waits = 0
        out["joins"] += 1
        pinger = asyncio.create_task(_pinger(line, ping_every))
        try:
            if session.older_relay(welcome):
                log("The host's Breathalyzer is older: you see each other, nothing else is shared.")
            host_id = welcome.get("host")
            st["host"] = next((p["name"] for p in welcome["peers"] if p["id"] == host_id), st["host"])
            if welcome.get("host_key"):
                st["host_key"] = welcome["host_key"]
                if check and security.check_words(welcome["host_key"]) != check:
                    log("This host's check words don't match the Join list: story is off for this session.")
                    st["sharing"] = False
            timef.host_id = host_id
            storyf.new_host(host_id)
            if out["joins"] > 1:
                log(f"Back in {st['host']}'s world.")
            pending = []
            if welcome.get("story") is not None:
                storyf.on_story(welcome["story"])
            elif share is not False and st["sharing"] is not False and not started:
                end = time.monotonic() + story_wait   # (the host's first story line may come a moment later)
                while storyf.seq < 0 and time.monotonic() < end:
                    try:
                        msg = await line.recv(max(0.05, end - time.monotonic()))
                    except asyncio.TimeoutError:
                        break
                    if msg is None:
                        break
                    if msg.get("type") == "story":
                        storyf.on_story(msg)
                    else:
                        pending.append(msg)
            if storyf.why_off:
                log(storyf.why_off)
            if storyf.seq >= 0 and st["sharing"] is None:
                st["sharing"] = await asyncio.to_thread(ask_story, st["host"], share)
            elif storyf.seq < 0 and not started and share is not False and st["sharing"] is not False:
                log(f"{st['host']} isn't sharing story yet: you get time only for now.")
            if st["sharing"] and storyf.changed:
                await try_merge()
            if welcome.get("time"):
                timef.on_time(welcome["time"])
            if not started:
                started = True
                if before_game is not None:
                    before_game()
            reason = await _follow(line, game, timef, storyf, st, out, pending, len(names), stop, tick, try_merge,
                                   log)
        finally:
            pinger.cancel()
            line.close()
        if stop.is_set() or _fatal(reason) or not reconnect:
            out["ended"] = out["ended"] or reason
            break
        log(f"Lost the line to {st['host']} ({reason or 'no answer'}); trying again...")
        lost_at = time.monotonic()
        await _wait(stop, retry[0])
    out["story"] = story.encode_bits(storyf.host_bits, len(names)) if storyf.seq >= 0 else None
    return out


async def _follow(line: Line, game, timef, storyf, st, out, pending, n, stop, tick, try_merge, log) -> str | None:
    """Follow the host until the line ends. Messages are read as they come (no game talk there); the game is
    asked once a `tick` on a worker thread. Returns why it ended (None: the line dropped)."""
    reason: list[str | None] = [None]

    def take(msg: dict) -> bool:
        """Handle one message; False when the session is over."""
        kind = msg.get("type")
        if kind == "time":
            timef.on_time(msg)
        elif kind == "story":
            if storyf.on_story(msg) is False and storyf.why_off:
                log(storyf.why_off)
        elif kind == "join" and msg.get("host") is True and isinstance(msg.get("id"), int):
            timef.host_id = msg["id"]   # (the host's agent reconnected: same host, new id)
            storyf.new_host(msg["id"])
        elif kind == "end":
            reason[0] = out["ended"] = f"{msg.get('name')} ended the session"
            log(out["ended"])
            return False
        elif kind == "bye":
            reason[0] = str(msg.get("reason") or "the host closed the connection")[:200]
            if _fatal(reason[0]):
                out["ended"] = out["ended"] or reason[0]
                log(f"The host closed your connection: {reason[0]}.")
            return False
        return True

    async def read() -> None:
        for msg in pending:
            if not take(msg):
                return
        while True:
            try:
                msg = await line.recv()
            except BadHost as e:
                reason[0] = str(e)
                return
            if msg is None or not take(msg):
                return

    reader = asyncio.create_task(read())
    stopped = asyncio.create_task(stop.wait())
    try:
        while not stop.is_set() and not reader.done():
            if game is not None:
                did = await asyncio.to_thread(_guest_tick, game, timef, storyf, bool(st["sharing"]), n)
                out["times"] += did["time"]
                out["adds"] += did["adds"]
            if st["sharing"] is None and storyf.seq >= 0:   # (the host's story came after the join)
                st["sharing"] = await asyncio.to_thread(ask_story, st["host"], st["answer"])
            if st["sharing"] and storyf.changed and time.monotonic() >= st["merge_at"]:
                await try_merge()
            await asyncio.wait([reader, stopped], timeout=tick, return_when=asyncio.FIRST_COMPLETED)
    finally:
        reader.cancel()
        stopped.cancel()
    return reason[0]


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), seconds)
    except asyncio.TimeoutError:
        pass


# -------------------------------------------------------------- the menu and the command line

def _host_game(a, names: list[str], story_from: Path | None, log=print):
    """The host's game for the CLI and the menu: hour from --game-port (else --time as a running clock), story from
    the world save. Says loudly when a source is missing."""
    control = ControlGame(a.game_port, len(names), a.list, a.dlc) if a.game_port else None
    hour = ClockHour(a.time) if getattr(a, "time", None) is not None and control is None else None
    save = SaveStory(story_from, names) if story_from else None
    if save is None:
        log("WARNING: no story source (--story-from <your world's save folder>): friends get time only, no story.")
    elif not story.slot_files(story_from):
        log(f"WARNING: no save in {story_from}: friends get no story until your game saves there.")
    if control is None and hour is None:
        log("Note: no --game-port (or --time): friends' time of day isn't shared.")
    return HostGame(control, save, hour)


async def _run_host(a, sl, game, **kw) -> dict:
    """host_session with the host's typed commands (a thread reads the terminal)."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    stop = asyncio.Event()

    def typed():
        for text in sys.stdin:
            loop.call_soon_threadsafe(q.put_nowait, text)
            if text.strip().lower() in ("end", "quit", "stop"):
                return

    if sys.stdin is not None and sys.stdin.isatty():
        threading.Thread(target=typed, daemon=True).start()
        print("Type: players, kick <name>, ban <name>, unban <name>, code <3 words>, code off, end")
    return await host_session(a.library, a.name, game, sl, dlc=a.dlc, code=a.code, port=a.port,
                              discovery_port=discovery.DISCOVERY_PORT, stop=stop, commands=q, **kw)


def menu(a, sl: story.StoryList, n: int) -> int:
    """The Multiplayer menu (Host / Join / Solo) in a window, then the co-op save copy, then host or join. Your own
    save is only read (copied once); the game is to be started with the printed save folder."""
    from mp import lobby, worlds
    plan = lobby.choose()
    if plan["mode"] == "solo":
        print("Play solo: your own save.")
        return 0
    try:
        if plan["mode"] == "host":
            folder = worlds.host_world(a.own_save, a.library, f"{a.name}'s world", protect=[a.own_save])
            print(f"Your world: start the game with SAVAGEWIND_SAVE_DIR={folder}")
            asyncio.run(_run_host(a, sl, _host_game(a, sl.names(a.dlc), folder)))
            return 0
        folder = worlds.joined_copy(a.own_save, a.library, plan["world"], check=plan.get("check"),
                                    protect=[a.own_save])
        game = ControlGame(a.game_port, n, a.list, a.dlc) if a.game_port else None
        out = asyncio.run(join_session(
            a.library, a.name, plan["address"], folder, sl, game=game, dlc=a.dlc, code=plan["code"],
            port=plan["port"], share=plan["share"], check=plan.get("check"),
            before_game=lambda: print(f"Ready: start the game with SAVAGEWIND_SAVE_DIR={folder} and Continue")))
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError) as e:
        print(f"Stopped: {e}")
        return 1
    if out["error"]:
        print(f"Couldn't join: {out['error']}")
        return 1
    return 0


def _pick_host(hosts: list[dict]) -> dict | None:
    """The host to join: the only one found, else the player picks (never the first of several on its own: a fake
    host could sort first)."""
    if len(hosts) == 1:
        return hosts[0]
    for i, h in enumerate(hosts, 1):
        print(f"{i}  {h['world']}  -  {h['name']}  ({h['address']}; check words {h['check']})")
    if not sys.stdin or not sys.stdin.isatty():
        print("Several hosts found: give --address (ask your host for it, and compare the check words).")
        return None
    try:
        got = input("Which one (number)? ").strip()
    except EOFError:
        return None
    return hosts[int(got) - 1] if got.isdigit() and 1 <= int(got) <= len(hosts) else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Co-op agent: host or join a world (home network), shared time and story.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", help="list hosts on your home network")
    f.add_argument("--seconds", type=float, default=3.0)
    c = sub.add_parser("copy", help="make your co-op copy of your own save (your save is only read)")
    c.add_argument("--own-save", required=True, type=Path)
    c.add_argument("--library", required=True, type=Path)
    c.add_argument("--world", required=True, help="the host's world name (a friend) or your own (a host)")
    c.add_argument("--check", help="the host's 4 check words (from find): one copy per host")
    c.add_argument("--host", action="store_true", help="a host's world (worlds/), not a friend's copy (joined/)")
    for name in ("host", "join", "menu"):
        p = sub.add_parser(name)
        p.add_argument("--library", required=True, type=Path, help="the multiplayer library (keys, bans, backups)")
        p.add_argument("--name", required=True)
        p.add_argument("--list", type=Path, help="story list (default <library>/mp/story-list.txt)")
        p.add_argument("--dlc", action="store_true", help="both players have the DLC: share its story too")
        p.add_argument("--code", help="room code: 3 words")
        p.add_argument("--port", type=int, default=DEFAULT_RELAY_PORT)
        p.add_argument("--game-port", type=int, help="your game's control port (SAVAGEWIND_CONTROL)")
    h = sub.choices["host"]
    h.add_argument("--story-from", type=Path, help="your world's save folder: its newest slot is the story you share")
    h.add_argument("--time", type=float, help="no game: start the shared hour here (it runs like the game's clock)")
    h.add_argument("--friends-story", action="store_true", help="friends' new story progress counts in your world")
    j = sub.choices["join"]
    j.add_argument("--address", help="the host's address (default: the host found, if only one)")
    j.add_argument("--save-dir", required=True, type=Path, help="your CO-OP save copy (never your own save)")
    j.add_argument("--same-pc", action="store_true",
                   help="the host's game runs on this PC: trust Savage Wind's save lock alone")
    g = j.add_mutually_exclusive_group()
    g.add_argument("--yes", dest="share", action="store_true", default=None)
    g.add_argument("--no", dest="share", action="store_false")
    m = sub.choices["menu"]
    m.add_argument("--own-save", required=True, type=Path,
                   help="your own save folder: only READ (copied once into the library's worlds/ or joined/)")
    a = ap.parse_args(argv)
    safe_console()
    if a.cmd == "find":
        hosts = discovery.search(a.seconds)
        for i, hst in enumerate(hosts, 1):
            print(f"{i}  {hst['world']}  -  {hst['name']}  -  {hst['players']} of {hst['max']} players"
                  + ("  -  needs a room code" if hst["code"] else "") + f"  ({hst['address']}, {hst['check']})")
        if not hosts:
            print("No host found on your home network. Same Wi-Fi? On the host PC, Windows Firewall must allow "
                  "Python on Private networks (UDP and TCP 7788). Or give the host's address with --address.")
        return 0
    if a.cmd == "copy":
        from mp import worlds
        try:
            if a.host:
                print(worlds.host_world(a.own_save, a.library, a.world, protect=[a.own_save]))
            else:
                print(worlds.joined_copy(a.own_save, a.library, a.world, check=a.check, protect=[a.own_save]))
        except (OSError, ValueError) as e:
            print(f"Stopped: {e}")
            return 1
        return 0
    if valid_name(a.name) is None:
        ap.error("bad --name")
    if a.code and security.normalize_code(a.code) is None:
        ap.error("a room code is 3 words from the word list")
    lst = a.list = a.list or a.library / "mp" / story.LIST_FILE
    try:
        sl = story.StoryList.load(lst)
    except OSError:
        ap.error(f"no story list at {lst}: run  python tools/mp/story.py list --romfs <your romfs> --out {lst}")
    n = len(sl.names(a.dlc))
    if a.cmd == "menu":
        a.time = None
        return menu(a, sl, n)
    if a.cmd == "host":
        try:
            asyncio.run(_run_host(a, sl, _host_game(a, sl.names(a.dlc), a.story_from),
                                  friends_story=a.friends_story))
        except KeyboardInterrupt:
            pass
        except RuntimeError as e:
            print(f"Stopped: {e}")
            return 1
        return 0
    address, check = a.address, None
    if not address:
        hosts = discovery.search(3.0)
        if not hosts:
            print("No host found on your home network: give --address.")
            return 1
        pick = _pick_host(hosts)
        if pick is None:
            return 1
        address, a.port, check = pick["address"], pick["port"], pick["check"] or None
        print(f"Joining {pick['world']} ({address}; check words {pick['check']})")
    game = ControlGame(a.game_port, n, a.list, a.dlc) if a.game_port else None
    try:
        out = asyncio.run(join_session(a.library, a.name, address, a.save_dir, sl, game=game, dlc=a.dlc, code=a.code,
                                       port=a.port, share=a.share, check=check,
                                       processes=[] if a.same_pc else None))
    except KeyboardInterrupt:
        return 0
    except OSError as e:
        print(f"Stopped: {e}")
        return 1
    if out["error"]:
        print(f"Couldn't join: {out['error']}")
        return 1
    print(json.dumps({k: str(v) if isinstance(v, Path) else v for k, v in out.items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
