"""The Multiplayer menu (docs/coop.md): Host a world / Join a world / Play solo, with no typing.

    Multiplayer
      1  Host a world      your friends join you
      2  Join a world      Ana's world  -  Ana  -  2 of 8 players  -  needs a room code
      3  Play solo         your own save
      4  Who am I          Zelda   (your friends see you as this)

Keys: 1 2 3 4, or Up/Down to move the big > and Enter (Space) to pick; Esc on the first screen is Play solo, and
Esc (Backspace) on the others goes back. A controller does the same with the d-pad (or the left stick) and A; its B
goes back, and on the first screen B does nothing (it never starts Play solo by itself). Join lists the hosts found
on your home network (tools/mp/discovery.py) every second; a room code is picked with three word wheels (up/down
change a word, left/right move between wheels); then the story question (Enter = Yes, Esc = No). Who am I is the
character wheel (tools/charsel.py Wheel, docs/charsel.md): Left/Right the next character, Up/Down the next group,
Enter picks (Lobby.look; choose() keeps it for the multiplayer client), Esc goes back unchanged.

`Lobby` is the menu as a state machine (no window: the launcher's window and the in-game screen draw its `lines()`
and send it keys). `choose()` shows it in a small window and returns the plan:
    {"mode": "solo"} | {"mode": "host"} | {"mode": "join", "address", "port", "world", "code", "share", "check"}
("check": the host's check words: the friend's co-op copy is kept per host by them.)
The launcher (another job's file) is to call choose() before Play when the multiplayer mod is on.
"""
from __future__ import annotations

import threading

from . import discovery, security


MAIN = ("1", "2", "3", "4")   # the first screen's lines, in order (the cursor's places)


class Lobby:
    """Keys: '1'-'4', 'up' 'down' 'left' 'right' 'enter' 'esc', and 'back' (a controller's B, Backspace): 'esc'
    everywhere but the first screen, where it does nothing."""

    def __init__(self, look: str = "link", built: dict | None = None, linkle: bool | None = None):
        self.look = look                 # Who am I: your pick (a charsel id)
        self.built, self.linkle = built, linkle   # what this PC's game has (charsel catalog), for the wheel's words
        self.who = None                  # the Who am I wheel while it is open (charsel.Wheel)
        self.screen = "main"
        self.cur = 0                     # the first screen's cursor (the big >): an index into MAIN
        self.hosts: list[dict] = []
        self.sel = 0
        self.wheels = [0, 0, 0]
        self.wheel = 0                   # the room code: which of the three word wheels is lit
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
        """One key: '1' '2' '3' '4' 'up' 'down' 'left' 'right' 'enter' 'esc' 'back'. Returns the plan once chosen."""
        s = self.screen
        if k == "back":
            if s == "main":
                return self.plan          # (a controller's B on the first screen: nothing, never Play solo)
            k = "esc"
        if s == "main":
            if k in ("up", "down"):
                self.cur = (self.cur + (1 if k == "down" else -1)) % len(MAIN)
                return self.plan
            if k == "enter":
                k = MAIN[self.cur]
            elif k in MAIN:
                self.cur = MAIN.index(k)
            if k == "1":
                self.plan = {"mode": "host"}
            elif k == "3" or k == "esc":
                self.plan = {"mode": "solo"}
            elif k == "2":
                self.screen, self.sel = "join", 0
            elif k == "4":
                import charsel
                self.who = charsel.Wheel(self.look, self.built)
                self.who.linkle = self.linkle
                self.screen = "who"
        elif s == "who":
            got = self.who.key(k) if self.who is not None else ""
            if got is not None:
                if got:
                    self.look = got
                self.screen, self.who = "main", None
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
            rows = ["1  Host a world      your friends join you", f"2  Join a world      {join}",
                    "3  Play solo         your own save",
                    f"4  Who am I          {who}   (your friends see you as this)"]
            return ["Multiplayer"] + [f" {'>' if i == self.cur else ' '} {r}" for i, r in enumerate(rows)] + [
                "  Up/Down and Enter, or 1-4"]
        if self.screen == "who" and self.who is not None:
            return self.who.lines()
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


# (charsel.TK_KEYS's keys too; Backspace is 'back' here: on the first screen it never starts Play solo)
KEYS = {"Return": "enter", "KP_Enter": "enter", "space": "enter", "Escape": "esc", "BackSpace": "back", "Up": "up",
        "Down": "down", "Left": "left", "Right": "right", "1": "1", "2": "2", "3": "3", "4": "4", "KP_1": "1",
        "KP_2": "2", "KP_3": "3", "KP_4": "4", "y": "y", "n": "n"}


class LobbyWindow:
    """The menu in a Tk window: big text, and on Who am I the drawn wheel (charsel.WheelWindow) in its place. Keys
    and a controller (charsel.Pad: d-pad or left stick, A, B) go to the Lobby; on_plan gets the plan once chosen."""

    def __init__(self, root, lobby: Lobby, on_plan=None, pad=None):
        import tkinter as tk
        self.tk, self.root, self.lobby, self.on_plan, self.pad = tk, root, lobby, on_plan, pad
        self.label = tk.Label(root, font=("Segoe UI", 18), justify="left", anchor="w", padx=24, pady=24)
        self.label.pack(fill="both", expand=True)
        self.frame = self.win = None
        root.bind("<Key>", self._on_key)
        self.draw()

    def _on_key(self, ev) -> None:
        k = KEYS.get(ev.keysym)
        if k:
            self.press(k)

    def press(self, k: str) -> None:
        plan = self.lobby.key(k)
        self.draw()
        if plan is not None and self.on_plan is not None:
            self.on_plan(plan)

    def poll_pad(self) -> None:
        if self.pad is not None and self.lobby.plan is None:
            for k in self.pad.keys():
                self.press("back" if k == "esc" else k)   # (B: back, never Play solo on the first screen)
            self.root.after(50, self.poll_pad)

    def draw(self) -> None:
        import charsel
        lb = self.lobby
        if lb.screen == "who" and lb.who is not None:
            if self.win is None or self.win.wheel is not lb.who:
                if self.frame is not None:
                    self.frame.destroy()
                self.label.pack_forget()
                self.frame = self.tk.Frame(self.root)
                self.frame.pack(fill="both", expand=True)
                self.win = charsel.WheelWindow(self.frame, lb.who, scale=charsel.screen_scale(self.root))
            else:
                self.win.draw()
            return
        if self.frame is not None:
            self.frame.destroy()
            self.frame = self.win = None
            self.label.pack(fill="both", expand=True)
        self.label.config(text="\n".join(lb.lines()))


def choose(timeout_s: float | None = None, search=discovery.search) -> dict:
    """Show the Multiplayer menu in a small window (big text) and return the plan. Esc on the first screen = solo. A
    new Who am I pick is kept (charsel.save_choice) for the multiplayer client."""
    import tkinter as tk
    import charsel
    choice = charsel.load_choice()
    cat, _where = charsel.here_catalog(choice["packs"])   # (what this PC's game really has: deployed, enabled)
    lobby = Lobby(choice["look"], cat.get("built") if cat else None,
                  (cat.get("linkle") or {}).get("how") == "link-body" if cat else None)
    root = tk.Tk()
    root.title("Multiplayer")
    win = LobbyWindow(root, lobby, on_plan=lambda plan: root.quit(), pad=charsel.Pad())
    win.poll_pad()
    stop = threading.Event()
    found: list[list[dict]] = [[]]

    def finder():
        while not stop.is_set():
            try:
                found[0] = search(1.0)
            except OSError:
                stop.wait(1.0)

    threading.Thread(target=finder, daemon=True).start()

    def refresh():
        lobby.set_hosts(found[0])
        if lobby.screen != "who":   # (the wheel redraws on its own keys)
            win.draw()
        root.after(1000, refresh)

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
