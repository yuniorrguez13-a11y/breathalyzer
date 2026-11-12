"""The Multiplayer menu (docs/coop.md): Host a world / Join a world / Play solo, with no typing.

    Multiplayer
      1  Host a world      your friends join you
      2  Join a world      Ana's world  -  Ana  -  2 of 8 players  -  needs a room code
      3  Play solo         your own save
      4  Who am I          Zelda   (your friends see you as this)

Keys: 1 2 3 4, arrows, Enter, Esc (a controller's d-pad and A/B map to the same). Join lists the hosts found on your
home network (tools/mp/discovery.py) every second; a room code is picked with three word wheels (up/down change a
word, left/right move between wheels); then the story question (Enter = Yes, Esc = No). Who am I is the character
wheel (tools/charsel.py Wheel, docs/charsel.md): Left/Right the next character, Up/Down the next group, Enter picks
(Lobby.look; choose() keeps it for the multiplayer client), Esc goes back unchanged.

`Lobby` is the menu as a state machine (no window: the launcher's window and the in-game screen draw its `lines()`
and send it keys). `choose()` shows it in a small window and returns the plan:
    {"mode": "solo"} | {"mode": "host"} | {"mode": "join", "address", "port", "world", "code", "share", "check"}
("check": the host's check words: the friend's co-op copy is kept per host by them.)
The launcher (another job's file) is to call choose() before Play when the multiplayer mod is on.
"""
from __future__ import annotations

import threading

from . import discovery, security


class Lobby:
    def __init__(self, look: str = "link", built: dict | None = None, linkle: bool | None = None):
        self.look = look                 # Who am I: your pick (a charsel id)
        self.built, self.linkle = built, linkle   # what this PC built (charsel catalog), for the wheel's words
        self.wheel = None                # the Who am I wheel while it is open (charsel.Wheel)
        self.screen = "main"
        self.hosts: list[dict] = []
        self.sel = 0
        self.wheels = [0, 0, 0]
        self.wheel = 0
        self.plan: dict | None = None
        self.host: dict | None = None

    def set_hosts(self, hosts: list[dict]) -> None:
        keep = self.hosts[self.sel] if self.screen == "join" and self.sel < len(self.hosts) else None
        self.hosts = list(hosts)
        if keep is not None:
            same = [i for i, h in enumerate(self.hosts) if (h["address"], h["port"]) == (keep["address"], keep["port"])]
            self.sel = same[0] if same else 0
        self.sel = min(self.sel, max(0, len(self.hosts) - 1))

    def code(self) -> str:
        return " ".join(security.WORDS[i] for i in self.wheels)

    def key(self, k: str) -> dict | None:
        """One key: '1' '2' '3' 'up' 'down' 'left' 'right' 'enter' 'esc'. Returns the plan once chosen."""
        s = self.screen
        if s == "main":
            if k == "1":
                self.plan = {"mode": "host"}
            elif k == "3" or k == "esc":
                self.plan = {"mode": "solo"}
            elif k == "2":
                self.screen, self.sel = "join", 0
            elif k == "4":
                import charsel
                self.wheel = charsel.Wheel(self.look, self.built)
                self.wheel.linkle = self.linkle
                self.screen = "who"
        elif s == "who":
            got = self.wheel.key(k) if self.wheel is not None else ""
            if got is not None:
                if got:
                    self.look = got
                self.screen, self.wheel = "main", None
        elif s == "join":
            if k == "esc":
                self.screen = "main"
            elif k in ("up", "down") and self.hosts:
                self.sel = (self.sel + (1 if k == "down" else -1)) % len(self.hosts)
            elif k == "enter" and self.hosts:
                self.host = self.hosts[self.sel]
                self.screen = "code" if self.host.get("code") else "story"
        elif s == "code":
            if k == "esc":
                self.screen = "join"
            elif k in ("left", "right"):
                self.wheel = (self.wheel + (1 if k == "right" else -1)) % 3
            elif k in ("up", "down"):
                self.wheels[self.wheel] = (self.wheels[self.wheel] + (1 if k == "down" else -1)) % len(security.WORDS)
            elif k == "enter":
                self.screen = "story"
        elif s == "story":
            if k in ("enter", "esc", "y", "n"):
                h = self.host
                self.plan = {"mode": "join", "address": h["address"], "port": h["port"], "world": h["world"],
                             "code": self.code() if h.get("code") else None, "share": k in ("enter", "y"),
                             "check": h.get("check") or None}
        if self.plan is not None:
            self.screen = "done"
        return self.plan

    def lines(self) -> list[str]:
        if self.screen == "main":
            first = self.hosts[0] if self.hosts else None
            join = (f"{first['world']}  -  {first['name']}  -  {first['players']} of {first['max']} players"
                    + ("  -  needs a room code" if first["code"] else "")) if first else "looking for hosts..."
            import charsel
            who = charsel.RECIPES[self.look].name.split(" (")[0] if self.look in charsel.RECIPES else "Link"
            return ["Multiplayer", "  1  Host a world      your friends join you", f"  2  Join a world      {join}",
                    "  3  Play solo         your own save", f"  4  Who am I          {who}   (your friends see you as this)"]
        if self.screen == "who" and self.wheel is not None:
            return self.wheel.lines()
        if self.screen == "join":
            if not self.hosts:
                return ["Join a world", "  Looking for hosts on your home network...", "  Esc: back"]
            out = ["Join a world"]
            for i, h in enumerate(self.hosts):
                mark = ">" if i == self.sel else " "
                out.append(f" {mark} {h['world']}  -  {h['name']}  -  {h['players']} of {h['max']}"
                           + ("  -  needs a room code" if h["code"] else "") + f"   [{h['check']}]")
            return out + ["  Enter: join   Esc: back"]
        if self.screen == "code":
            words = [w.upper() if i == self.wheel else w for i, w in enumerate(self.code().split())]
            return ["Room code", "  " + "   ".join(words), "  Up/Down: change   Left/Right: next word   Enter: OK"]
        if self.screen == "story":
            name = self.host["name"] if self.host else "the host"
            from .story import story_question
            return story_question(name)
        return ["Starting..."]


def choose(timeout_s: float | None = None, search=discovery.search) -> dict:
    """Show the Multiplayer menu in a small window (big text) and return the plan. Esc on the first screen = solo. A
    new Who am I pick is kept (charsel.save_choice) for the multiplayer client."""
    import tkinter as tk
    import charsel
    choice = charsel.load_choice()
    cat = charsel.load_catalog(choice["packs"] or charsel.DEFAULT_OUT)
    lobby = Lobby(choice["look"], cat.get("built") if cat else None,
                  (cat.get("linkle") or {}).get("how") == "link-body" if cat else None)
    root = tk.Tk()
    root.title("Multiplayer")
    label = tk.Label(root, font=("Segoe UI", 18), justify="left", anchor="w", padx=24, pady=24)
    label.pack(fill="both", expand=True)
    stop = threading.Event()
    found: list[list[dict]] = [[]]

    def finder():
        while not stop.is_set():
            try:
                found[0] = search(1.0)
            except OSError:
                stop.wait(1.0)

    threading.Thread(target=finder, daemon=True).start()
    names = {"Return": "enter", "Escape": "esc", "Up": "up", "Down": "down", "Left": "left", "Right": "right",
             "1": "1", "2": "2", "3": "3", "4": "4", "y": "y", "n": "n", "BackSpace": "esc"}

    def on_key(ev):
        k = names.get(ev.keysym)
        if k and lobby.key(k) is not None:
            root.quit()
        draw()

    def draw():
        label.config(text="\n".join(lobby.lines()))

    def refresh():
        lobby.set_hosts(found[0])
        draw()
        root.after(1000, refresh)

    root.bind("<Key>", on_key)
    refresh()
    if timeout_s:
        root.after(int(timeout_s * 1000), root.quit)
    try:
        root.mainloop()
    finally:
        stop.set()
        root.destroy()
    if lobby.look != choice["look"]:
        try:
            charsel.save_choice(lobby.look)
        except (OSError, charsel.CharselError):
            pass
    return lobby.plan or {"mode": "solo"}
