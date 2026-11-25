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
- That scrypt runs on a worker thread (`asyncio.to_thread(security.proof_answer, ...)`), one at a time, never in the
  relay's event loop: a join (or 14 reconnects after a Wi-Fi blip) no longer freezes every room's ghosts and host
  commands. At most `PROOF_QUEUE = 16` hellos wait for it; more are told "the relay is busy checking room codes; try
  again in a few seconds". One address gets `PROOFS_PER_MINUTE = 20` proofs, right or wrong, so someone who knows
  the code can't keep the relay busy by joining in a loop. The checks run again after the proof (the room may have
  changed while it ran); a code changed meanwhile means "try again".

**Older peers are refused, with a plain message.** Accepting their answers isn't safe, because any answer they
send (even to a fake host) can be cracked offline in a minute. A hello without `"kdf"`, or one with the plain-text
`code`, gets `security.OLD_PEER`: "this room needs a newer Breathalyzer: please update Breathalyzer and try
again". It is not counted as a wrong code. Rooms without a code don't change. The protocol version stays 1.

**A new friend and an older host.** `client.py` and `coop_agent.py` read the challenge first
(`security.challenge_refusal`). A nonce without `"kdf"` means the host's Breathalyzer is older: the friend sends no
proof (that host would read it as a wrong room code and give a strike) and stops with `security.OLDER_HOST` ("the
host's Breathalyzer is older: ask them to update Breathalyzer ..."). Another `"kdf"` gives `OTHER_KDF` ("update both
PCs to the same Breathalyzer"). They never fall back to the old answer, or a fake host could force a downgrade.
`OLD_PEER`, `OLDER_HOST` and `OTHER_KDF` are in `security.STOP_REASONS`, which the client's `GIVE_UP` and the
agent's `FATAL` include, so none of them is retried. A PAKE (CPace/SPAKE2) is still the real long-term fix.

## Relay slots (#4)

Before: one peer could fill all 64 slots (any room name opened a new room), and the host's own reconnect was
refused.

- `RESERVED_LOCAL = 4`: only this PC (127.0.0.1, the host's own lines and reconnects) can take the last 4 slots.
- `Relay.max_per_address = LINES_PER_PLAYER x room_cap` (2 x 8 = 16): connections from one other address, joined or
  not (pending ones count). It used to be 4, which let only 2 players in from one public address (siblings at home,
  a mobile carrier's CGNAT): each player uses 2 lines. It still stops one address from filling the 60 other slots.
- `MAX_PER_KEY = 2`: one player key from another PC can have 2 joined connections (the co-op line and the ghost line).
- **A reconnect replaces the stale line.** When Wi-Fi drops with no goodbye, the relay keeps the old line until it
  times out (30 s). A hello with the same valid key AND the same address as the line holding its name in that room
  closes the old line (`security.REPLACED`) and takes its place, instead of "the name is taken ... wait 30 s" or
  "too many connections with your player key" (the old line no longer counts). The key alone
  is not enough (fingerprints are not secret). A host's line is replaced only by a hello that proves the host. A
  live old line (a second copy of the same agent) stops on `REPLACED` (it is in `STOP_REASONS`), so two copies can't
  push each other out forever. Ghost lines carry no key, so they still wait the 30 s.
- With a host secret (a co-op session), another PC can only open `HOST_ROOMS` ("coop", "default") or a room this
  PC has already opened. Anything else gets "no room X in this session".
- This PC is never capped by address. The old `MAX_PENDING_PER_ADDRESS` (4 not-yet-hello connections) stays.
- Don't put a local tunnel or proxy (ssh -R, playit-style agents) in front of the relay: every friend would then come
  from 127.0.0.1, which every cap here trusts as this PC.

## Names and kicks (#6)

- Names are unique per room by `security.name_key`. It applies NFKC, case folding, look-alike folding (Cyrillic and
  Greek letters that look Latin, 0/o, 1/l), drops accents and drops separators (space `_ . ' -`). So "Ana", "A.na",
  "Ána" and "Аna" (Cyrillic А) all clash. A duplicate gets "the name X is taken in this room: pick another (if it's
  you, wait 30 s)". The same name in another room is fine (the ghost line).
- `Relay.find_players` takes an id: `#5`, `5` or the int (ASCII digits only: "²" is read as a name, it used to crash
  the command). A number that is nobody's id (a player called "42") is read as a name; `#42` is always an id. Names
  match by `name_key`; connections of one player (same key, same address) count once. `find_player` is the one
  player, or nothing when a name fits two players.
- The host's terminal (`coop_agent.py`): `players` shows `#id name [room]`, and `kick Ana` when two different
  players are called Ana answers "2 players called Ana: #3 [coop], #7 [default]. Type kick #id." instead of "No
  player called Ana".
