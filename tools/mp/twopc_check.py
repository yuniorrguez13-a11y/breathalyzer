"""Two-PC test kit: a guided checker each player runs on their own PC (a console, no windows).

    python tools\\mp\\twopc_check.py                  the whole test: checks, the connection both ways, the game steps
    python tools\\mp\\twopc_check.py --dry-run        only print the steps (nothing is checked, nothing is opened)
    python tools\\mp\\twopc_check.py listen [--port N]          the connection test, this PC waits
    python tools\\mp\\twopc_check.py connect ADDRESS [--port N] the connection test, this PC calls the other one

What it checks on this PC: Savage Wind's version (0.4.1-alpha.4, read from the exe like the launcher does), the mod
library (found, readable, Ghost multiplayer on, Infinite Stamina off, a save to copy), this PC's home address, the
Wi-Fi set to Private, the port free. Then between the two PCs: a tiny TCP echo on the port (7788, the one Host uses)
both ways, the delay (latency) and how far apart the two clocks are, the same Savage Wind and the same mods for
multiplayer (the friend's list comes over the echo). Then it walks both players through the game steps (host, join,
see each other, held items, story at join, towers and memories live, stop, own save) and records PASS / FAIL / SKIP
and notes into a JSON and a short text report (in Documents\\Breathalyzer two-PC test) both players can send.

The echo is ours only: one JSON line each way ({"t":"brz2pc",...}, at most 16 KB), home-network addresses only (like
the relay), at most 20 lines per connection, the listener closes after one good friend or 3 minutes. Nothing is sent
anywhere else; nothing is downloaded; the game and Breathalyzer are not started or changed.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import ipaddress
import json
import os
import re
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

TOOL = "twopc_check"
FORMAT = 1
SUPPORTED = "0.4.1-alpha.4"          # (launcher.SUPPORTED)
EXE = "savagewind.exe"
DEFAULT_PORT = 7788                  # (mp.DEFAULT_RELAY_PORT: Host's world listens here, TCP)
MAX_LINE = 16384
MAX_LINES = 20                       # per connection
MAX_EXE = 512 * 1024 * 1024
PINGS = 10
LISTEN_SECONDS = 180
CONNECT_RETRY_SECONDS = 20
GHOST_ID = "botw-mod-loader.ghost"
CHARS_ID = "botw-mod-loader.characters"
STAMINA_ID = "botw-mod-loader.basics-inf-stamina"
MUST_MATCH = (GHOST_ID, CHARS_ID)    # different versions of these break multiplayer
LATENCY_OK_MS = 30.0
LATENCY_BAD_MS = 150.0
CLOCK_OK_S = 5.0
RESULTS = ("PASS", "FAIL", "WARN", "SKIP", "INFO")

ANCHOR_RE = re.compile(rb"-(?:alpha|beta|rc)\.\d{1,3}(?![0-9])")
VERSION_RE = re.compile(rb"(\d\.\d{1,3}\.\d{1,3}-(?:alpha|beta|rc)\.\d{1,3})$")

# The game steps. who: "A" (the host), "B" (the friend) or "both" does it; everyone answers for what they see.
STEPS = [
    ("ready", "both", "Get ready",
     "Both PCs: Breathalyzer open, Home > Multiplayer > Ghost multiplayer: Turn on. Who am I: Build the characters, "
     "then pick one (not Link: he is a stand-in). Same Wi-Fi, set to Private."),
    ("host", "A", "Host",
     "PC A: press Host, type a name. At 'Your world is open', if Windows asks about Python: Allow. "
     "Then Start the game, and Continue in the game."),
    ("join", "B", "Join",
     "PC B: press Join (Allow if Windows asks). The check words must match the ones on A's page. "
     "Pick A's world, Story together: Yes. Then Continue in the game."),
    ("see", "both", "See each other",
     "A's page says B joined. Walk close: each of you sees the other as the picked character. "
     "Run and jump: the other one moves the same way, with little delay."),
    ("items", "both", "Held items",
     "Each takes out a sword, a shield and a bow. The other one sees them in the hands. "
     "Then one attack with a real weapon (not on each other): the other one sees the swing."),
    ("story_join", "B", "Story at join",
     "PC B: open the map. You have A's towers, A's quests, A's freed Divine Beasts and A's shrine warps."),
    ("towers_live", "A", "Tower live",
     "PC A: activate a tower B doesn't have (or the closest one). PC B: within a minute it shows on your map "
     "(you may need to close and open the map)."),
    ("memories_live", "A", "Memory live",
     "PC A: find a memory B doesn't have (if one is close; else Skip). PC B: it shows in your Album / memories."),
    ("stop", "A", "Stop",
     "PC A: press Stop on the Multiplayer page. PC B: your page says the session ended."),
    ("own_save", "both", "Own save unchanged",
     "Both: close the game. Press Play (not Multiplayer) and load your own save once: it is like before."),
]


# ------------------------------------------------------------------ small helpers

def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def home_address(ip) -> bool:
    """A home-network address (private, link-local, loopback, or a virtual-LAN tool's 100.64/10), like the relay."""
    try:
        from mp.security import address_allowed  # type: ignore
        return bool(address_allowed(ip))
    except Exception:
        pass
    try:
        addr = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    if addr.is_loopback or addr.is_link_local or addr.is_private:
        return not addr.is_unspecified and not addr.is_multicast
    return addr.version == 4 and addr in ipaddress.ip_network("100.64.0.0/10")


def check(cid: str, title: str, result: str, detail: str = "") -> dict:
    assert result in RESULTS
    return {"id": cid, "title": title, "result": result, "detail": detail}


# ------------------------------------------------------------------ this PC

def exe_version(path) -> str | None:
    """The Savage Wind version written in the exe (it has no version info; its 'about' text holds it)."""
    try:
        if os.path.getsize(path) > MAX_EXE:
            return None
        data = Path(path).read_bytes()
    except OSError:
        return None
    if re.search(re.escape(SUPPORTED.encode("ascii")) + rb"(?![0-9])", data):
        return SUPPORTED
    for m in ANCHOR_RE.finditer(data):
        v = VERSION_RE.search(data[max(0, m.start() - 12):m.end()])
        if v:
            return v.group(1).decode("ascii")
    return None


def _read_json(path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def find_places(library: str | None = None, install: str | None = None, env: dict | None = None) -> dict:
    """{"library", "install", "why"}: from the arguments, SWMOD_LIBRARY, the launcher's remembered folders
    (%LOCALAPPDATA%\\Breathalyzer\\launcher.json), Documents\\SavageWind\\modloader, then the library's own
    config.json (its `install`). Only reads; never searches the disk."""
    env = os.environ if env is None else env
    why = []
    home = env.get("BREATHALYZER_HOME") or os.path.join(
        env.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local"), "Breathalyzer")
    remembered = _read_json(os.path.join(home, "launcher.json")) or {}
    if not library:
        for cand, w in ((env.get("SWMOD_LIBRARY"), "SWMOD_LIBRARY"),
                        (remembered.get("library"), "Breathalyzer remembers it"),
                        (os.path.join(env.get("USERPROFILE") or os.path.expanduser("~"), "Documents", "SavageWind",
                                      "modloader"), "Documents\\SavageWind\\modloader")):
            if isinstance(cand, str) and cand and os.path.isfile(os.path.join(cand, "config.json")):
                library = cand
                why.append(f"library: {w}")
                break
    else:
        why.append("library: given")
    if not install:
        for cand, w in ((remembered.get("install"), "Breathalyzer remembers it"),
                        ((_read_json(os.path.join(library, "config.json")) or {}).get("install") if library else None,
                         "the library's config.json")):
            if isinstance(cand, str) and cand and os.path.isfile(os.path.join(cand, EXE)):
                install = cand
                why.append(f"Savage Wind: {w}")
                break
    else:
        why.append("Savage Wind: given")
    return {"library": library, "install": install, "why": why}


def library_mods(library: str | None) -> tuple[list[dict] | None, str]:
    """(the library's mods [{"id","name","version","enabled"}], a problem or "")."""
    if not library:
        return None, "no mod library found"
    data = _read_json(os.path.join(library, "config.json"))
    if data is None:
        return None, f"{os.path.join(library, 'config.json')} can't be read"
    mods = data.get("mods")
    if not isinstance(mods, list):
        return None, "config.json has no mod list"
    out = []
    for m in mods:
        if isinstance(m, dict) and isinstance(m.get("id"), str):
            out.append({"id": m["id"], "name": str(m.get("name") or m["id"])[:80],
                        "version": m["version"] if isinstance(m.get("version"), str) else "",
                        "enabled": bool(m.get("enabled")),
                        "part_of": m.get("part_of") if isinstance(m.get("part_of"), str) else None})
    return out, ""


def enabled_mods(mods: list[dict] | None) -> dict:
    """{id: version} of the mods that are on (a part counts only while its main mod is on, like modman)."""
    if not mods:
        return {}
    on = {m["id"] for m in mods if m["enabled"]}
    return {m["id"]: m["version"] for m in mods if m["enabled"] and (not m.get("part_of") or m["part_of"] in on)}


def mods_code(mods: dict) -> str:
    """A short code for a mod list (same mods and versions = same code), to read out over the phone."""
    text = "\n".join(f"{k}={mods[k]}" for k in sorted(mods))
    h = hashlib.sha256(text.encode("utf-8")).hexdigest().upper()
    return f"{h[:4]}-{h[4:8]}"


def compare_mods(mine: dict, theirs: dict) -> dict:
    """PASS: the same. FAIL: Ghost multiplayer or the characters differ (missing or another version). WARN: other
    mods differ (fine for multiplayer unless they change the world)."""
    only_me = sorted(set(mine) - set(theirs))
    only_them = sorted(set(theirs) - set(mine))
    other_ver = sorted(k for k in set(mine) & set(theirs) if mine[k] != theirs[k])
    bad = [k for k in MUST_MATCH if (k in mine or k in theirs) and (mine.get(k) != theirs.get(k) or
                                                                     (k in mine) != (k in theirs))]
    parts = []
    if only_me:
        parts.append("only you: " + ", ".join(only_me))
    if only_them:
        parts.append("only your friend: " + ", ".join(only_them))
    if other_ver:
        parts.append("other versions: " + ", ".join(f"{k} ({mine[k] or '?'} / {theirs[k] or '?'})" for k in other_ver))
    if bad:
        result = "FAIL"
        parts.insert(0, "must be the same: " + ", ".join(bad))
    elif parts:
        result = "WARN"
    else:
        result = "PASS"
    return check("same_mods", "Same mods for multiplayer", result,
                 "; ".join(parts) if parts else f"both {mods_code(mine)} ({len(mine)} mods on)")


def local_addresses() -> list[str]:
    """This PC's home-network IPv4 addresses (no packet is sent: a UDP 'connect' only picks the route)."""
    found = []
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if home_address(ip) and not ip.startswith("127.") and ip not in found:
                found.append(ip)
    except OSError:
        pass
    for probe in ("192.168.254.254", "10.254.254.254", "172.31.254.254"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((probe, 9))
                ip = s.getsockname()[0]
            if home_address(ip) and not ip.startswith("127."):
                if ip in found:
                    found.remove(ip)
                found.insert(0, ip)          # (the one the router path uses: most likely the right one)
                break
        except OSError:
            continue
    return found


PS_PROFILES = ("Get-NetConnectionProfile | ForEach-Object { $p = $_; $ips = (Get-NetIPAddress -InterfaceIndex "
               "$p.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).IPAddress -join ','; "
               "'{0}|{1}|{2}' -f $p.NetworkCategory, $p.InterfaceAlias, $ips }")
KINDS = ("Private", "Public", "DomainAuthenticated")


def network_category(run=None) -> list[dict]:
    """Windows' connected networks: [{"kind": "Private" | "Public" | "DomainAuthenticated", "name", "ips"}]; []
    when unknown. Runs PowerShell's Get-NetConnectionProfile with no window (only reads)."""
    if run is None:
        if os.name != "nt":
            return []

        def run():
            p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_PROFILES],
                               capture_output=True, text=True, timeout=20,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return p.stdout
    try:
        text = run() or ""
    except Exception:
        return []
    out = []
    for line in text.splitlines():
        parts = [x.strip() for x in line.split("|")]
        if parts and parts[0] in KINDS:
            ips = [x.strip() for x in parts[2].replace(" ", ",").split(",") if x.strip()] if len(parts) > 2 else []
            out.append({"kind": parts[0], "name": parts[1][:40] if len(parts) > 1 else "", "ips": ips})
    return out


def judge_private(nets: list[dict], addrs: list[str]) -> dict:
    """The network that carries this PC's main home address must be Private (others, like a VPN, don't count)."""
    title = "Wi-Fi set to Private"
    fix = "Settings > Network & internet > your Wi-Fi > Private network"
    if not nets:
        return check("private", title, "INFO", "couldn't read it: check " + fix)
    main = addrs[0] if addrs else None
    hit = [n for n in nets if main and main in n["ips"]]
    if hit:
        n = hit[0]
        ok = n["kind"] != "Public"
        return check("private", title, "PASS" if ok else "FAIL",
                     f"{n['name'] or 'your network'} ({main}): {n['kind']}" + ("" if ok else f": {fix}"))
    if all(n["kind"] == "Public" for n in nets):
        return check("private", title, "FAIL", "Windows calls your networks Public: " + fix)
    return check("private", title, "WARN" if any(n["kind"] == "Public" for n in nets) else "PASS",
                 ", ".join(f"{n['name'] or '?'}: {n['kind']}" for n in nets))


def port_free(port: int, host: str = "") -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", 0) or socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def local_checks(places: dict, port: int, *, run_netcat=None, addresses=None) -> tuple[list[dict], dict]:
    """(the checks on this PC, the facts the friend gets over the echo)."""
    out = []
    py = sys.version.split()[0]
    out.append(check("python", "Python", "INFO", f"{py} at {sys.executable} (Windows' firewall allows programs: "
                                                  "Breathalyzer's own Python must be allowed for Private)"))
    sw = None
    inst = places.get("install")
    if not inst:
        out.append(check("savage_wind", "Savage Wind version", "FAIL",
                         "Savage Wind folder not found: give it with --install <folder>"))
    else:
        sw = exe_version(os.path.join(inst, EXE))
        if sw == SUPPORTED:
            out.append(check("savage_wind", "Savage Wind version", "PASS", f"{sw} in {inst}"))
        else:
            out.append(check("savage_wind", "Savage Wind version", "FAIL",
                             f"{sw or 'unknown'} in {inst}: the beta needs {SUPPORTED} (not 0.5.x)"))
        save = os.path.join(inst, "save")
        has_save = os.path.isdir(save) and bool(os.listdir(save))
        out.append(check("own_save", "A save of your own", "PASS" if has_save else "WARN",
                         save if has_save else "no save next to savagewind.exe: play once and save "
                                               "(multiplayer starts from a copy of it)"))
    mods, problem = library_mods(places.get("library"))
    on = enabled_mods(mods)
    if mods is None:
        out.append(check("library", "Mod library", "FAIL", problem + ": give it with --library <modloader folder>"))
    else:
        out.append(check("library", "Mod library", "PASS",
                         f"{places['library']}: {len(mods)} mods, {len(on)} on (code {mods_code(on)})"))
        out.append(check("ghost_on", "Ghost multiplayer on", "PASS" if GHOST_ID in on else "FAIL",
                         f"version {on[GHOST_ID] or '?'}" if GHOST_ID in on else
                         "Home > Multiplayer > Ghost multiplayer > Turn on"))
        out.append(check("stamina_off", "Infinite Stamina off", "FAIL" if STAMINA_ID in on else "PASS",
                         "Ghost multiplayer doesn't run with it: turn it off on Home" if STAMINA_ID in on else ""))
    addrs = local_addresses() if addresses is None else addresses
    out.append(check("address", "This PC's home address", "PASS" if addrs else "FAIL",
                     ", ".join(addrs) if addrs else "no home-network address: is the Wi-Fi or cable on?"))
    out.append(judge_private(network_category(run_netcat), addrs))
    free = port_free(port)
    out.append(check("port", f"Port {port} free", "PASS" if free else "WARN",
                     "" if free else "something uses it (Breathalyzer's Host still open?): Stop it first, "
                                     "or use --port with the same number on both PCs"))
    info = {"sw": sw, "mods": dict(list(on.items())[:500]), "mods_code": mods_code(on),
            "addresses": addrs[:8]}
    return out, info


def my_info(places: dict, name: str = "player") -> dict:
    """What the friend gets over the echo, without the slower checks: name, Savage Wind version, mods that are on."""
    inst = places.get("install")
    on = enabled_mods(library_mods(places.get("library"))[0])
    return {"name": name, "sw": exe_version(os.path.join(inst, EXE)) if inst else None,
            "mods": dict(list(on.items())[:500]), "mods_code": mods_code(on)}


# ------------------------------------------------------------------ the echo

def _line(obj: dict) -> bytes:
    data = (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")
    if len(data) > MAX_LINE:
        raise ValueError("line too long")
    return data


def _read_line(sock: socket.socket, buf: bytearray) -> dict | None:
    """One JSON object line, or None (closed, too long, not ours)."""
    while b"\n" not in buf:
        if len(buf) > MAX_LINE:
            return None
        chunk = sock.recv(4096)
        if not chunk:
            return None
        buf.extend(chunk)
    raw, _, rest = bytes(buf).partition(b"\n")
    buf[:] = rest
    if len(raw) > MAX_LINE:
        return None
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict) or obj.get("t") != "brz2pc" or obj.get("v") != FORMAT:
        return None
    return obj


def _clean_info(info) -> dict:
    """Only the fields we use, with sane sizes (it comes from the network)."""
    if not isinstance(info, dict):
        return {}
    out = {}
    for k in ("name", "role", "sw", "mods_code"):
        if isinstance(info.get(k), str):
            out[k] = info[k][:64]
    mods = info.get("mods")
    if isinstance(mods, dict):
        out["mods"] = {str(k)[:96]: str(v)[:32] for k, v in list(mods.items())[:500]
                       if isinstance(k, str) and isinstance(v, str)}
    port = info.get("port")
    if isinstance(port, int) and 1 <= port <= 65535:
        out["port"] = port
    return out


def serve(port: int, info: dict, *, host: str = "", seconds: float = LISTEN_SECONDS, allow=home_address,
          ready=None, say=None) -> dict:
    """Wait for the friend's echo test on TCP `port`. Answers each line with this PC's clock (and `info` once).
    Returns after one good friend or `seconds`: {"ok", "peer", "peer_info", "lines", "error"}."""
    say = say or (lambda *_: None)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            srv.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", 0) or socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(4)
    except OSError as e:
        srv.close()
        return {"ok": False, "error": f"port {port} can't be opened here ({e.strerror or e}): is Host still open?"}
    if ready:
        ready(srv.getsockname()[1])
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            srv.settimeout(max(0.1, min(1.0, end - time.monotonic())))
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError as e:
                return {"ok": False, "error": f"listening stopped ({e})"}
            with conn:
                if not allow(addr[0]):
                    say(f"  (refused {addr[0]}: not a home-network address)")
                    continue
                conn.settimeout(10)
                buf = bytearray()
                lines = 0
                peer_info = {}
                try:
                    while lines < MAX_LINES:
                        msg = _read_line(conn, buf)
                        if msg is None:
                            break
                        lines += 1
                        if lines == 1:
                            peer_info = _clean_info(msg.get("info"))
                        if msg.get("bye"):
                            break
                        reply = {"t": "brz2pc", "v": FORMAT, "n": msg.get("n"), "ts": time.time()}
                        if lines == 1:
                            reply["info"] = info
                        conn.sendall(_line(reply))
                except (OSError, ValueError):
                    pass
                if lines:
                    return {"ok": True, "peer": addr[0], "peer_info": peer_info, "lines": lines, "error": ""}
        return {"ok": False, "error": f"nobody came in {int(seconds)} s"}
    finally:
        srv.close()


def ping(address: str, port: int, info: dict, *, count: int = PINGS, timeout: float = 5.0,
         retry_seconds: float = CONNECT_RETRY_SECONDS, allow=home_address) -> dict:
    """The echo test towards the friend: {"ok","rtt_ms" (median),"rtts","offset_s","peer_info","error"}.
    offset_s: the friend's clock minus this one's (from the quickest ping)."""
    try:
        ip = socket.getaddrinfo(address, port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    except (OSError, IndexError, UnicodeError):
        return {"ok": False, "error": f"'{address}' isn't an address this PC can find"}
    if not allow(ip):
        return {"ok": False, "error": f"{ip} isn't a home-network address (multiplayer is home network only)"}
    end = time.monotonic() + retry_seconds
    while True:
        try:
            sock = socket.create_connection((ip, port), timeout=timeout)
            break
        except ConnectionRefusedError:
            if time.monotonic() >= end:
                return {"ok": False, "error": f"{ip} answers, but nothing listens on port {port}: "
                                              "is your friend at 'waiting for you'?"}
            time.sleep(0.5)
        except socket.timeout:
            return {"ok": False, "error": f"no answer from {ip}:{port} in {int(timeout)} s: the firewall on your "
                                          "friend's PC probably blocks it (Private Wi-Fi, Python allowed?)"}
        except OSError as e:
            return {"ok": False, "error": f"can't reach {ip}:{port} ({e.strerror or e})"}
    rtts, best, peer_info = [], None, {}
    buf = bytearray()
    try:
        with sock:
            sock.settimeout(timeout)
            for n in range(count):
                msg = {"t": "brz2pc", "v": FORMAT, "n": n, "ts": time.time()}
                if n == 0:
                    msg["info"] = info
                t0 = time.perf_counter()
                w0 = time.time()
                sock.sendall(_line(msg))
                reply = _read_line(sock, buf)
                t1 = time.perf_counter()
                if reply is None or reply.get("n") != n or not isinstance(reply.get("ts"), (int, float)):
                    return {"ok": False, "error": "the other side isn't this checker (a wrong answer came back)"}
                if n == 0:
                    peer_info = _clean_info(reply.get("info"))
                rtt = t1 - t0
                rtts.append(rtt * 1000.0)
                if best is None or rtt < best[0]:
                    best = (rtt, reply["ts"] - (w0 + rtt / 2.0))
            try:
                sock.sendall(_line({"t": "brz2pc", "v": FORMAT, "n": count, "bye": True}))
            except OSError:
                pass
    except (OSError, ValueError) as e:
        return {"ok": False, "error": f"the connection broke ({e})"}
    return {"ok": True, "rtt_ms": round(statistics.median(rtts), 2), "rtts": [round(r, 2) for r in rtts],
            "offset_s": round(best[1], 3), "peer_info": peer_info, "peer": ip, "error": ""}


def grade_latency(rtt_ms: float) -> dict:
    if rtt_ms <= LATENCY_OK_MS:
        r, d = "PASS", ""
    elif rtt_ms <= LATENCY_BAD_MS:
        r, d = "WARN", " (playable, but the other player may jump a little; a cable or closer router helps)"
    else:
        r, d = "FAIL", " (too slow for a home network: other downloads? a weak Wi-Fi?)"
    return check("latency", "Delay between the PCs", r, f"{rtt_ms:.1f} ms there and back{d}")


def grade_clock(offset_s: float) -> dict:
    off = abs(offset_s)
    if off <= CLOCK_OK_S:
        return check("clock", "Clocks agree", "PASS", f"{offset_s:+.1f} s apart")
    return check("clock", "Clocks agree", "WARN", f"{offset_s:+.1f} s apart: fine for the game, but set both "
                                                  "clocks automatically (Settings > Time) so the reports line up")


def peer_checks(mine: dict, peer: dict) -> list[dict]:
    out = []
    psw = peer.get("sw")
    if psw is None and "sw" not in peer:
        out.append(check("same_sw", "Same Savage Wind", "INFO", "your friend's version didn't come over"))
    elif psw == mine.get("sw") == SUPPORTED:
        out.append(check("same_sw", "Same Savage Wind", "PASS", SUPPORTED))
    else:
        out.append(check("same_sw", "Same Savage Wind", "FAIL",
                         f"you {mine.get('sw') or 'unknown'}, your friend {psw or 'unknown'}: both need {SUPPORTED}"))
    if "mods" in peer:
        out.append(compare_mods(mine.get("mods") or {}, peer["mods"]))
    else:
        out.append(check("same_mods", "Same mods for multiplayer", "INFO",
                         "your friend's mod list didn't come over: compare the mods codes "
                         f"(yours {mine.get('mods_code')})"))
    return out


# ------------------------------------------------------------------ the console

BLOCK = {
    "P": ["### ", "#  #", "### ", "#   ", "#   "], "A": [" ## ", "#  #", "####", "#  #", "#  #"],
    "S": [" ###", "#   ", " ## ", "   #", "### "], "F": ["####", "#   ", "### ", "#   ", "#   "],
    "I": ["###", " # ", " # ", " # ", "###"], "L": ["#   ", "#   ", "#   ", "#   ", "####"],
    "W": ["#   #", "#   #", "# # #", "## ##", "#   #"], "R": ["### ", "#  #", "### ", "# # ", "#  #"],
    "N": ["#  #", "## #", "# ##", "#  #", "#  #"], "K": ["#  #", "# # ", "##  ", "# # ", "#  #"],
    "O": [" ## ", "#  #", "#  #", "#  #", " ## "], " ": ["  ", "  ", "  ", "  ", "  "],
}


def big(word: str) -> str:
    rows = ["", "", "", "", ""]
    for ch in word.upper():
        g = BLOCK.get(ch, BLOCK[" "])
        for i in range(5):
            rows[i] += g[i] + "  "
    return "\n".join("   " + r.rstrip() for r in rows)


class Console:
    """Prompts and prints (stand-ins in the tests)."""

    def __init__(self, inp=input, out=print):
        self.inp, self.out = inp, out

    def say(self, text: str = "") -> None:
        self.out(text)

    def head(self, text: str) -> None:
        self.out("")
        self.out("=" * 64)
        self.out("   " + text.upper())
        self.out("=" * 64)

    def ask(self, prompt: str, default: str = "") -> str:
        try:
            got = self.inp(f">> {prompt} ")
        except EOFError:
            got = ""
        got = (got or "").strip()
        return got or default

    def choose(self, prompt: str, options: dict, default: str) -> str:
        keys = "/".join(k.upper() if k == default else k for k in options)
        while True:
            got = self.ask(f"{prompt} [{keys}]", default).lower()
            if got[:1] in options:
                return got[:1]
            self.say("   Type one of: " + ", ".join(f"{k} = {v}" for k, v in options.items()))

    def show(self, c: dict) -> None:
        self.out(f"   {c['result']:<5} {c['title']}" + (f": {c['detail']}" if c.get("detail") else ""))


# ------------------------------------------------------------------ the record

def summary(checks: list[dict], steps: list[dict]) -> dict:
    counts = {r: 0 for r in RESULTS}
    for c in checks + steps:
        counts[c["result"]] = counts.get(c["result"], 0) + 1
    overall = "FAIL" if counts["FAIL"] else ("WARN" if counts["WARN"] else "PASS")
    return {"overall": overall, "counts": counts}


def make_record(name: str, role: str, places: dict, checks: list[dict], steps: list[dict], peer: dict | None,
                info: dict | None = None) -> dict:
    return {"tool": TOOL, "format": FORMAT, "when": now_iso(), "name": name, "role": role,
            "pc": {"library": places.get("library"), "install": places.get("install"),
                   "python": sys.version.split()[0], "sw": (info or {}).get("sw"),
                   "mods_code": (info or {}).get("mods_code")},
            "friend": peer or {}, "checks": checks, "steps": steps, "summary": summary(checks, steps)}


def report_text(rec: dict) -> str:
    """The short report to send: results first, then what failed, then the rest."""
    s = rec["summary"]
    c = s["counts"]
    who = "host (PC A)" if rec["role"] == "A" else "friend (PC B)"
    lines = [f"Breathalyzer two-PC test: {rec['name']}, {who}, {rec['when']}",
             f"RESULT: {s['overall']}  ({c['FAIL']} FAIL, {c['WARN']} WARN, {c['PASS']} PASS, {c['SKIP']} SKIP)"]
    rows = rec["checks"] + rec["steps"]
    bad = [r for r in rows if r["result"] in ("FAIL", "WARN")]
    if bad:
        lines.append("")
        lines.append("Problems:")
        for r in bad:
            lines.append(f"  {r['result']:<5} {r['title']}: {r.get('detail') or r.get('notes') or ''}".rstrip(": "))
    lines.append("")
    lines.append("All:")
    for r in rows:
        extra = r.get("detail") or r.get("notes") or ""
        lines.append(f"  {r['result']:<5} {r['title']}" + (f": {extra}" if extra else ""))
    pc = rec.get("pc") or {}
    lines.append("")
    lines.append(f"Savage Wind {pc.get('sw') or '?'}, mods code {pc.get('mods_code') or '?'}, "
                 f"Python {pc.get('python')}")
    return "\n".join(lines) + "\n"


def default_out_dir(env: dict | None = None) -> Path:
    env = os.environ if env is None else env
    base = env.get("USERPROFILE") or os.path.expanduser("~")
    return Path(base) / "Documents" / "Breathalyzer two-PC test"


def save_record(rec: dict, out_dir) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H%M")
    safe = re.sub(r"[^A-Za-z0-9 _-]", "", rec["name"])[:20].strip() or "player"
    stem = f"two-PC test {safe} {rec['role']} {stamp}"
    j = out_dir / (stem + ".json")
    t = out_dir / (stem + ".txt")
    j.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    t.write_text(report_text(rec), encoding="utf-8")
    return j, t


# ------------------------------------------------------------------ the guided run

def connection_both_ways(con: Console, role: str, port: int, info: dict, *, serve_fn=None, ping_fn=None,
                         friend_address: str | None = None, listen_seconds: float = LISTEN_SECONDS) -> tuple[list, dict]:
    """A waits first, B calls A; then B waits and A calls B (A already knows B's address from the first call).
    Returns (checks, the friend's facts)."""
    serve_fn, ping_fn = serve_fn or serve, ping_fn or ping
    out, peer = [], {}
    addrs = info.get("addresses") or []
    shown = (addrs[0] + (f"   (if that one fails: {', '.join(addrs[1:3])})" if addrs[1:] else "")) if addrs         else "unknown (type ipconfig in a console: the IPv4 Address)"

    def listen(title):
        con.say(f"   WAITING FOR YOUR FRIEND on port {port} (up to {int(listen_seconds // 60)} minutes; "
                "Ctrl+C stops waiting)...")
        try:
            r = serve_fn(port, info, seconds=listen_seconds, say=con.say)
        except KeyboardInterrupt:
            r = {"ok": False, "error": "you stopped waiting"}
        if r["ok"]:
            out.append(check("reach_in", title, "PASS", f"from {r['peer']}"))
        else:
            out.append(check("reach_in", title, "FAIL", r["error"] + ". On this PC: Wi-Fi Private, and Windows "
                             "Security > Firewall > Allow an app: tick Python for Private"))
        con.show(out[-1])
        return r

    def call(address, title):
        con.say(f"   CALLING {address} on port {port}...")
        try:
            r = ping_fn(address, port, info)
        except KeyboardInterrupt:
            r = {"ok": False, "error": "you stopped it"}
        if r["ok"]:
            out.append(check("reach_out", title, "PASS", f"to {r['peer']}"))
            con.show(out[-1])
            out.append(grade_latency(r["rtt_ms"]))
            con.show(out[-1])
            out.append(grade_clock(r["offset_s"]))
            con.show(out[-1])
        else:
            out.append(check("reach_out", title, "FAIL", r["error"]))
            con.show(out[-1])
        return r

    con.head("Connection test, both ways")
    if role == "A":
        con.say(f"   YOUR ADDRESS: {shown}")
        con.say(f"   Tell your friend this address. Ask your friend to run the checker as PC B now,")
        con.say(f"   and type your address when it asks.")
        con.ask("Press Enter when you are ready to wait for your friend.")
        r = listen("Your friend can reach this PC")
        if r.get("ok"):
            peer = r.get("peer_info") or {}
            friend_address = friend_address or r["peer"]
        else:
            friend_address = friend_address or con.ask("Type your friend's address (or Enter to skip):")
        if friend_address:
            con.say("   Now this PC calls your friend's PC (your friend's checker is waiting).")
            p = call(friend_address, "This PC can reach your friend")
            if p.get("ok") and p.get("peer_info"):
                peer = p["peer_info"]
        else:
            out.append(check("reach_out", "This PC can reach your friend", "SKIP", "no address"))
    else:
        friend_address = friend_address or con.ask("Type your friend's address (PC A shows it):")
        if friend_address:
            p = call(friend_address, "This PC can reach your friend")
            if p.get("ok"):
                peer = p.get("peer_info") or {}
        else:
            out.append(check("reach_out", "This PC can reach your friend", "SKIP", "no address"))
        con.say(f"   Now your friend calls you back. YOUR ADDRESS: {shown}")
        r = listen("Your friend can reach this PC")
        if r.get("ok") and not peer:
            peer = r.get("peer_info") or {}
    for c in peer_checks(info, peer):
        out.append(c)
        con.show(c)
    return out, peer


def game_steps(con: Console, role: str, steps=STEPS) -> list[dict]:
    """Walk through the game steps; each answer is this player's own view."""
    out = []
    con.head("The game steps")
    con.say("   Do each step together (a call or chat helps). Answer for what YOU see.")
    con.say("   p = PASS (it worked), f = FAIL (it didn't), s = SKIP (couldn't try), q = stop here")
    for i, (sid, who, title, text) in enumerate(steps, 1):
        con.head(f"Step {i} of {len(steps)}: {title}")
        mine = who == "both" or who == role
        con.say(f"   WHO: {'both of you' if who == 'both' else ('you' if mine else 'your friend')} "
                f"(PC {who if who != 'both' else 'A and B'})")
        for chunk in _wrap(text, 60):
            con.say("   " + chunk)
        try:
            got = con.choose("Result?", {"p": "PASS", "f": "FAIL", "s": "SKIP", "q": "stop"}, "p")
        except KeyboardInterrupt:
            got = "q"
        if got == "q":
            for sid2, _w, title2, _t in steps[i - 1:]:
                out.append({"id": sid2, "title": title2, "result": "SKIP", "notes": "stopped before this step"})
            break
        result = {"p": "PASS", "f": "FAIL", "s": "SKIP"}[got]
        notes = con.ask("Notes (what you saw; Enter for none):")
        out.append({"id": sid, "title": title, "result": result, "notes": notes[:500]})
    return out


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def dry_run(out=print) -> None:
    out("Breathalyzer two-PC test: the steps (dry run: nothing is checked, nothing is opened)")
    out("")
    out("Each player runs, in the Breathalyzer folder:  app\\runtime\\python.exe app\\tools\\mp\\twopc_check.py")
    out("(PC A hosts, PC B joins; at the same time, each on their own PC. Don't press Host before Part 3.)")
    out("")
    out("Part 1. This PC (no network):")
    for t in ("Savage Wind is " + SUPPORTED, "the mod library is found and readable", "Ghost multiplayer is on",
              "Infinite Stamina is off", "a save of your own exists", "this PC's home address",
              "the Wi-Fi is set to Private", f"port {DEFAULT_PORT} is free"):
        out(f"  - {t}")
    out("")
    out(f"Part 2. Between the PCs (a tiny echo on TCP port {DEFAULT_PORT}, home network only):")
    out("  - PC A waits; PC B types A's address and calls A (B can reach A)")
    out("  - PC B waits; PC A calls B back (A can reach B)")
    out("  - delay there and back, clocks, same Savage Wind, same mods for multiplayer")
    out("")
    out("Part 3. In the game (answer PASS / FAIL / SKIP + notes):")
    for i, (_sid, who, title, text) in enumerate(STEPS, 1):
        out(f"  {i:>2}. {title} [{'A and B' if who == 'both' else 'PC ' + who}]")
        for chunk in _wrap(text, 72):
            out("      " + chunk)
    out("")
    out(f"Results: a .json and a short .txt in {default_out_dir()} (send both to each other).")


def guided(args, con: Console) -> int:
    con.head("Breathalyzer two-PC test")
    con.say("   One of you is PC A (hosts the world), the other PC B (joins).")
    con.say("   Both run this on your own PC at the same time.")
    role = (args.role or "").upper()
    if role not in ("A", "B"):
        role = con.choose("Are you PC A (host) or PC B (friend)?", {"a": "host", "b": "friend"}, "a").upper()
    name = args.name or con.ask("Your name (for the report):", "player")[:20]
    places = find_places(args.library, args.install)
    con.head("Part 1: this PC")
    checks, info = local_checks(places, args.port)
    info.update({"name": name, "role": role, "port": args.port})
    for c in checks:
        con.show(c)
    peer = {}
    if not args.no_network:
        conn, peer = connection_both_ways(con, role, args.port, info, friend_address=args.friend)
        checks += conn
    steps = [] if args.no_game else game_steps(con, role)
    rec = make_record(name, role, places, checks, steps, peer, info)
    try:
        j, t = save_record(rec, args.out or default_out_dir())
    except OSError as e:
        con.say(f"   (couldn't write there: {e}; writing next to this console instead)")
        j, t = save_record(rec, Path.cwd())
    con.head("Done")
    con.say(big(rec["summary"]["overall"]))
    con.say("")
    con.say(report_text(rec).split("\n\nAll:")[0])
    con.say("")
    con.say("   SEND THESE TWO FILES TO EACH OTHER (and to giruka):")
    con.say(f"   {t}")
    con.say(f"   {j}")
    return 0 if rec["summary"]["overall"] != "FAIL" else 1


def main(argv=None, con: Console | None = None) -> int:
    ap = argparse.ArgumentParser(description="Breathalyzer two-PC test: run it on both PCs (no windows).")
    ap.add_argument("mode", nargs="?", choices=("run", "listen", "connect"), default="run")
    ap.add_argument("address", nargs="?", help="connect: the other PC's address")
    ap.add_argument("--dry-run", action="store_true", help="only print the steps")
    ap.add_argument("--role", choices=("A", "B", "a", "b"))
    ap.add_argument("--name")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--library", help="the mod library (modloader folder)")
    ap.add_argument("--install", help="the Savage Wind folder")
    ap.add_argument("--friend", help="the friend's address (skips that question)")
    ap.add_argument("--out", help="where the report goes")
    ap.add_argument("--no-network", action="store_true", help="skip the connection test")
    ap.add_argument("--no-game", action="store_true", help="skip the game steps")
    args = ap.parse_args(argv)
    con = con or Console()
    if not 1 <= args.port <= 65535:
        con.say("The port must be 1 to 65535.")
        return 2
    if args.dry_run:
        dry_run(con.say)
        return 0
    if args.mode == "run":
        return guided(args, con)
    info = my_info(find_places(args.library, args.install), args.name or "player")
    if args.mode == "listen":
        con.say(f"Waiting on port {args.port}. This PC: {', '.join(local_addresses()) or 'unknown'}")
        r = serve(args.port, info, say=con.say)
        con.say(f"PASS  {r['peer']} reached this PC" if r["ok"] else f"FAIL  {r['error']}")
        return 0 if r["ok"] else 1
    if not args.address:
        con.say("connect needs the other PC's address, like: connect 192.168.1.20")
        return 2
    r = ping(args.address, args.port, info)
    if not r["ok"]:
        con.say(f"FAIL  {r['error']}")
        return 1
    for c in [grade_latency(r["rtt_ms"]), grade_clock(r["offset_s"])] + peer_checks(info, r["peer_info"]):
        con.show(c)
    return 0


if __name__ == "__main__":
    tools = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if tools not in sys.path:
        sys.path.insert(0, tools)
    sys.exit(main())
