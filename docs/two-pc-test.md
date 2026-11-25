# Two-PC test

Multiplayer and Story together have only been tried on one PC (with a bot). This test checks them on two real
PCs. You need two people, two PCs, and about 30 minutes.

**PC A** hosts the world. **PC B** is the friend who joins. Each of you runs the checker on your own PC. It asks
questions in a console (no windows), and at the end writes two small files you send to each other.

## Before you start (both)

- Breathalyzer Plus unpacked, Savage Wind **0.4.1-alpha.4** set up (not 0.5.x), and a save of your own (play once
  and save).
- Both PCs on the same Wi-Fi or router. Windows must call that Wi-Fi **Private**: Settings > Network & internet >
  your Wi-Fi > **Private network**.
- A phone call or a chat open between you: you do the steps at the same time.
- **Don't press Host yet.** The checker uses the same port (7788) as Host. Close any open multiplayer first.

## Start the checker

In the Breathalyzer folder: click the address bar of the folder window, type `cmd`, press Enter. In the black
window, paste this and press Enter:

    app\runtime\python.exe app\tools\mp\twopc_check.py

(From the source folder instead: `python tools\mp\twopc_check.py`.)

To only see the steps, without checking anything: add `--dry-run`.

## What it does

**Part 1, this PC** (each of you, on your own):

| Check | PASS means |
| --- | --- |
| Savage Wind version | the exe says 0.4.1-alpha.4 |
| A save of your own | there is a save next to savagewind.exe |
| Mod library | Breathalyzer's mod list is found and can be read (it shows a "mods code" like `3F9A-12C0`) |
| Ghost multiplayer on | the ghost mod is turned on |
| This PC's home address | the PC has an address like 192.168.x.x |
| Wi-Fi set to Private | Windows calls the network Private |
| Port 7788 free | nothing else (like an open Host) uses it |

**Part 2, between the PCs.** A tiny "hello, hello back" on port 7788, both ways:

1. PC A says **YOUR ADDRESS: 192.168.x.x** and waits. A tells B that address.
2. PC B types A's address. B's PC calls A's PC. Then B waits.
3. A's PC calls B back by itself (it learned B's address in step 2).

Windows may ask "allow Python on networks?" the first time a PC waits: press **Allow** (Private ticked). Until
you do, the other PC's call gets no answer, so it keeps calling for up to 90 seconds. This is the same Python
Breathalyzer uses, so allowing it now also helps Host later.

Then it shows: the **delay** between the PCs (under 30 ms is good at home), how far apart the two **clocks** are,
whether you both have the **same Savage Wind**, and whether you have the **same mods**. Ghost multiplayer and the
characters must be the same version on both PCs (FAIL if not); other different mods are a WARN.

**Part 3, the game.** It shows one step at a time. Do it together, then type what YOU saw:
`p` = PASS (it worked), `f` = FAIL (it didn't), `s` = SKIP (couldn't try), `q` = stop here. Then a short note
(what you saw), or just Enter.

| # | Step | Who |
| --- | --- | --- |
| 1 | Get ready: Ghost multiplayer on, Who am I picked (not Link), Wi-Fi Private | both |
| 2 | Host: A presses Host, Allows Python, Start the game, Continue | A |
| 3 | Join: B presses Join, check words match A's page, picks A's world, Story together: Yes | B |
| 4 | See each other: as the picked character, moving and running the same | both |
| 5 | Held items: sword, shield, bow show in the other's hands; one swing with a real weapon | both |
| 6 | Story at join: B's map has A's towers, quests, freed Divine Beasts, shrine warps | B |
| 7 | Tower live: A activates a tower B doesn't have; it shows on B's map within a minute | A |
| 8 | Memory live: A finds a memory B doesn't have (Skip if none is close) | A |
| 9 | Stop: A presses Stop; B's page says the session ended | A |
| 10 | Own save unchanged: both close the game, Play (not Multiplayer), load your own save | both |

Only towers and memories are shared live today; quests, Divine Beasts and shrines come over when B joins. So steps 7
and 8 are the live part.

## When it's done

It prints a big **PASS**, **WARN** or **FAIL**, the problems, and two file names in
`Documents\Breathalyzer two-PC test`:

- `two-PC test <name> <A or B> <date>.txt`: the short report (results first)
- `... .json`: the same, for the tools

**Send both files** to each other and to giruka. Nothing is sent by itself. Folders in them start at `~` (your
home folder), so they don't carry your Windows user name.

## If something fails

| It says | Do this |
| --- | --- |
| Savage Wind version: FAIL | Get Savage Wind 0.4.1-alpha.4 (Breathalyzer's version screen has the link) |
| Mod library: FAIL | Start Breathalyzer once, or give the folder: `--library <...\modloader>` |
| Ghost multiplayer on: FAIL | Home > Multiplayer > Ghost multiplayer > Turn on |
| Wi-Fi set to Private: FAIL | Settings > Network & internet > your Wi-Fi > Private network |
| Port 7788 free: WARN | Stop Host (or close Breathalyzer), then run the checker again |
| Your friend can reach this PC: FAIL | On THIS PC: Windows Security > Firewall & network protection > Allow an app > tick Python for Private. Wi-Fi Private. Then run again |
| This PC can reach your friend: FAIL "nothing listens" | Your friend's checker wasn't waiting yet: run again, in order |
| This PC can reach your friend: FAIL "no answer" | Your friend's firewall blocks it (the line above, on the friend's PC) |
| isn't a home-network address | Multiplayer is home network only: use the 192.168.x.x address |
| Same mods: FAIL | Both use the same Breathalyzer Plus zip, then Turn on Ghost multiplayer on both |
| Delay: WARN or FAIL | Move closer to the router, or use a cable; stop big downloads |
| Clocks agree: WARN | Not a problem for the game. Settings > Time > Set time automatically |

Ctrl+C stops waiting for the friend (the test goes on).

## Options

    --dry-run              only print the steps
    --role A | B           skip the "are you A or B" question
    --name NAME            your name in the report
    --friend ADDRESS       the friend's address (skips that question)
    --port N               another port (both PCs must use the same one)
    --library FOLDER       the mod library (modloader folder), if not found
    --install FOLDER       the Savage Wind folder, if not found
    --out FOLDER           where the report goes
    --no-network           skip Part 2
    --no-game              skip Part 3
    listen                 only Part 2's waiting side
    connect ADDRESS        only Part 2's calling side

## What goes over the network

Only between your two PCs, only on a home network (addresses like 192.168.x.x; any other address is refused):
your name, your Savage Wind version, the list of mods you have on (names and versions), your PC's home
addresses, and the time. Nothing goes to the internet. The game and Breathalyzer are not started or changed.

For developers: `tools/mp/twopc_check.py`, tests in `tests/test_twopc_check.py` (127.0.0.1 only). The echo is one
JSON line each way (`{"t":"brz2pc","v":1,"n":..,"ts":..,"info":{..}}`, at most 16 KB, at most 20 lines per
connection); the waiting side stops after one good friend or 3 minutes.
