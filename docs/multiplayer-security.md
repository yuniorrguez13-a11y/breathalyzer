# Multiplayer security fixes (part 1)

Fixes for findings #2, #4 and #6 of the multiplayer security review (2026-11-18). Code: `tools/mp/security.py`,
`tools/mp/relay.py`. Tests: `tests/test_mp_security.py`, `tests/test_mp_session.py`. The base design is in
`docs/coop.md`; everything still runs on the players' own PCs (no project servers).

## Room codes: slow proofs, 4-word codes (#2)

Before: the friend's join answer was `HMAC(code, nonce)`. A fake host that caught one answer could try all
16.7 million 3-word codes in about a minute.

Now the HMAC key is `scrypt(code, salt = "breathalyzer-code1:" + the friend's own nonce, n=2^14, r=8, p=1)`.

- Cost, measured on the dev laptop: n=2^14 takes 0.14 s and 16 MB (n=2^15 took 0.29 s). We picked 2^14 so a weak
  laptop still joins quickly. Each offline guess now costs that much: about 27 CPU-days for a 3-word code and
  thousands of CPU-years for a 4-word one.
- The salt is the friend's random nonce, so a fake host can't build one table and reuse it on everyone.
- Codes can be **3 or 4 words** (`security.make_code(words=4)`, `LONG_CODE_WORDS`). `normalize_code` and the relay
  (`code` message, `room_code`) accept both. Use 4 words for anything beyond a trusted home network.
- The challenge says `"kdf": "scrypt1"` and new hellos send `"kdf": "scrypt1"` (from `security.code_hello`).
- The relay rejects bad-looking answers with cheap checks before it runs scrypt. A blocked address (too many
  wrong codes) is refused before scrypt runs too. One join costs the host about 0.14 s.

**Older peers are refused, with a plain message.** Accepting their answers isn't safe, because any answer they
send (even to a fake host) can be cracked offline in a minute. A hello without `"kdf"`, or one with the plain-text
`code`, gets `security.OLD_PEER`: "this room needs a newer Breathalyzer: please update Breathalyzer and try
again". It is not counted as a wrong code. Rooms without a code don't change. The protocol version stays 1.

Still open: a new client talking to an **old host** gets "wrong room code", because that host can't check the new
answer. `client.py` and `coop_agent.py` should look at the challenge: no `"kdf"` there means "the host's
Breathalyzer is older: ask them to update". They must never fall back to the old answer, or a fake host could
force a downgrade. A PAKE (CPace/SPAKE2) is still the real long-term fix.

## Relay slots (#4)

Before: one peer could fill all 64 slots (any room name opened a new room), and the host's own reconnect was
refused.

- `RESERVED_LOCAL = 4`: only this PC (127.0.0.1, the host's own lines and reconnects) can take the last 4 slots.
- `MAX_PER_ADDRESS = 4`: another PC can hold at most 4 connections, joined or not (pending ones count).
- `MAX_PER_KEY = 2`: one player key from another PC can have 2 joined connections (the co-op line and the ghost line).
- With a host secret (a co-op session), another PC can only open `HOST_ROOMS` ("coop", "default") or a room this
  PC has already opened. Anything else gets "no room X in this session".
- This PC is never capped by address. The old `MAX_PENDING_PER_ADDRESS` (4 not-yet-hello connections) stays.

## Names and kicks (#6)

- Names are unique per room by `security.name_key`. It applies NFKC, case folding, look-alike folding (Cyrillic and
  Greek letters that look Latin, 0/o, 1/l), drops accents and drops separators (space `_ . ' -`). So "Ana", "A.na",
  "Ána" and "Аna" (Cyrillic А) all clash. A duplicate gets "the name X is taken in this room: pick another (if it's
  you, wait 30 s)". The same name in another room is fine (the ghost line).
- `Relay.find_player` takes an id: `#5`, `5` or the int. Names match by `name_key`. If a name fits connections of
  more than one player, it returns nothing, so `kick Ana` can't hit the wrong Ana. Kick by id then.

Still open: `players` on the host's terminal (`coop_agent.py`) and the launcher's player list should show the id
(`relay.players()` already returns it), so the host can type `kick #5`.
