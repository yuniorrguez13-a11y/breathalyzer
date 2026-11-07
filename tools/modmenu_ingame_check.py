#!/usr/bin/env python3
"""Check the Mod menu in a running game, as far as a script can. Run it only when the game may be used.

    python tools/modmenu_ingame_check.py [--library <folder>] [--install <Savage Wind folder>] [--port N]

It never starts or stops the game, and it changes nothing: it reads savagewind.log and the library's menu files, and,
when the game listens on its control port (SAVAGEWIND_CONTROL), asks it `mods` and `modmenu` (both only read).

  1. savagewind.log, last start: the Mod menu's own line ("[mod] modmenu: Mods page on (N mods, path A+)" or "off:
     <why>"), modhost's line for modmenu.dll, and Savage Wind's "[options] ... pages added" (once Options was open).
  2. The control port: `modmenu` answers, the page is on, and the game lists the mods menu.txt lists.
  3. The changes waiting in pending.txt.
  4. What is left to check by hand (docs/mod-menu.md, "Game test checklist").

Exit code: 0 every check that could run passed, 1 one failed, 2 nothing to check (no savagewind.log).
"""
from __future__ import annotations

import argparse
import os
import re
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import modman as mm  # noqa: E402
import modmenu  # noqa: E402

LOG = "savagewind.log"
LOG_TAIL = 4 << 20   # the last 4 MB of the log is plenty for one start
MENU_ON_RE = re.compile(r"\[mod\] modmenu: Mods page on \((\d+) mods?, path ([AB+]+)")
MENU_OFF_RE = re.compile(r"\[mod\] modmenu: Mods page off: (.*)")
LOADED_RE = re.compile(r"\[modhost\] mod modmenu\.dll: (.*)")
SW_PAGES = "[options] Savage Wind's pages added"
CRASH_WORDS = ("access violation", "unhandled exception", "crashed")
BY_HAND = [
    "Pause > System > Options: Mods is just above Quit game.",
    "Every Savage Wind page still works (Video and Window size, Keyboard key capture, Language): keys, controller, "
    "mouse.",
    "Mods page: turn a mod off, B, open again: still OFF. Y: back to ON.",
    "Order page: left/right moves a mod, the other numbers change.",
    "Mouse: click an ON/OFF, click Mods, scroll a long list.",
    "Title screen > Options: the same.",
    "Language > Restart now, then Options again: Mods is still there.",
    "Close the game, run python tools/modmenu.py play: the change is in.",
]


def read_log(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - LOG_TAIL))
            data = f.read()
    except OSError:
        return None
    return data.decode("utf-8", errors="replace")


def last_start(text: str) -> list[str]:
    """The log lines since modhost last started (the whole tail when it didn't)."""
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.startswith("[modhost] modhost ")]
    return lines[starts[-1]:] if starts else lines


def check_log(lines: list[str]) -> list[tuple[str, str]]:
    """(PASS / FAIL / INFO, text) for the log of one start."""
    out = []
    on = next((m for ln in lines if (m := MENU_ON_RE.search(ln))), None)
    off = next((m for ln in lines if (m := MENU_OFF_RE.search(ln))), None)
    loaded = next((m for ln in lines if (m := LOADED_RE.search(ln))), None)
    if not any(ln.startswith("[modhost] modhost ") for ln in lines):
        out.append(("FAIL", "modhost didn't start: the game wasn't started with mods (use Play in Mod Manager)."))
    elif not loaded:
        out.append(("FAIL", "modmenu.dll wasn't loaded: turn Mod menu on in Mod Manager, deploy, start again."))
    elif not loaded.group(1).startswith("ok"):
        out.append(("FAIL", f"modmenu.dll: {loaded.group(1)}"))
    if on:
        out.append(("PASS", f"Mod menu is on: {on.group(1)} mods, path {on.group(2)}."))
    elif off:
        out.append(("FAIL", f"Mod menu is off: {off.group(1)}"))
    if any(SW_PAGES in ln for ln in lines):
        out.append(("PASS", "Savage Wind's pages were added (Options was opened)."))
    else:
        out.append(("INFO", "Options wasn't opened yet in this start (no \"[options] ... pages added\" line)."))
    crash = [ln for ln in lines if any(w in ln.lower() for w in CRASH_WORDS)]
    if crash:
        out.append(("FAIL", f"The log mentions a crash: {crash[-1][:120]}"))
    return out


def check_game(reply: dict, menu: modmenu.Menu | None) -> list[tuple[str, str]]:
    """(PASS / FAIL / INFO, text) for the game's answer to `modmenu`."""
    out = []
    if not reply.get("on"):
        return [("FAIL", f"The game says the Mod menu is off: {reply.get('why') or '?'}")]
    out.append(("PASS", f"The game answers modmenu: on, path {reply.get('path')}, {reply.get('count')} mods, "
                        f"{reply.get('changes')} change(s) waiting."))
    game = [reply.get(f"mods.{i}.id") for i in range(int(reply.get("count") or 0))]
    if menu is None:
        out.append(("INFO", "The library has no menu.txt to compare with."))
    else:
        listed = [m.id for m in menu.mods if "hidden" not in m.flags]
        if sorted(game) == sorted(listed[:modmenu_max()]):
            out.append(("PASS", "The game lists the same mods as menu.txt."))
        else:
            out.append(("FAIL", f"The game lists other mods than menu.txt (game: {', '.join(game[:6])}...). "
                                "Was the library changed after the game started?"))
    if reply.get("list") != "ok":
        out.append(("FAIL", f"The game's mod list is {reply.get('list')}."))
    if reply.get("stale"):
        out.append(("INFO", "The game says its list may be old (Mod Manager changed the library since)."))
    return out


def modmenu_max() -> int:
    return 64  # (native/examples/modmenu/opt_page.h MM_MAX_MODS)


def ask_game(port: int) -> tuple[dict | None, str]:
    from control import Control, ControlError  # (tools/control.py)
    try:
        with Control(port, timeout=5.0) as c:
            mods = c.cmd("mods")
            if not any(str(v) == "modmenu.dll" for k, v in mods.items() if k.endswith(".file")):
                return None, "modhost runs, but modmenu.dll isn't among its mods."
            return c.cmd("modmenu"), ""
    except (OSError, ControlError) as e:
        return None, f"No answer on port {port} ({e}). Start the game with a control port to check more."


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="modmenu_ingame_check.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--library", help="the mod library folder (as for modman.py)")
    ap.add_argument("--install", help="your Savage Wind folder (default: the library's, else the usual place)")
    ap.add_argument("--port", type=int, default=mm.DEFAULT_PORT, help="the game's control port (default 7777)")
    a = ap.parse_args(argv)
    lib = mm.Library.load(mm.resolve_library(a.library))
    install = a.install or (lib.install if lib else None) or mm.default_install()
    data_root = os.environ.get("SAVAGEWIND_DATA_ROOT") or install
    results: list[tuple[str, str]] = []
    text = read_log(os.path.join(data_root, LOG))
    if text is None:
        print(f"No {LOG} in {data_root}: nothing to check. Start the game with Play in Mod Manager first.")
        return 2
    results += check_log(last_start(text))
    reply, why = ask_game(a.port)
    menu = modmenu.read_menu(lib) if lib else None
    if reply is not None:
        results += check_game(reply, menu)
    else:
        results.append(("INFO", why))
    if lib and modmenu.has_pending(lib):
        n = sum(1 for r in modmenu.rows(lib) if r.changed)
        results.append(("INFO", f"{n} change(s) from the game wait in pending.txt (python tools/modmenu.py show)."))
    failed = [r for r in results if r[0] == "FAIL"]
    print("Mod menu check: " + ("FAILED" if failed else "nothing failed") + f" ({len(results)} checks).")
    for kind, msg in results:
        print(f"{kind:<5} {msg}")
    print("Check by hand (docs/mod-menu.md, Game test checklist):")
    for step in BY_HAND:
        print(f"  - {step}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
