"""Tests for the two-PC test kit (tools/mp/twopc_check.py).

Network: only 127.0.0.1, on ports the system picks (port 0), with short time limits. No game, no window, no
PowerShell (the Wi-Fi kind is read through a stand-in). Libraries, Savage Wind folders and reports live in temp
folders."""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

from mp import twopc_check as tc  # noqa: E402

LOCAL = "127.0.0.1"


def free_port() -> int:
    with socket.socket() as s:
        s.bind((LOCAL, 0))
        return s.getsockname()[1]


def make_library(root: Path, mods, install: Path | None = None) -> Path:
    lib = root / "modloader"
    lib.mkdir(parents=True, exist_ok=True)
    data = {"tool": "botw-mod-loader modman", "mods": mods}
    if install:
        data["install"] = str(install)
    (lib / "config.json").write_text(json.dumps(data), encoding="utf-8")
    return lib


def make_install(root: Path, version: str | None = tc.SUPPORTED, save: bool = True) -> Path:
    inst = root / "SavageWind"
    inst.mkdir(parents=True, exist_ok=True)
    body = b"\0" * 64 + (b"dda7" + version.encode() if version else b"no version here") + b"\0" * 64
    (inst / tc.EXE).write_bytes(body)
    if save:
        (inst / "save").mkdir(exist_ok=True)
        (inst / "save" / "0").mkdir(exist_ok=True)
    return inst


class Feed:
    """Scripted answers for Console.ask; collects what was printed."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.lines = []

    def inp(self, prompt):
        self.lines.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def out(self, text=""):
        self.lines.append(text)

    def text(self):
        return "\n".join(self.lines)


class ServeThread:
    """tc.serve on 127.0.0.1 with a port the system picks, in a thread."""

    def __init__(self, info, seconds=5.0, allow=tc.home_address, port=0):
        self.ready = threading.Event()
        self.port = None
        self.result = None

        def go():
            self.result = tc.serve(port, info, host=LOCAL, seconds=seconds, allow=allow, ready=self._ready)
            self.ready.set()

        self.t = threading.Thread(target=go, daemon=True)
        self.t.start()
        assert self.ready.wait(5)

    def _ready(self, port):
        self.port = port
        self.ready.set()

    def join(self):
        self.t.join(10)
        return self.result


class VersionTests(unittest.TestCase):
    def test_supported_version_found(self):
        with tempfile.TemporaryDirectory() as d:
            inst = make_install(Path(d))
            self.assertEqual(tc.exe_version(inst / tc.EXE), tc.SUPPORTED)

    def test_other_version_read(self):
        with tempfile.TemporaryDirectory() as d:
            inst = make_install(Path(d), "0.5.2-alpha.1")
            self.assertEqual(tc.exe_version(inst / tc.EXE), "0.5.2-alpha.1")

    def test_no_version_or_no_file(self):
        with tempfile.TemporaryDirectory() as d:
            inst = make_install(Path(d), None)
            self.assertIsNone(tc.exe_version(inst / tc.EXE))
            self.assertIsNone(tc.exe_version(Path(d) / "missing.exe"))


class LibraryTests(unittest.TestCase):
    MODS = [{"id": tc.GHOST_ID, "name": "Ghost", "version": "0.7.0", "enabled": True},
            {"id": "x.part", "name": "Part", "enabled": True, "part_of": "x.main"},
            {"id": "x.main", "name": "Main", "enabled": False},
            {"id": "y.other", "name": "Other", "version": "1.0.0", "enabled": True}]

    def test_enabled_mods_respects_part_of(self):
        with tempfile.TemporaryDirectory() as d:
            lib = make_library(Path(d), self.MODS)
            mods, problem = tc.library_mods(str(lib))
            self.assertEqual(problem, "")
            self.assertEqual(tc.enabled_mods(mods), {tc.GHOST_ID: "0.7.0", "y.other": "1.0.0"})

    def test_damaged_or_missing_library(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(tc.library_mods(None)[0])
            lib = Path(d) / "lib"
            lib.mkdir()
            (lib / "config.json").write_text("{not json", encoding="utf-8")
            mods, problem = tc.library_mods(str(lib))
            self.assertIsNone(mods)
            self.assertIn("can't be read", problem)

    def test_find_places_from_launcher_json_and_library(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            inst = make_install(root)
            lib = make_library(root, self.MODS, install=inst)
            home = root / "bz"
            home.mkdir()
            (home / "launcher.json").write_text(json.dumps({"library": str(lib)}), encoding="utf-8")
            env = {"BREATHALYZER_HOME": str(home), "USERPROFILE": str(root / "nobody")}
            p = tc.find_places(env=env)
            self.assertEqual(p["library"], str(lib))
            self.assertEqual(p["install"], str(inst))   # from the library's config.json

    def test_find_places_env_library_and_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            lib = make_library(root, [])
            env = {"SWMOD_LIBRARY": str(lib), "BREATHALYZER_HOME": str(root / "none"),
                   "USERPROFILE": str(root / "nobody")}
            self.assertEqual(tc.find_places(env=env)["library"], str(lib))
            env = {"BREATHALYZER_HOME": str(root / "none"), "USERPROFILE": str(root / "nobody")}
            p = tc.find_places(env=env)
            self.assertIsNone(p["library"])
            self.assertIsNone(p["install"])


class ModsCompareTests(unittest.TestCase):
    def test_code_is_order_free_and_version_sensitive(self):
        a = {"a": "1", "b": "2"}
        self.assertEqual(tc.mods_code(a), tc.mods_code({"b": "2", "a": "1"}))
        self.assertNotEqual(tc.mods_code(a), tc.mods_code({"a": "1", "b": "3"}))
        self.assertRegex(tc.mods_code(a), r"^[0-9A-F]{4}-[0-9A-F]{4}$")

    def test_same_mods_pass(self):
        m = {tc.GHOST_ID: "0.7.0", "y": "1"}
        self.assertEqual(tc.compare_mods(m, dict(m))["result"], "PASS")

    def test_ghost_version_differs_fail(self):
        c = tc.compare_mods({tc.GHOST_ID: "0.7.0"}, {tc.GHOST_ID: "0.6.0"})
        self.assertEqual(c["result"], "FAIL")
        self.assertIn(tc.GHOST_ID, c["detail"])

    def test_characters_missing_on_one_side_fail(self):
        c = tc.compare_mods({tc.GHOST_ID: "1", tc.CHARS_ID: "1"}, {tc.GHOST_ID: "1"})
        self.assertEqual(c["result"], "FAIL")

    def test_other_mod_differs_warn(self):
        c = tc.compare_mods({tc.GHOST_ID: "1", "z.extra": "1"}, {tc.GHOST_ID: "1"})
        self.assertEqual(c["result"], "WARN")
        self.assertIn("only you: z.extra", c["detail"])

    def test_peer_checks_versions(self):
        mine = {"sw": tc.SUPPORTED, "mods": {}, "mods_code": tc.mods_code({})}
        ok = tc.peer_checks(mine, {"sw": tc.SUPPORTED, "mods": {}})
        self.assertEqual([c["result"] for c in ok], ["PASS", "PASS"])
        bad = tc.peer_checks(mine, {"sw": "0.5.0-alpha.1", "mods": {}})
        self.assertEqual(bad[0]["result"], "FAIL")
        none = tc.peer_checks(mine, {})
        self.assertEqual([c["result"] for c in none], ["INFO", "INFO"])


class LocalChecksTests(unittest.TestCase):
    def test_all_good(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            inst = make_install(root)
            lib = make_library(root, [{"id": tc.GHOST_ID, "version": "0.7.0", "enabled": True}])
            checks, info = tc.local_checks({"library": str(lib), "install": str(inst)}, free_port(),
                                           run_netcat=lambda: "Public|VPN|172.16.0.2\nPrivate|Wi-Fi|192.168.1.20\n",
                                           addresses=["192.168.1.20"])
            res = {c["id"]: c["result"] for c in checks}
            for k in ("savage_wind", "own_save", "library", "ghost_on", "stamina_off", "address", "private", "port"):
                self.assertEqual(res[k], "PASS", (k, checks))
            self.assertEqual(info["sw"], tc.SUPPORTED)
            self.assertEqual(info["mods"], {tc.GHOST_ID: "0.7.0"})

    def test_problems_found(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            inst = make_install(root, "0.5.1-alpha.2", save=False)
            lib = make_library(root, [{"id": tc.STAMINA_ID, "enabled": True}])
            with socket.socket() as busy:              # (127.0.0.1 and no listen: no firewall question)
                busy.bind((LOCAL, 0))
                port = busy.getsockname()[1]
                checks, _ = tc.local_checks({"library": str(lib), "install": str(inst)}, port,
                                            run_netcat=lambda: "Public|Wi-Fi|", addresses=[])
            res = {c["id"]: c["result"] for c in checks}
            self.assertEqual(res["savage_wind"], "FAIL")
            self.assertEqual(res["own_save"], "WARN")
            self.assertEqual(res["ghost_on"], "FAIL")
            self.assertEqual(res["stamina_off"], "FAIL")
            self.assertEqual(res["address"], "FAIL")
            self.assertEqual(res["private"], "FAIL")
            self.assertEqual(res["port"], "WARN")

    def test_nothing_found(self):
        checks, info = tc.local_checks({"library": None, "install": None}, free_port(),
                                       run_netcat=lambda: "", addresses=["10.0.0.5"])
        res = {c["id"]: c["result"] for c in checks}
        self.assertEqual(res["savage_wind"], "FAIL")
        self.assertEqual(res["library"], "FAIL")
        self.assertEqual(res["private"], "INFO")
        self.assertIsNone(info["sw"])

    def test_network_category_parse(self):
        nets = tc.network_category(lambda: "Private|Wi-Fi|192.168.1.20 10.0.0.2\nPublic|VPN|172.16.0.2\njunk\n")
        self.assertEqual(nets, [{"kind": "Private", "name": "Wi-Fi", "ips": ["192.168.1.20", "10.0.0.2"]},
                                {"kind": "Public", "name": "VPN", "ips": ["172.16.0.2"]}])
        self.assertEqual(tc.judge_private(nets, ["192.168.1.20"])["result"], "PASS")   # (a VPN doesn't count)
        self.assertEqual(tc.judge_private(nets, ["172.16.0.2"])["result"], "FAIL")
        self.assertEqual(tc.judge_private(nets, [])["result"], "WARN")
        self.assertEqual(tc.judge_private([], ["192.168.1.20"])["result"], "INFO")
        self.assertEqual(tc.network_category(lambda: (_ for _ in ()).throw(OSError("no"))), [])


class EchoTests(unittest.TestCase):
    INFO_A = {"name": "Ana", "role": "A", "sw": tc.SUPPORTED, "mods": {tc.GHOST_ID: "0.7.0"}, "mods_code": "X"}
    INFO_B = {"name": "Ben", "role": "B", "sw": tc.SUPPORTED, "mods": {tc.GHOST_ID: "0.7.0"}, "mods_code": "Y"}

    def test_echo_one_way(self):
        srv = ServeThread(self.INFO_A)
        r = tc.ping(LOCAL, srv.port, self.INFO_B, count=5, retry_seconds=2)
        s = srv.join()
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(r["rtts"]), 5)
        self.assertLess(abs(r["offset_s"]), 1.0)       # same PC, same clock
        self.assertEqual(r["peer_info"]["name"], "Ana")
        self.assertEqual(r["peer_info"]["mods"], {tc.GHOST_ID: "0.7.0"})
        self.assertTrue(s["ok"])
        self.assertEqual(s["peer"], LOCAL)
        self.assertEqual(s["peer_info"]["name"], "Ben")

    def test_echo_both_ways(self):
        a = ServeThread(self.INFO_A)
        rb = tc.ping(LOCAL, a.port, self.INFO_B, count=3, retry_seconds=2)
        self.assertTrue(a.join()["ok"])
        b = ServeThread(self.INFO_B)
        ra = tc.ping(LOCAL, b.port, self.INFO_A, count=3, retry_seconds=2)
        self.assertTrue(b.join()["ok"])
        self.assertTrue(rb["ok"] and ra["ok"])
        self.assertEqual(ra["peer_info"]["name"], "Ben")

    def test_nothing_listening(self):
        r = tc.ping(LOCAL, free_port(), self.INFO_B, retry_seconds=0.3, timeout=5)   # (Windows: ~2 s to refuse)
        self.assertFalse(r["ok"])
        self.assertIn("nothing listens", r["error"])

    def test_public_address_refused_before_any_packet(self):
        with mock.patch.object(tc.socket, "create_connection") as cc:
            r = tc.ping("8.8.8.8", 7788, self.INFO_B)
        self.assertFalse(r["ok"])
        self.assertIn("home-network", r["error"])
        cc.assert_not_called()

    def test_server_refuses_non_home_peer_then_times_out(self):
        srv = ServeThread(self.INFO_A, seconds=1.5, allow=lambda ip: False)
        with socket.create_connection((LOCAL, srv.port), timeout=2) as s:
            s.sendall(tc._line({"t": "brz2pc", "v": 1, "n": 0, "ts": time.time()}))
            s.settimeout(2)
            try:
                self.assertEqual(s.recv(100), b"")         # closed with no answer
            except (ConnectionResetError, ConnectionAbortedError):
                pass                                       # (Windows: closed with unread data)
        r = srv.join()
        self.assertFalse(r["ok"])
        self.assertIn("nobody came", r["error"])

    def test_server_ignores_garbage_and_oversize(self):
        srv = ServeThread(self.INFO_A, seconds=3)
        with socket.create_connection((LOCAL, srv.port), timeout=2) as s:
            s.sendall(b"GET / HTTP/1.0\r\n\r\n")
            s.settimeout(2)
            self.assertEqual(s.recv(100), b"")
        with socket.create_connection((LOCAL, srv.port), timeout=2) as s:
            try:
                s.sendall(b"x" * (tc.MAX_LINE + 10))
                s.settimeout(2)
                self.assertEqual(s.recv(100), b"")
            except (ConnectionResetError, ConnectionAbortedError):
                pass
        r = tc.ping(LOCAL, srv.port, self.INFO_B, count=2, retry_seconds=2)   # still serving a real friend
        self.assertTrue(r["ok"], r)
        self.assertTrue(srv.join()["ok"])

    def test_wrong_answer_detected(self):
        lsock = socket.socket()
        lsock.bind((LOCAL, 0))
        lsock.listen(1)
        port = lsock.getsockname()[1]

        def fake():
            c, _ = lsock.accept()
            with c:
                c.recv(4096)
                c.sendall(b'{"hello":"not ours"}\n')
        t = threading.Thread(target=fake, daemon=True)
        t.start()
        r = tc.ping(LOCAL, port, self.INFO_B, retry_seconds=1, timeout=2)
        t.join(3)
        lsock.close()
        self.assertFalse(r["ok"])
        self.assertIn("isn't this checker", r["error"])

    def test_clean_info_trims(self):
        info = tc._clean_info({"name": "n" * 200, "mods": {"a": "1", "b": 2}, "port": 99999, "evil": "x"})
        self.assertEqual(len(info["name"]), 64)
        self.assertEqual(info["mods"], {"a": "1"})
        self.assertNotIn("port", info)
        self.assertNotIn("evil", info)
        self.assertEqual(tc._clean_info("nope"), {})


class GradeTests(unittest.TestCase):
    def test_latency(self):
        self.assertEqual(tc.grade_latency(4)["result"], "PASS")
        self.assertEqual(tc.grade_latency(80)["result"], "WARN")
        self.assertEqual(tc.grade_latency(400)["result"], "FAIL")

    def test_clock(self):
        self.assertEqual(tc.grade_clock(-1.2)["result"], "PASS")
        self.assertEqual(tc.grade_clock(75)["result"], "WARN")


class GuidedTests(unittest.TestCase):
    def test_game_steps_record_and_notes(self):
        answers = []
        for i in range(len(tc.STEPS)):
            answers += (["f", "B stood still"] if i == 3 else ["p", ""])
        f = Feed(answers)
        steps = tc.game_steps(tc.Console(f.inp, f.out), "A")
        self.assertEqual(len(steps), len(tc.STEPS))
        self.assertEqual(steps[3]["result"], "FAIL")
        self.assertEqual(steps[3]["notes"], "B stood still")
        self.assertEqual(sum(s["result"] == "PASS" for s in steps), len(tc.STEPS) - 1)

    def test_game_steps_stop_marks_rest_skip(self):
        f = Feed(["p", "", "q"])
        steps = tc.game_steps(tc.Console(f.inp, f.out), "B")
        self.assertEqual([s["result"] for s in steps], ["PASS"] + ["SKIP"] * (len(tc.STEPS) - 1))

    def test_bad_answer_asked_again(self):
        f = Feed(["maybe", "s", ""] + ["p", ""] * len(tc.STEPS))
        steps = tc.game_steps(tc.Console(f.inp, f.out), "A")
        self.assertEqual(steps[0]["result"], "SKIP")
        self.assertIn("Type one of", f.text())

    def test_connection_flow_A_uses_friends_address_from_first_call(self):
        calls = []

        def serve_fn(port, info, seconds, say):
            calls.append(("serve", port))
            return {"ok": True, "peer": "192.168.1.30", "peer_info": {"name": "Ben", "sw": tc.SUPPORTED,
                                                                     "mods": {}}, "error": ""}

        def ping_fn(address, port, info):
            calls.append(("ping", address, port))
            return {"ok": True, "peer": address, "rtt_ms": 3.0, "offset_s": 0.2,
                    "peer_info": {"name": "Ben", "sw": tc.SUPPORTED, "mods": {}}}
        f = Feed([""])
        info = {"sw": tc.SUPPORTED, "mods": {}, "mods_code": tc.mods_code({}), "addresses": ["192.168.1.20"]}
        checks, peer = tc.connection_both_ways(tc.Console(f.inp, f.out), "A", 7788, info,
                                               serve_fn=serve_fn, ping_fn=ping_fn)
        self.assertEqual(calls, [("serve", 7788), ("ping", "192.168.1.30", 7788)])
        res = {c["id"]: c["result"] for c in checks}
        self.assertEqual(res, {"reach_in": "PASS", "reach_out": "PASS", "latency": "PASS", "clock": "PASS",
                               "same_sw": "PASS", "same_mods": "PASS"})
        self.assertIn("YOUR ADDRESS: 192.168.1.20", f.text())
        self.assertEqual(peer["name"], "Ben")

    def test_connection_flow_B_firewall_fail(self):
        def serve_fn(port, info, seconds, say):
            return {"ok": False, "error": "nobody came in 180 s"}

        def ping_fn(address, port, info):
            return {"ok": False, "error": "no answer from 192.168.1.20:7788 in 5 s: the firewall ..."}
        f = Feed(["192.168.1.20"])
        info = {"sw": tc.SUPPORTED, "mods": {}, "mods_code": "X", "addresses": []}
        checks, _ = tc.connection_both_ways(tc.Console(f.inp, f.out), "B", 7788, info,
                                            serve_fn=serve_fn, ping_fn=ping_fn)
        res = {c["id"]: c["result"] for c in checks}
        self.assertEqual(res["reach_out"], "FAIL")
        self.assertEqual(res["reach_in"], "FAIL")
        self.assertIn("Allow an app", [c for c in checks if c["id"] == "reach_in"][0]["detail"])

    def test_real_echo_through_the_flow_on_localhost(self):
        """B's side of the flow against a real listener (A) on 127.0.0.1."""
        port = free_port()
        a = ServeThread(EchoTests.INFO_A, port=port)
        f = Feed([LOCAL])
        info = {"sw": tc.SUPPORTED, "mods": {tc.GHOST_ID: "0.7.0"}, "mods_code": "Y", "addresses": [LOCAL]}

        def serve_fn(port, info, seconds, say):      # B's own wait: A calls back with a real ping
            out = {}
            srv = ServeThread(info, port=0)
            out["r"] = tc.ping(LOCAL, srv.port, EchoTests.INFO_A, count=2, retry_seconds=2)
            return srv.join()
        checks, peer = tc.connection_both_ways(tc.Console(f.inp, f.out), "B", port, info, serve_fn=serve_fn)
        self.assertTrue(a.join()["ok"])
        res = {c["id"]: c["result"] for c in checks}
        self.assertEqual(res["reach_out"], "PASS")
        self.assertEqual(res["reach_in"], "PASS")
        self.assertEqual(res["same_mods"], "PASS")
        self.assertEqual(peer["name"], "Ana")


class RecordTests(unittest.TestCase):
    def test_record_and_report_files(self):
        checks = [tc.check("savage_wind", "Savage Wind version", "PASS", tc.SUPPORTED),
                  tc.check("private", "Wi-Fi set to Private", "FAIL", "Public")]
        steps = [{"id": "host", "title": "Host", "result": "PASS", "notes": ""},
                 {"id": "items", "title": "Held items", "result": "FAIL", "notes": "no bow"}]
        rec = tc.make_record("Ana/..\\x", "A", {"library": "L", "install": "I"}, checks, steps, {"name": "Ben"},
                             {"sw": tc.SUPPORTED, "mods_code": "ABCD-1234"})
        self.assertEqual(rec["summary"]["overall"], "FAIL")
        self.assertEqual(rec["summary"]["counts"]["FAIL"], 2)
        with tempfile.TemporaryDirectory() as d:
            j, t = tc.save_record(rec, d)
            self.assertEqual(j.parent, Path(d))          # the name can't leave the folder
            back = json.loads(j.read_text(encoding="utf-8"))
            self.assertEqual(back["tool"], "twopc_check")
            self.assertEqual(back["steps"][1]["notes"], "no bow")
            text = t.read_text(encoding="utf-8")
        first = text.splitlines()
        self.assertIn("RESULT: FAIL", first[1])
        self.assertLess(text.index("Problems:"), text.index("All:"))
        self.assertIn("Held items: no bow", text)
        self.assertIn("ABCD-1234", text)

    def test_big_letters(self):
        self.assertEqual(len(tc.big("PASS").splitlines()), 5)
        self.assertIn("#", tc.big("FAIL"))


class MainTests(unittest.TestCase):
    def test_dry_run_prints_every_step_and_opens_nothing(self):
        f = Feed([])
        with mock.patch.object(tc.socket, "socket") as sk, mock.patch.object(tc.subprocess, "run") as run, \
                mock.patch.object(tc, "save_record") as save:
            rc = tc.main(["--dry-run"], tc.Console(f.inp, f.out))
        self.assertEqual(rc, 0)
        sk.assert_not_called()
        run.assert_not_called()
        save.assert_not_called()
        text = f.text()
        for _sid, _who, title, _t in tc.STEPS:
            self.assertIn(title, text)
        self.assertIn("Savage Wind is " + tc.SUPPORTED, text)

    def test_connect_needs_address_and_bad_port(self):
        f = Feed([])
        self.assertEqual(tc.main(["connect", "--library", "nowhere"], tc.Console(f.inp, f.out)), 2)
        self.assertIn("needs the other PC's address", f.text())
        self.assertEqual(tc.main(["--port", "0", "--dry-run"], tc.Console(f.inp, f.out)), 2)

    def test_whole_run_without_network_writes_report(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            inst = make_install(root)
            lib = make_library(root, [{"id": tc.GHOST_ID, "version": "0.7.0", "enabled": True}])
            answers = ["p", ""] * len(tc.STEPS)
            f = Feed(answers)
            with mock.patch.object(tc, "network_category",
                                      return_value=[{"kind": "Private", "name": "Wi-Fi", "ips": ["192.168.1.20"]}]), \
                    mock.patch.object(tc, "local_addresses", return_value=["192.168.1.20"]):
                rc = tc.main(["--role", "A", "--name", "Ana", "--library", str(lib), "--install", str(inst),
                              "--no-network", "--out", str(root / "out"), "--port", str(free_port())],
                             tc.Console(f.inp, f.out))
            files = sorted(p.suffix for p in (root / "out").iterdir())
            self.assertEqual(files, [".json", ".txt"])
            self.assertEqual(rc, 0, f.text())
            self.assertIn("SEND THESE TWO FILES", f.text())

    def test_whole_run_as_B_on_localhost(self):
        """The guided run with the network on: A is a real listener on 127.0.0.1; B's own wait is kept on
        127.0.0.1 (no firewall question) and short, so A's call back is missed: reach_in FAIL, the rest PASS."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            inst = make_install(root)
            lib = make_library(root, [{"id": tc.GHOST_ID, "version": "0.7.0", "enabled": True}])
            port = free_port()
            a = ServeThread(EchoTests.INFO_A, port=port)
            real = tc.serve

            def local_serve(port, info, **kw):
                kw.update(host=LOCAL, seconds=0.5)
                return real(port, info, **kw)
            f = Feed(["p", ""] * len(tc.STEPS))
            with mock.patch.object(tc, "serve", local_serve),                     mock.patch.object(tc, "network_category", return_value=[]),                     mock.patch.object(tc, "local_addresses", return_value=[LOCAL]):
                rc = tc.main(["--role", "B", "--name", "Ben", "--library", str(lib), "--install", str(inst),
                              "--friend", LOCAL, "--out", str(root / "out"), "--port", str(port)],
                             tc.Console(f.inp, f.out))
            self.assertTrue(a.join()["ok"])
            rec = json.loads(next((root / "out").glob("*.json")).read_text(encoding="utf-8"))
            res = {c["id"]: c["result"] for c in rec["checks"]}
            self.assertEqual(res["reach_out"], "PASS")
            self.assertEqual(res["reach_in"], "FAIL")
            self.assertEqual(res["same_mods"], "PASS")
            self.assertEqual(res["same_sw"], "PASS")
            self.assertEqual(rec["friend"]["name"], "Ana")
            self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
