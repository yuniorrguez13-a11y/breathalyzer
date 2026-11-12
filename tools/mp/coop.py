"""Co-op followers (docs/coop.md): what a friend's agent does with the host's time and story.

They hold no sockets and touch no game: each takes what the host sent and what the friend's game says, and answers
what to do (a `time set` hour, the story bits to add). The agent runs the game commands.
"""
from __future__ import annotations

TIME_SLACK_H = 2.0 / 60.0                       # 2 game minutes: less than this is left alone
BUSY_STATUS = frozenset({"menu", "cutscene", "event", "loading", "title", "paused", "busy"})


def hour_gap(a: float, b: float) -> float:
    """The shortest distance between two hours of the day (21.9 and 0.1 are 0.2 apart)."""
    d = abs(a - b) % 24.0
    return min(d, 24.0 - d)


class TimeFollower:
    """Follows the host's hour. `decide(my_hour, status)` gives the hour to `time set`, or None."""

    def __init__(self, host_id: int | None = None):
        self.host_id = host_id
        self.host_hour: float | None = None

    def on_time(self, msg: dict) -> bool:
        """Take a relayed `time` line. Only the host's (from == host id) counts; returns True if taken."""
        if self.host_id is None or msg.get("from") != self.host_id:
            return False
        h = msg.get("h")
        if isinstance(h, (int, float)) and not isinstance(h, bool) and 0.0 <= h < 24.0:
            self.host_hour = float(h)
            return True
        return False

    def decide(self, my_hour: float | None, status: str | None) -> float | None:
        if self.host_hour is None or my_hour is None:
            return None
        if status is None or status in BUSY_STATUS:
            return None
        if hour_gap(my_hour, self.host_hour) < TIME_SLACK_H:
            return None
        return self.host_hour


class StoryFollower:
    """The host's story on a friend's side: which bits the friend's game still lacks. Never removes anything: a bit
    the host loses (he loaded an older save) stays with the friend. `names` (the story list): to_add() then gives
    only what a RUNNING game may get (story.live_ok: towers and memories, settled); without names nothing goes live."""

    def __init__(self, n: int, list_hash: str, host_id: int | None = None, names: list[str] | None = None):
        self.n, self.list_hash, self.host_id = n, list_hash, host_id
        self.names = names
        self.host_bits: set[int] = set()
        self.seq = -1
        self.why_off: str | None = None
        self.changed = False   # new bits since the last merge (the agent clears it)

    def on_story(self, msg: dict) -> bool:
        from .story import decode_bits
        if self.host_id is None or msg.get("from") != self.host_id:
            return False
        if msg.get("list") != self.list_hash:
            self.why_off = "the host's game has another story list (another version or DLC): story is off"
            return False
        seq = msg.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq <= self.seq:
            return False
        bits = decode_bits(msg.get("bits"), self.n)
        if bits is None:
            return False
        self.seq = seq
        if not bits <= self.host_bits:
            self.changed = True
        self.host_bits |= bits     # (a union: what the host once had stays)
        return True

    def new_host(self, host_id: int | None) -> None:
        """The host came back with a new id (his agent reconnected): his next line starts a new seq."""
        if host_id != self.host_id:
            self.host_id, self.seq = host_id, -1

    def to_add(self, mine: set[int]) -> set[int]:
        """The bits to add to the friend's RUNNING game: settled, live-safe ones only (the rest waits for the next
        join's file merge)."""
        if self.names is None:
            return set()
        from .story import live_ok, settle
        return {i for i in settle(self.names, self.host_bits) if live_ok(self.names[i])} - set(mine)
