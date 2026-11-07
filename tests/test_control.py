"""Tests for tools/control.py against a made-up control server on 127.0.0.1 (no game needed)."""
import json
import os
import socket
import socketserver
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import control  # noqa: E402


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        for raw in self.rfile:
            words = raw.decode().split()
            if words == ["ping"]:
                reply = {"ok": True, "data": {"pong": True}}
            elif words[:2] == ["mem", "read"]:
                reply = {"ok": True, "data": {"addr": words[2], "bytes": int(words[3]), "value.0": "42"}}
            elif words == ["drop"]:
                # close without answering, the way a game that quits or restarts the socket does
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            else:
                reply = {"ok": False, "error": f"Unknown command: {words[0]}"}
            self.wfile.write((json.dumps(reply) + "\n").encode())


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class ControlClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _Server(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_quote(self):
        self.assertEqual(control.quote("a b"), '"a b"')
        self.assertEqual(control.quote('x"y'), '"x\\"y"')
        self.assertEqual(control.quote(""), '""')
        with self.assertRaises(ValueError):
            control.quote("a\nb")

    def test_cmd_and_read(self):
        with control.Control(self.port) as c:
            self.assertEqual(c.cmd("ping"), {"pong": True})
            self.assertEqual(c.read(0x7001E13ED0, "u64"), 42)

    def test_error_reply(self):
        with control.Control(self.port) as c:
            with self.assertRaises(control.ControlError):
                c.cmd("nope")
            self.assertEqual(c.cmd("ping"), {"pong": True})   # the connection is still usable

    def test_batch_with_errors(self):
        with control.Control(self.port) as c:
            out = c.batch([["ping"], ["nope"], ["ping"]], errors=True)
            self.assertEqual(out[0], {"pong": True})
            self.assertIsInstance(out[1], control.ControlError)
            self.assertEqual(out[2], {"pong": True})

    def test_lost_connection_is_a_control_error(self):
        with control.Control(self.port) as c:
            with self.assertRaises(control.ControlError):
                c.cmd("drop")

    def test_no_game(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
        s.close()
        with self.assertRaises(control.ControlError):
            control.Control(free, timeout=1.0)
        with self.assertRaises(control.ControlError):
            control.wait_for_game(free, timeout=1.5)

    def test_wait_for_game(self):
        c = control.wait_for_game(self.port, timeout=5)
        self.assertEqual(c.cmd("ping"), {"pong": True})
        c.close()


if __name__ == "__main__":
    unittest.main()
