#!/usr/bin/env python3
"""Client for Savage Wind's control connection (start the game with SAVAGEWIND_CONTROL=<port>).

The game listens on 127.0.0.1 only. One text line per request, one JSON line per reply; every request
runs on the next presented frame, so a reply can take one frame. Several requests can be sent at once
with `batch`, and they all run in that same frame.

    from control import Control
    with Control(7777) as c:
        print(c.cmd("pos"))                                  # {'x': ..., 'y': ..., 'z': ..., ...}
        ptr = c.read_u64(0x7001e13ed0)
        c.batch([["mem", "read", hex(ptr + 0xa4), "4", "f32"], ["state"]])

Protocol: upstream docs/CONTROL.md (Revivedx/SavageWindRecomp, MIT).
"""
from __future__ import annotations

import json
import socket
import time

DEFAULT_PORT = 7777
MAIN_BASE = 0x7000000000
ALIAS_BASE = 0x7100000000
HEAP_BASE = 0x7240000000
GUEST_LO, GUEST_HI = 0x7000000000, 0x7320000000


class ControlError(RuntimeError):
    """The game answered with an error, or the connection failed."""


def quote(word) -> str:
    """Quote one argument for the control protocol (not a shell)."""
    word = str(word)
    if any(c in word for c in "\r\n\0"):
        raise ValueError("arguments can't contain a newline or NUL")
    if not word or any(c.isspace() or c in '"\\' for c in word):
        word = '"' + word.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return word


def request_line(words) -> bytes:
    if isinstance(words, str):
        words = [words]
    return (" ".join(quote(w) for w in words) + "\n").encode("utf-8")


class Control:
    def __init__(self, port: int = DEFAULT_PORT, host: str = "127.0.0.1", timeout: float = 10.0,
                 wait: float = 0.0):
        """Connect to the game. `wait` retries for that many seconds while the game is still starting."""
        self.timeout = timeout
        deadline = time.monotonic() + wait
        while True:
            try:
                self.sock = socket.create_connection((host, port), timeout=timeout)
                break
            except OSError as e:
                if time.monotonic() >= deadline:
                    raise ControlError(f"can't connect to the game on {host}:{port} ({e})") from None
                time.sleep(0.25)
        self.sock.settimeout(timeout)
        self._buf = b""

    # -- plumbing --

    def _read_line(self) -> bytes:
        while b"\n" not in self._buf:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                raise ControlError("the game didn't answer in time") from None
            except OSError as e:  # reset / aborted: the game closed or restarted the connection
                raise ControlError("the game closed the connection") from e
            if not chunk:
                raise ControlError("the game closed the connection")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line

    def _reply(self) -> dict:
        try:
            reply = json.loads(self._read_line().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ControlError(f"invalid reply from the game: {e}") from None
        if not reply.get("ok"):
            raise ControlError(reply.get("error", "unknown error"))
        return reply.get("data", {})

    def cmd(self, *words) -> dict:
        """Run one command. Words may be given separately or as one list."""
        if len(words) == 1 and isinstance(words[0], (list, tuple)):
            words = words[0]
        self._send(request_line(list(words)))
        return self._reply()

    def _send(self, data: bytes) -> None:
        try:
            self.sock.sendall(data)
        except OSError as e:
            raise ControlError("the game closed the connection") from e

    def batch(self, requests, *, errors: bool = False) -> list:
        """Send several commands at once; they run in the same frame. Returns their data in order.

        With errors=True a failed command gives a ControlError object in its slot instead of raising.
        """
        lines = [request_line(r) for r in requests]
        if not lines:
            return []
        self._send(b"".join(lines))
        out = []
        for _ in lines:
            try:
                out.append(self._reply())
            except ControlError as e:
                if not errors:
                    # drain the remaining replies so the connection stays usable
                    for _ in range(len(lines) - len(out) - 1):
                        try:
                            self._reply()
                        except ControlError:
                            pass
                    raise
                out.append(e)
        return out

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- memory helpers (addresses are guest addresses; guest == host in Savage Wind) --

    TYPES = {"u8": 1, "u16": 2, "u32": 4, "u64": 8, "s32": 4, "f32": 4, "f64": 8}

    def read(self, addr: int, kind: str = "u32", count: int = 1):
        width = self.TYPES[kind]
        data = self.cmd("mem", "read", hex(addr), str(width * count), kind)
        vals = [data[f"value.{i}"] for i in range(count)]
        conv = float if kind.startswith("f") else int
        vals = [conv(v) for v in vals]
        return vals[0] if count == 1 else vals

    def read_u64(self, addr: int) -> int:
        return self.read(addr, "u64")

    def read_bytes(self, addr: int, size: int) -> bytes:
        data = self.cmd("mem", "read", hex(addr), str(size), "hex")
        return bytes.fromhex(data["hex"].replace(" ", ""))

    def write(self, addr: int, kind: str, value) -> None:
        self.cmd("mem", "write", hex(addr), kind, str(value))

    def pos(self) -> tuple[float, float, float]:
        d = self.cmd("pos")
        return float(d["x"]), float(d["y"]), float(d["z"])


def wait_for_game(port: int = DEFAULT_PORT, timeout: float = 120.0) -> Control:
    """Wait until the game's control connection answers `ping`."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        c = None
        try:
            c = Control(port, timeout=5.0)
            c.cmd("ping")
            return c
        except ControlError as e:
            last = e
            if c is not None:
                c.close()
            time.sleep(1.0)
    raise ControlError(f"the game didn't open its control connection in {timeout:.0f} s ({last})")
