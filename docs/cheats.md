# Cheats (Atmosphere format)

`tools/cheats.py` runs Switch cheat files (Atmosphere `.txt` codes) against Savage Wind. It runs outside the
game and changes the game's memory through Savage Wind's control port. Data cheats (values, pointer chains)
work. Cheats that patch game code do nothing.

## Quick start

1. Start the game with the control port on (PowerShell):

   ```powershell
   $env:SAVAGEWIND_CONTROL = '7777'
   & "$HOME\Documents\SavageWind\Savage Wind\savagewind.exe"
   ```

   `savagewind.log` should show `[control] listening on 127.0.0.1:7777`.

2. See what the file has and what can run:

   ```
   python tools/cheats.py list cd57b23fa4bbad65.txt
   ```

3. Run some cheats (names or the numbers from `list`), or all of them:

   ```
   python tools/cheats.py run cd57b23fa4bbad65.txt --enable "Infinite Stamina" 3
   python tools/cheats.py run cd57b23fa4bbad65.txt --all
   ```

   It prints one `Started:` line per cheat, then runs until you press Ctrl+C or close the game.

Cheat files for BotW 1.9.0 are named `cd57b23fa4bbad65.txt` (the first 16 hex digits of the build id), usually
under `atmosphere/contents/01007EF00011E000/cheats/`. The tool warns when a file is named for another version
(1.8.2 is `de0b6ac4efc1dda7`): its addresses will be wrong.

## Options

| Option | Meaning |
|---|---|
| `--port 7777` | The port in `SAVAGEWIND_CONTROL` |
| `--enable NAME ...` | Cheats to run, by name, part of a name, or number from `list` |
| `--all` | Run every cheat that can run |
| `--force` | Run the other cheats even when the master code can't run (they may write to the wrong places) |
| `--hz 12` | Ticks per second (Atmosphere also uses 12) |
| `--once` | Run one tick and exit |
| `--wait N` | Wait up to N seconds for the game to open its port (default 0: don't wait) |
| `--timeout 30` | Give up when the game doesn't answer for that long |
| `--hotkeys ZL=SHIFT,B=J` | Map Switch buttons to keyboard keys for keypress cheats (Windows only) |
| `--main PATH` | The game's `extracted\exefs\main` (or its folder). Cheats that only patch game code are refused, and code writes in the others are skipped. Only the 256-byte header is read. |

The master code (`{Name}`) always runs, first. The chosen cheats then run in file order, whatever order you
give to `--enable`, because each cheat starts with the registers the one before it left. In a file with
[groups](#groups), all of this holds for each group. Without `--enable` or `--all`, only the master code and the
cheats turned on in a `toggles.txt` next to the file run (Atmosphere's format: `[Name]` then `true`). If no cheat
is on, the tool says so and exits. Note that Atmosphere turns every cheat on by default; this tool turns them on
only when you ask.

The other cheats usually use values the master code puts in registers (a pointer or an offset). So:

- If the master code can't run (an unknown opcode, a bad word, or a second master code in the file), `run`
  refuses to start. Fix the file, or add `--force` to run the other cheats without it. In a file with groups,
  only that group's cheats are skipped.
- If the master code is stopped while running (an endless loop), the other cheats stop too (in a file with
  groups, the other cheats of its group).
- An empty master section (`{Master Code}` with no code) counts as no master code.

`list` shows a second master code as `M2` with the reason it can't run. Atmosphere refuses such a file.

## Groups

A whole line `// group: <name>` starts a group (Atmosphere reads it as a comment; `group:` matches in any case).
The cheats after it, up to the next such line, belong to that group. `tools/modman.py` merges several mods' cheats
this way, one group per mod. Each group runs as if it were its own cheat file:

- It may have one master code. A second one can't run: "a second master code in its group; only one is allowed
  per group".
- Its registers and saved registers start at 0 at the start of the group, every tick, and it has its own static
  registers.
- Groups run in file order, each with its master code first. A group's master code runs only when at least one
  cheat of that group is on.
- A master code that can't run skips only its group: `run` prints `Skipped: <cheat> (the master code of its group
  can't run, group <name>)` and starts the rest. `--force` runs those cheats without the master code.
- A master code that is stopped (an endless loop) stops only its group.

`list` shows a `group: <name>` line before each group's cheats and adds `, in N groups, M master code(s)` to its
summary. A group's master code shows `yes` or `no` in the `on` column (a file without groups shows `always`).
Cheat numbers go on across groups. `run` prints `Started: <master> [master of group <name>]`. A file without group
lines runs as before.

## What works

- Opcodes 0-7, 9, A, C0-C4, FF0, FF1 and FFF from Atmosphere's public cheat spec, with every variant: memory
  stores and loads, conditionals with Else, nested blocks, loops, arithmetic (integer and float), register
  conditionals, save/restore, static registers and debug logs.
- Memory regions:

  | Cheat region | Savage Wind address | Notes |
  |---|---|---|
  | Main | `0x7000000000` | Exact: main sits where the code expects it |
  | Heap | `0x7240000000` | Savage Wind's heap; fixed heap offsets may not match the console |
  | Alias | `0x7100000000` | |
  | Aslr | `0x7000000000` | Savage Wind has no separate ASLR base, so this is main |
  | Type 4 (absolute) | the address as written | |

- Pointer-chain cheats (load a pointer from main, then add offsets) are the most likely to work, because the
  pointers they read are real ones.
- Memory is read in 256-byte blocks. A block is kept for the rest of the tick, so reading it again gives the
  same bytes, and a cheat sees its own earlier writes. Each new block is fetched in its own game frame, so reads
  of different blocks in one tick can come from different frames.
- Writes are sent at the end of the tick, up to 64 at a time (the game's limit). Up to 64 writes land in the
  same game frame; more spread over several frames.
- Registers carry over from the master code to the cheats, and from each cheat to the next, within a tick (in a
  file with groups, within a group). They start at 0 every tick, as in Atmosphere. Static registers keep their
  values.

## Limits

- **Code patches do nothing.** Savage Wind runs recompiled game code, so writing ARM instructions into main's
  code changes nothing. Many BotW cheats are code patches. Use `--main` to find them; such mods need to be
  ExeFS patches applied before the game code is rebuilt.
- **Heap-relative cheats may miss.** Savage Wind lays out its heap its own way. Pointer chains are fine; fixed
  heap offsets probably aren't.
- **Keypress cheats (opcodes 8 and C4)** can't see the game's real buttons. They count as "not pressed" unless
  you map keyboard keys with `--hotkeys`. Keys are read for the whole desktop, so pick keys the game doesn't
  use (F5-F8, numpad). `list` marks these cheats "partial".
- **Pause/resume (FF0/FF1)** is ignored: the game keeps running. A cheat that pauses so that its reads and
  writes happen while the game is still may see values change between reads, and more than 64 writes spread
  over several frames.
- **Speed:** every memory block the tool hasn't read yet this tick waits for one game frame. A cheat with a long
  pointer chain at 30 FPS can run slower than `--hz`.
- Reads that fail (for example on the title screen, before a pointer is set) give 0, and failed writes are
  dropped. Each is reported once per cheat; the cheat keeps trying.
- A cheat that runs more than 100,000 steps in one tick (an endless loop, such as a loop count of 0) is stopped.
- Static registers 0x00-0x7F (set by a host app on the Switch) always read 0 here.
- Unknown opcodes (B, C5 and up, D, E, F0x-FEx, FF2-FFE) refuse their cheat with a clear message. Other cheats
  still run, unless the refused cheat is the master code (see above).
- A cheat name ends at its matching `]` (or `}`), so `[Hearts [v2]] // note` is named `Hearts [v2]`. Text after
  the name that isn't code or a comment (`#`, `//`, `;`) is ignored with a warning.
- The control port is a developer feature: while it is on, any program on this PC can read and write the
  game's memory. It only listens on 127.0.0.1.
- **Tested in game** on 2026-11-06: a pointer-chain cheat that sets Link's height held him in the air at 12 Hz
  and let go when stopped, and a stamina cheat started by `modman run` kept stamina full while sprinting. Keypress
  cheats (`--hotkeys`) and groups haven't run in the game yet; the tests cover them against a fake game.
