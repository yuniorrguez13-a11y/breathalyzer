#!/usr/bin/env python3
"""Co-op agent (docs/coop.md): host a world or join one, with shared time and story together. Home network only.

    python tools/mp/coop_agent.py find                                   hosts on your home network (no typing)
    python tools/mp/coop_agent.py menu --library L --name Ben --own-save S   the Multiplayer menu: Host / Join / Solo
    python tools/mp/coop_agent.py copy --own-save S --library L --world "Ana's world" [--host]   a co-op copy
    python tools/mp/coop_agent.py host --library L --name Ana [--code "apple river stone"] [--story-from SAVE]
                                       [--time 21] [--game-port 7777] [--friends-story]
    python tools/mp/coop_agent.py join --library L --name Ben --save-dir COOP_SAVE [--address IP] [--code ...]
                                       [--yes | --no] [--game-port 7777]

host: runs the relay on this PC (players connect to it; no project servers), answers Join lists, and is the only
player whose time and story count. With --game-port it reads the hour (`time`) and the story (`coop story get`, the
ghost mod 0.7.0) from your game; --story-from / --time stand in for a game (the bot as a fake host, for tests).
join: finds the host (or --address), asks "Share story progress with <host>?" (Yes by default; --yes/--no for
scripts), backs up your co-op save folder and adds the host's story to it BEFORE your game starts, then follows the
host's time and story while you play (with --game-port: `time set`, `coop story add`). Only ever adds; never your
own save: --save-dir must be a co-op copy (the launcher makes one under the multiplayer library).
Not yet run with the game (docs/coop.md "Needs the game").
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
from mp import DEFAULT_RELAY_PORT, decode, encode, safe_console, valid_name  # noqa: E402
from mp import coop, discovery, security, session, story  # noqa: E402
from mp.relay import Relay  # noqa: E402

STORY_EVERY = 10.0     # the host sends its story line at least this often (and at once on a change)
PING_EVERY = 10.0      # a friend's agent says something at least this often (the relay drops one silent for 30 s)
ROOM = "coop"


class FakeGame:
    """A stand-in for the game (tests, the bot as host): an hour, a status and story bits."""

    def __init__(self, hour: float | None = 12.0, bits: set[int] | None = None, status: str = "play"):
        self.hour, self.bits, self._status = hour, set(bits or ()), status
        self.set_hours: list[float] = []
        self.added: list[str] = []

    def get_hour(self):
        return self.hour

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


def _hour_of(r) -> float | None:
    """The hour in a `time` reply: a number under hour/time/h/value, or an "HH:MM" text. (The reply's key is not
    confirmed yet: probe P6.)"""
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
    """The real game through its control port (SAVAGEWIND_CONTROL). Connects when the game answers (it may start
    after the agent, or restart): until then every read is None and every write is skipped. Loads the story list into
    the ghost mod (`coop story load`) whenever the mod has none (a new game). Not yet run: the `time` reply's key is a
    guess that the game check confirms."""

    def __init__(self, port: int, n: int, list_path: Path | None = None, dlc: bool = False):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from control import Control, ControlError
        self._Control, self._errors = Control, (ControlError, OSError)
        self.port, self.n, self.dlc = port, n, dlc
        self.list_path = Path(list_path).resolve() if list_path else None
        self.c = None
        self.last_error = None

    def _cmd(self, *words):
        try:
            if self.c is None:
                self.c = self._Control(self.port, timeout=5.0)
            return self.c.cmd(*words)
        except self._errors as e:
            self.last_error = str(e)
            lost = isinstance(e, OSError) or any(s in str(e) for s in (
                "can't connect", "didn't answer", "closed the connection", "invalid reply"))
            if not lost:
                return None   # (the game answered with an error: the connection is fine)
            try:
                if self.c is not None:
                    self.c.close()
            except Exception:   # noqa: BLE001 (closing a dead connection)
                pass
            self.c = None
            return None

    def get_hour(self):
        return _hour_of(self._cmd("time"))

    def status(self):
        return "play"   # (probe P7: the cutscene/menu flag; until then time is only set when 2+ minutes off)

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


class Line:
    """One JSON-lines connection to the relay."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    @classmethod
    async def open(cls, host: str, port: int):
        r, w = await asyncio.open_connection(host, port)
        return cls(r, w)

    def send(self, msg: dict) -> None:
        self.writer.write(encode(msg))

    async def recv(self, timeout: float | None = None) -> dict | None:
        line = await asyncio.wait_for(self.reader.readline(), timeout)
        return decode(line.rstrip(b"\r\n")) if line else None

    def close(self) -> None:
        try:
            self.writer.close()
        except (OSError, RuntimeError):
            pass


# -------------------------------------------------------------- host

async def host_session(library: Path, name: str, game, story_list: story.StoryList, *, dlc: bool = False,
                       code: str | None = None, port: int = DEFAULT_RELAY_PORT, discovery_port: int | None = None,
                       friends_story: bool = False, stop: asyncio.Event | None = None, log=print,
                       relay_host: str = "0.0.0.0", tick: float = 1.0, started=None) -> dict:
    """Run the relay, discovery and the host's own connection until `stop`. Returns counters."""
    stop = stop or asyncio.Event()
    secret = security.host_secret(library)
    key = security.player_key(library)
    bans = security.BanList(Path(library) / "mp" / "bans.json")
    relay = Relay(relay_host, port, host_secret=secret, room_code=code, bans=bans, friends_story=friends_story,
                  lan_only=True, log=log)
    _, port = await relay.start()
    if started is not None:
        started(port)
    names = story_list.names(dlc)
    list_hash = story_list.base_hash if not dlc else story_list._hash(names)
    fp = security.fingerprint(key)
    stop_disc = threading.Event()
    disc_thread = None
    if discovery_port is not None:
        try:
            sock = discovery.open_responder_socket(discovery_port)
            info = lambda: {"name": name, "players": relay.player_count(ROOM), "max": relay.room_cap,
                            "code": bool(relay.room_code), "port": port, "ver": "0.7.0",
                            "check": security.check_words(fp)}
            disc_thread = threading.Thread(target=discovery.Responder(info).serve, args=(sock, stop_disc.is_set),
                                           daemon=True)
            disc_thread.start()
        except OSError as e:
            log(f"Join lists can't find this host ({e}); friends can still type your address.")
    line = await Line.open("127.0.0.1", port)
    line.send(session.hello(name, ROOM, story={"v": 1, "base": list_hash, "dlc": None}, key=fp,
                            host_proof=security.host_proof(secret, ROOM)))
    welcome = await line.recv(5.0)
    if not welcome or not welcome.get("you_host"):
        raise RuntimeError(f"couldn't become the host: {welcome}")
    log(f"Hosting {name}'s world on port {port}. Check words: {security.check_words(fp)}"
        + (f". Room code: {relay.room_code}" if relay.room_code else ""))
    counts = {"time": 0, "story": 0, "port": port}
    seq, last_bits, last_story = 0, None, -1e9
    reader = asyncio.create_task(_drain(line, log))
    try:
        while not stop.is_set():
            h = game.get_hour()
            if h is not None and 0 <= h < 24:
                line.send({"type": "time", "h": round(float(h), 3)})
                counts["time"] += 1
            bits = game.story_get()
            now = time.monotonic()
            if bits is not None and (bits != last_bits or now - last_story >= STORY_EVERY):
                if bits != last_bits:
                    seq += 1
                line.send({"type": "story", "seq": seq, "list": list_hash, "bits": story.encode_bits(bits, len(names)),
                           "dlc": dlc})
                last_bits, last_story = set(bits), now
                counts["story"] += 1
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
        msg = await line.recv()
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


async def join_session(library: Path, name: str, address: str, save_dir: Path, story_list: story.StoryList, *,
                       game=None, dlc: bool = False, code: str | None = None, port: int = DEFAULT_RELAY_PORT,
                       share: bool | None = None, stop: asyncio.Event | None = None, log=print,
                       before_game=None, processes=None) -> dict:
    """Join a host. With its story in the welcome and a Yes: back up `save_dir` and add the host's story before the
    game starts (`before_game()` is called after that, e.g. to start the game). Then follow time and story until stop
    or the host ends. Returns what happened."""
    stop = stop or asyncio.Event()
    names = story_list.names(dlc)
    list_hash = story_list.base_hash if not dlc else story_list._hash(names)
    key = security.fingerprint(security.player_key(library))
    line = await Line.open(address, port)
    line.send(session.hello(name, ROOM, story={"v": 1, "base": list_hash, "dlc": None}, code=code, key=key))
    welcome = await line.recv(5.0)
    out = {"merged": None, "backup": None, "times": 0, "adds": 0, "ended": None, "error": None}
    if not welcome or welcome.get("type") != "welcome":
        out["error"] = (welcome or {}).get("error", "no answer")
        line.close()
        return out
    if session.older_relay(welcome):
        log("The host's Breathalyzer is older: you see each other, nothing else is shared.")
    host_id = welcome.get("host")
    host_name = next((p["name"] for p in welcome.get("peers", []) if p.get("id") == host_id), "the host")
    timef = coop.TimeFollower(host_id)
    storyf = coop.StoryFollower(len(names), list_hash, host_id)
    sharing = False
    first = welcome.get("story")
    if first is not None:
        storyf.on_story(first)
        if storyf.why_off:
            log(storyf.why_off)
        elif ask_story(host_name, share):
            sharing = True
            backups = Path(library) / "mp" / "backups" / Path(save_dir).name
            res = story.merge_folder(save_dir, names, storyf.host_bits, backups, processes=processes)
            out["merged"], out["backup"] = res.summary(host_name), res.backup
            log(out["merged"] + f" (backup: {res.backup})")
    if welcome.get("time"):
        timef.on_time(welcome["time"])
    if before_game is not None:
        before_game()
    pinged = time.monotonic()
    try:
        while not stop.is_set():
            if time.monotonic() - pinged >= PING_EVERY:   # (the relay drops a client silent for 30 s)
                line.send({"type": "ping"})
                pinged = time.monotonic()
            try:
                msg = await line.recv(1.0)
            except asyncio.TimeoutError:
                msg = {}
            if msg is None:
                break
            kind = msg.get("type")
            if kind == "time":
                timef.on_time(msg)
            elif kind == "story" and storyf.on_story(msg) is False and storyf.why_off:
                log(storyf.why_off)
            elif kind == "end":
                out["ended"] = f"{msg.get('name')} ended the session"
                log(out["ended"])
                break
            elif kind == "bye":
                out["ended"] = out["ended"] or msg.get("reason")
                break
            if game is not None:
                h = timef.decide(game.get_hour(), game.status())
                if h is not None:
                    game.set_hour(h)
                    out["times"] += 1
                if sharing:
                    mine = game.story_get()
                    if mine is not None:
                        add = storyf.to_add(mine)
                        if add:
                            game.story_add(add, len(names))
                            out["adds"] += 1
    finally:
        line.close()
    return out


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
            files = story.slot_files(folder)
            game = ControlGame(a.game_port, n, a.list, a.dlc) if a.game_port else FakeGame(
                None, story.story_bits(max(files, key=lambda p: p.stat().st_mtime), sl.names(a.dlc)))
            asyncio.run(host_session(a.library, a.name, game, sl, dlc=a.dlc, code=a.code, port=a.port,
                                     discovery_port=discovery.DISCOVERY_PORT))
            return 0
        folder = worlds.joined_copy(a.own_save, a.library, plan["world"], protect=[a.own_save])
        game = ControlGame(a.game_port, n, a.list, a.dlc) if a.game_port else None
        out = asyncio.run(join_session(
            a.library, a.name, plan["address"], folder, sl, game=game, dlc=a.dlc, code=plan["code"],
            port=plan["port"], share=plan["share"],
            before_game=lambda: print(f"Ready: start the game with SAVAGEWIND_SAVE_DIR={folder} and Continue")))
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as e:
        print(f"Stopped: {e}")
        return 1
    if out["error"]:
        print(f"Couldn't join: {out['error']}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Co-op agent: host or join a world (home network), shared time and story.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", help="list hosts on your home network")
    f.add_argument("--seconds", type=float, default=3.0)
    c = sub.add_parser("copy", help="make your co-op copy of your own save (your save is only read)")
    c.add_argument("--own-save", required=True, type=Path)
    c.add_argument("--library", required=True, type=Path)
    c.add_argument("--world", required=True, help="the host's world name (a friend) or your own (a host)")
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
    h.add_argument("--story-from", type=Path, help="no game: send the story of this save folder (the bot as host)")
    h.add_argument("--time", type=float, help="no game: send this hour")
    h.add_argument("--friends-story", action="store_true", help="friends' new story progress counts in your world")
    j = sub.choices["join"]
    j.add_argument("--address", help="the host's address (default: the first host found)")
    j.add_argument("--save-dir", required=True, type=Path, help="your CO-OP save copy (never your own save)")
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
            print("No host found on your home network.")
        return 0
    if a.cmd == "copy":
        from mp import worlds
        make = worlds.host_world if a.host else worlds.joined_copy
        try:
            print(make(a.own_save, a.library, a.world, protect=[a.own_save]))
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
        return menu(a, sl, n)
    if a.cmd == "host":
        if a.game_port:
            game = ControlGame(a.game_port, n, a.list, a.dlc)
        else:
            files = story.slot_files(a.story_from) if a.story_from else []
            bits = story.story_bits(max(files, key=lambda p: p.stat().st_mtime), sl.names(a.dlc)) if files else set()
            game = FakeGame(a.time, bits)
        try:
            asyncio.run(host_session(a.library, a.name, game, sl, dlc=a.dlc, code=a.code, port=a.port,
                                     discovery_port=discovery.DISCOVERY_PORT, friends_story=a.friends_story))
        except KeyboardInterrupt:
            pass
        return 0
    address = a.address
    if not address:
        hosts = discovery.search(3.0)
        if not hosts:
            print("No host found on your home network: give --address.")
            return 1
        address, a.port = hosts[0]["address"], hosts[0]["port"]
        print(f"Joining {hosts[0]['world']} ({address}; check words {hosts[0]['check']})")
    game = ControlGame(a.game_port, n, a.list, a.dlc) if a.game_port else None
    try:
        out = asyncio.run(join_session(a.library, a.name, address, a.save_dir, sl, game=game, dlc=a.dlc, code=a.code,
                                       port=a.port, share=a.share))
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
