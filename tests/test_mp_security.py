"""Multiplayer security fixes (docs/multiplayer-security.md): slow room-code proofs, 4-word codes, plain refusal of
older peers, relay slot caps, unique names and kick by id. Offline: only 127.0.0.x sockets."""
import asyncio
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mp  # noqa: E402
from mp import relay, security  # noqa: E402
import test_mp_session as base  # noqa: E402

OTHER_PC = "127.0.0.2"   # stands in for another PC: is_loopback is patched to say only 127.0.0.1 is this PC


def _this_pc_only(ip) -> bool:
    return str(ip).split("%")[0] in ("127.0.0.1", "::1")


class CodeTests(unittest.TestCase):
    def test_scrypt_proof_round_trip(self):
        nonce = security.new_nonce()
        fields, cnonce = security.code_hello("Apple River Stone", nonce)
        self.assertEqual(fields["kdf"], security.KDF)
        self.assertNotIn("code", fields)
        self.assertTrue(security.proof_matches("apple river stone", nonce, cnonce, fields["code_proof"]))
        self.assertFalse(security.proof_matches("apple river rock", nonce, cnonce, fields["code_proof"]))
        answer = security.code_answer("apple river stone", nonce, cnonce)
        self.assertTrue(security.answer_matches("apple river stone", nonce, cnonce, answer))
        self.assertNotEqual(answer, fields["code_proof"])

    def test_proof_is_not_the_old_fast_hmac(self):
        """The old proof was HMAC(code) and fell to a 2^24 search in a minute; the new key is scrypt(code, cnonce)."""
        import hashlib
        import hmac
        nonce, cnonce = security.new_nonce(), security.new_nonce()
        old = hmac.new(b"apple river stone", f"breathalyzer-join:{nonce}:{cnonce}".encode(),
                       hashlib.sha256).hexdigest()[:32]
        self.assertNotEqual(security.code_proof("apple river stone", nonce, cnonce), old)
        self.assertGreaterEqual(security.SCRYPT_N, 2 ** 14)
        security._code_key.cache_clear()
        t = time.perf_counter()
        security._code_key("lake moon star", cnonce)
        self.assertGreater(time.perf_counter() - t, 0.01)   # (slow on purpose: about 0.14 s on the dev laptop)

    def test_salt_is_the_friends_nonce(self):
        """The same code with two cnonces gives unrelated keys: a fake host can't build one table for everyone."""
        a, b = security.new_nonce(), security.new_nonce()
        self.assertNotEqual(security._code_key("apple river stone", a), security._code_key("apple river stone", b))

    def test_cheap_rejects_skip_scrypt(self):
        with mock.patch.object(security, "_code_key", side_effect=AssertionError("scrypt ran")):
            nonce = security.new_nonce()
            self.assertFalse(security.proof_matches("apple river stone", nonce, "zz", "0" * 32))
            self.assertFalse(security.proof_matches("apple river stone", nonce, nonce, "not hex"))
            self.assertFalse(security.proof_matches("apple river stone", nonce, nonce, "0" * 31))

    def test_four_word_codes(self):
        code = security.make_code(words=security.LONG_CODE_WORDS)
        self.assertEqual(len(code.split()), 4)
        self.assertEqual(security.normalize_code(code.upper()), code)
        self.assertEqual(len(security.make_code().split()), security.CODE_WORDS)
        self.assertEqual(security.normalize_code("Apple-river stone,MOON"), "apple river stone moon")
        for bad in ("apple river", "apple river stone moon lake", "apple river stone xyzzy"):
            self.assertIsNone(security.normalize_code(bad))
        with self.assertRaises(ValueError):
            security.make_code(words=5)

    def test_name_key_folds_look_alikes(self):
        for name in ("Ana", "ANA", "A.na", "a na", "Ána", "Аna", "аna", "Ａna"):
            self.assertEqual(security.name_key(name), "ana", name)
        self.assertNotEqual(security.name_key("Ana"), security.name_key("Anna"))
        self.assertEqual(security.name_key("Bo0b"), security.name_key("Boob"))


class RelaySecurityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = base.SessionRelayTests.asyncSetUp
    asyncTearDown = base.SessionRelayTests.asyncTearDown
    start = base.SessionRelayTests.start
    join = base.SessionRelayTests.join

    async def open_from(self, ip):
        r, w = await asyncio.open_connection("127.0.0.1", self.port, local_addr=(ip, 0))
        p = base.Peer(r, w)
        self.peers.append(p)
        return p

    async def hello_from(self, ip, name, room="default", key=None, expect="welcome"):
        p = await self.open_from(ip)
        msg = {"type": "hello", "version": 1, "name": name, "room": room, "features": base.FEATS}
        if key:
            msg["key"] = key
        await p.send(msg)
        w = await p.recv()
        self.assertEqual(w["type"], expect, w)
        p.welcome = w
        return p

    async def test_challenge_names_the_kdf(self):
        await self.start(room_code="apple river stone moon")
        await self.join("Ana", host=True)
        ben = await self.join("Ben", proof_code="Apple river stone MOON")
        self.assertTrue(security.answer_matches("apple river stone moon", ben.nonce, ben.cnonce, ben.welcome["code_ok"]))
        p = await base.Peer.open(self.port)
        self.peers.append(p)
        first = await p.recv()
        self.assertEqual(first.get("kdf"), security.KDF)

    async def test_older_peer_gets_update_message_and_no_strike(self):
        await self.start(room_code="apple river stone")
        await self.join("Ana", host=True)
        import hashlib
        import hmac
        for i in range(security.WRONG_CODE_TRIES + 1):   # old HMAC proofs (no kdf): told to update, never locked out
            p = await base.Peer.open(self.port)
            self.peers.append(p)
            nonce = (await p.recv())["nonce"]
            cnonce = security.new_nonce()
            proof = hmac.new(b"apple river stone", f"breathalyzer-join:{nonce}:{cnonce}".encode(),
                             hashlib.sha256).hexdigest()[:32]
            await p.send({"type": "hello", "version": 1, "name": f"Old{i}", "room": "default", "features": [],
                          "cnonce": cnonce, "code_proof": proof})
            w = await p.recv()
            self.assertEqual(w, {"type": "error", "error": security.OLD_PEER})
        await self.join("Ben", proof_code="apple river stone")

    async def test_relay_code_message_takes_four_words(self):
        await self.start()
        host = await self.join("Ana", host=True)
        await host.send({"type": "code", "code": "lake moon star sun"})
        await host.until_pong()
        self.assertEqual(self.relay.room_code, "lake moon star sun")

    async def test_unique_names_per_room(self):
        await self.start()
        await self.join("Ana", host=True)
        for dup in ("ana", "A.na", "Аna"):
            bad = await self.join(dup, expect="error")
            self.assertIn("taken", bad.welcome["error"])
        await self.join("Ana", room="ghosts")   # (another room: fine; the ghost line has the same name)

    async def test_kick_by_id_and_ambiguous_name(self):
        await self.start()
        await self.join("Ana", host=True)
        ben = await self.join("Ben")
        self.assertIs(self.relay.find_player(f"#{ben.id}"), self.relay.find_player(str(ben.id)))
        self.assertEqual(self.relay.find_player(f"#{ben.id}").name, "Ben")
        self.assertEqual(self.relay.find_player("BEN").id, ben.id)
        with mock.patch.object(relay.security, "is_loopback", _this_pc_only):
            await self.hello_from(OTHER_PC, "Ben", room="coop", key="ab" * 8)   # another player, another room
        self.assertIsNone(self.relay.find_player("ben"))   # two players fit: kick by id instead
        self.assertEqual(self.relay.find_player(ben.id).id, ben.id)
        self.assertIsNone(self.relay.find_player(True))

    async def test_caps_per_address_and_key(self):
        with mock.patch.object(relay.security, "is_loopback", _this_pc_only):
            await self.start()
            await self.join("Ana", host=True)
            await self.hello_from(OTHER_PC, "Ben", key="cd" * 8)
            await self.hello_from(OTHER_PC, "Ben", room="coop", key="cd" * 8)
            third = await self.hello_from(OTHER_PC, "Ben2", room="coop", key="cd" * 8, expect="error")
            self.assertIn("player key", third.welcome["error"])
            await self.hello_from(OTHER_PC, "Cy", key="ef" * 8)
            await self.hello_from(OTHER_PC, "Di")
            fifth = await self.hello_from(OTHER_PC, "Ed", expect="error")
            self.assertIn("too many connections", fifth.welcome["error"])
            await self.join("Fay")   # this PC is never capped by address

    async def test_only_host_rooms_from_other_pcs(self):
        with mock.patch.object(relay.security, "is_loopback", _this_pc_only):
            await self.start()
            await self.join("Ana", host=True, room="coop")
            bad = await self.hello_from(OTHER_PC, "Ben", room="a1", expect="error")
            self.assertIn("no room a1", bad.welcome["error"])
            await self.join("Ana", room="ghosts")              # this PC opened it ...
            await self.hello_from(OTHER_PC, "Ben", room="ghosts")   # ... so a friend may join it

    async def test_reserved_slots_for_this_pc(self):
        with mock.patch.object(relay.security, "is_loopback", _this_pc_only):
            await self.start(max_clients=5)
            await self.join("Ana", host=True)
            await self.hello_from(OTHER_PC, "Ben")
            full = await self.hello_from(OTHER_PC, "Cy", expect="error")   # 5 - 4 reserved: one other-PC slot
            self.assertEqual(full.welcome["error"], "relay full")
            await self.join("Ana", room="ghosts")   # this PC still gets in (the host's reconnect)


if __name__ == "__main__":
    unittest.main()
