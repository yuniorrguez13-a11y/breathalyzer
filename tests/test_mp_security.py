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

    def test_challenge_from_another_build_is_not_answered(self):
        """F-MP1: no "kdf" = an older host (it would read a scrypt proof as a wrong code: a strike); another kdf = a
        different build. Both are stop reasons for the ghost client and the co-op agent, never retried."""
        from mp import client, coop_agent
        nonce = security.new_nonce()
        self.assertIsNone(security.challenge_refusal({"type": "challenge", "nonce": nonce, "kdf": security.KDF}))
        self.assertEqual(security.challenge_refusal({"type": "challenge", "nonce": nonce}), security.OLDER_HOST)
        self.assertEqual(security.challenge_refusal({"type": "challenge", "nonce": nonce, "kdf": "argon9"}),
                         security.OTHER_KDF)
        self.assertIsNone(security.challenge_refusal({"type": "challenge", "nonce": "zz"}))   # (no usable nonce)
        self.assertIsNone(security.challenge_refusal(None))
        for why in security.STOP_REASONS:
            self.assertTrue(any(g in why for g in client.GIVE_UP), why)
            self.assertTrue(coop_agent._fatal(why), why)
        self.assertIn("older", security.OLDER_HOST)

    def test_proof_answer(self):
        nonce = security.new_nonce()
        fields, cnonce = security.code_hello("apple river stone", nonce)
        self.assertEqual(security.proof_answer("apple river stone", nonce, cnonce, fields["code_proof"]),
                         security.code_answer("apple river stone", nonce, cnonce))
        self.assertIsNone(security.proof_answer("apple river rock", nonce, cnonce, fields["code_proof"]))

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
        self.assertEqual(len(self.relay.find_players("ben")), 2)   # (the host's terminal lists both)

    async def test_find_players_odd_digits_and_number_names(self):
        """F-MP3: "²" is a digit to isdigit() but not to int(): it is a name now, never a crash. A player called
        "42" can be kicked by name when 42 is nobody's id; "#42" is always an id."""
        await self.start()
        await self.join("Ana", host=True)
        num = await self.join("42")
        self.assertEqual(self.relay.find_players("²"), [])
        self.assertEqual([c.id for c in self.relay.find_players("42")], [num.id])
        self.assertEqual(self.relay.find_players("#42"), [])
        self.assertEqual(self.relay.find_player(f"#{num.id}").name, "42")
        self.assertEqual(self.relay.find_players("   "), [])

    async def test_host_terminal_shows_ids_and_both_players_on_one_name(self):
        """F-MP3: `players` shows #id; `kick Ben` with two different Bens (other key, other PC, other room) names
        both instead of "No player called Ben"; `kick #id` then hits the right one."""
        from mp import coop_agent
        stop = asyncio.Event()
        await self.start()
        await self.join("Ana", host=True)
        ben = await self.join("Ben", key="ab" * 8)
        with mock.patch.object(relay.security, "is_loopback", _this_pc_only):
            other = await self.hello_from(OTHER_PC, "Bén", room="coop", key="cd" * 8)
        rows = coop_agent.host_command("players", self.relay, stop)
        self.assertIn(f"#{ben.id} Ben  [default]", rows)
        self.assertIn(f"#{other.welcome['id']} Bén  [coop]", rows)
        said = coop_agent.host_command("kick ben", self.relay, stop)
        self.assertIn("2 players called ben", said)
        self.assertIn(f"#{ben.id} [default]", said)
        self.assertIn(f"#{other.welcome['id']} [coop]", said)
        self.assertEqual(self.relay.player_count(), 3)   # (nobody kicked)
        said = coop_agent.host_command(f"kick #{other.welcome['id']}", self.relay, stop)
        self.assertIn("Kicked", said)
        self.assertTrue(await other.closed())
        self.assertEqual(self.relay.find_player("ben").id, ben.id)

    async def test_caps_per_address_and_key(self):
        """F-MP5: one address holds 2 lines per player of a full room (siblings, CGNAT), not 4 in all."""
        with mock.patch.object(relay.security, "is_loopback", _this_pc_only):
            await self.start(room_cap=3)
            self.assertEqual(self.relay.max_per_address, 6)
            await self.join("Ana", host=True)
            await self.join("Ana", room="ghosts")   # (this PC opens a third room)
            await self.hello_from(OTHER_PC, "Ben", key="cd" * 8)
            await self.hello_from(OTHER_PC, "Ben", room="coop", key="cd" * 8)
            third = await self.hello_from(OTHER_PC, "Ben2", room="coop", key="cd" * 8, expect="error")
            self.assertIn("player key", third.welcome["error"])
            await self.hello_from(OTHER_PC, "Cy", room="coop", key="ef" * 8)   # 3 players behind one address
            await self.hello_from(OTHER_PC, "Cy", room="ghosts", key="ef" * 8)
            await self.hello_from(OTHER_PC, "Di", room="coop")
            await self.hello_from(OTHER_PC, "Di", room="ghosts")
            seventh = await self.hello_from(OTHER_PC, "Ed", expect="error")
            self.assertIn("too many connections", seventh.welcome["error"])
            await self.join("Fay")   # this PC is never capped by address

    async def test_three_players_behind_one_address_with_the_default_cap(self):
        with mock.patch.object(relay.security, "is_loopback", _this_pc_only):
            await self.start()
            await self.join("Ana", host=True)
            for name, key in (("Ben", "b1" * 8), ("Cy", "c1" * 8), ("Di", "d1" * 8)):
                await self.hello_from(OTHER_PC, name, room="coop", key=key)
                await self.hello_from(OTHER_PC, name, key=key)
            self.assertEqual(self.relay.player_count(), 7)

    async def test_reconnect_replaces_the_stale_line(self):
        """F-MP5 / finding 5: Wi-Fi dropped with no goodbye, the relay still holds the old line. The same key from
        the same address takes its place at once (the old line is told REPLACED), not "the name is taken, wait
        30 s"; the room keeps its state."""
        with mock.patch.object(relay.security, "is_loopback", _this_pc_only):
            await self.start()
            host = await self.join("Ana", host=True, room="coop")
            await host.send({"type": "time", "h": 7.0})
            await host.until_pong()
            old = await self.hello_from(OTHER_PC, "Ben", room="coop", key="cd" * 8)
            new = await self.hello_from(OTHER_PC, "Ben", room="coop", key="cd" * 8)
            self.assertEqual((await old.recv())["reason"], security.REPLACED)
            self.assertTrue(await old.closed())
            self.assertEqual(new.welcome["time"]["h"], 7.0)
            self.assertEqual([c.id for c in self.relay.rooms["coop"].values()], [host.id, new.welcome["id"]])
            got = await host.until_pong()
            self.assertEqual([m["type"] for m in got], ["join", "leave", "join"])   # (old Ben in, out, new Ben)
            # the key alone proves nothing: same key from another address, or another key, still waits
            taken = await self.hello_from("127.0.0.3", "Ben", room="coop", key="cd" * 8, expect="error")
            self.assertIn("taken", taken.welcome["error"])
            other = await self.hello_from(OTHER_PC, "Ben", room="coop", key="ee" * 8, expect="error")
            self.assertIn("taken", other.welcome["error"])
            nokey = await self.hello_from(OTHER_PC, "Ben", room="coop", expect="error")
            self.assertIn("taken", nokey.welcome["error"])

    async def test_both_stale_lines_come_back_at_the_key_cap(self):
        """Both of his lines went stale: each comes back in its own room (the old one doesn't count for
        MAX_PER_KEY), while a third, new line with that key is still refused."""
        with mock.patch.object(relay.security, "is_loopback", _this_pc_only):
            await self.start()
            await self.join("Ana", host=True, room="coop")
            await self.hello_from(OTHER_PC, "Ben", room="coop", key="cd" * 8)
            await self.hello_from(OTHER_PC, "Ben", key="cd" * 8)
            await self.hello_from(OTHER_PC, "Ben", room="coop", key="cd" * 8)
            await self.hello_from(OTHER_PC, "Ben", key="cd" * 8)
            third = await self.hello_from(OTHER_PC, "Benny", key="cd" * 8, expect="error")
            self.assertIn("player key", third.welcome["error"])
            self.assertEqual(self.relay.player_count(), 3)

    async def test_host_line_replaced_only_by_the_host(self):
        await self.start()
        host = await self.join("Ana", host=True, room="coop", key="aa" * 8)
        fake = await self.join("Ana", room="coop", key="aa" * 8, expect="error")   # same key, no host proof
        self.assertIn("taken", fake.welcome["error"])
        again = await self.join("Ana", host=True, room="coop", key="aa" * 8)    # the host's agent reconnects
        self.assertTrue(again.welcome["you_host"])
        self.assertTrue(await host.closed())
        self.assertEqual(self.relay.room_info["coop"]["host"].id, again.id)

    def slow_scrypt(self, seconds):
        real = security._code_key

        def slow(code, cnonce):
            time.sleep(seconds)
            return real(code, cnonce)
        return mock.patch.object(security, "_code_key", slow)

    async def proof_hello(self, name, code="apple river stone"):
        """Open a line, read the challenge, send a ready proof (made before any slow scrypt patch)."""
        p = await base.Peer.open(self.port)
        self.peers.append(p)
        p.nonce = (await p.recv())["nonce"]
        fields, p.cnonce = security.code_hello(code, p.nonce)
        p.hello = dict({"type": "hello", "version": 1, "name": name, "room": "default", "features": []}, **fields)
        return p

    async def test_room_code_proof_runs_off_the_event_loop(self):
        """F-MP2: the scrypt ran inside the relay's loop: every join froze every room (~0.14 s; 14 reconnects =
        2 s). Now it runs on a worker thread: the host's ping is answered while a friend's proof is checked."""
        await self.start(room_code="apple river stone")
        host = await self.join("Ana", host=True)
        ben = await self.proof_hello("Ben")
        with self.slow_scrypt(0.6):
            await ben.send(ben.hello)
            await asyncio.sleep(0.05)   # (the proof is running now)
            t = time.perf_counter()
            await host.until_pong()
            self.assertLess(time.perf_counter() - t, 0.4)
            w = await ben.recv()
        self.assertEqual(w["type"], "welcome")
        self.assertTrue(security.answer_matches("apple river stone", ben.nonce, ben.cnonce, w["code_ok"]))

    async def test_busy_relay_and_per_address_proof_budget(self):
        with mock.patch.object(relay, "PROOF_QUEUE", 1), mock.patch.object(relay, "PROOFS_PER_MINUTE", 3):
            await self.start(room_code="apple river stone")
            await self.join("Ana", host=True)
            ben, cy = await self.proof_hello("Ben"), await self.proof_hello("Cy")
            with self.slow_scrypt(0.4):
                await ben.send(ben.hello)
                await asyncio.sleep(0.05)
                await cy.send(cy.hello)
                busy = await cy.recv()
                self.assertEqual(busy, {"type": "error", "error": relay.BUSY})
                self.assertEqual((await ben.recv())["type"], "welcome")
            from mp import coop_agent
            self.assertTrue(coop_agent._not_ready(relay.BUSY))   # (a friend's first join tries again)
            self.assertFalse(coop_agent._fatal(relay.BUSY))
            dee = await self.proof_hello("Dee")                  # the 3rd proof this minute from 127.0.0.1
            await dee.send(dee.hello)
            self.assertEqual((await dee.recv())["type"], "welcome")
            eve = await self.proof_hello("Eve")
            await eve.send(eve.hello)
            self.assertIn("too many joins", (await eve.recv())["error"])
            self.clock.now += 61
            fay = await self.proof_hello("Fay")
            await fay.send(fay.hello)
            self.assertEqual((await fay.recv())["type"], "welcome")

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
