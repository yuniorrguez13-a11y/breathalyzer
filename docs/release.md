# Making a release

`tools/make_release.py` makes the zips that are safe to share. Never game files, never mods made by other people,
never the stress-test mods.

| Download | File | For | What's in it |
|---|---|---|---|
| **Breathalyzer** | `breathalyzer-<version>.zip` | players who want no mods | `Start Breathalyzer.exe`, `READ ME FIRST.txt`, `app\` (our own Python, the tools, docs, `modhost.dll`) |
| **Breathalyzer Plus** | `breathalyzer-plus-<version>.zip` | players who want our mods | the same, plus our own mods to pick from before the first Play |
| source | `breathalyzer-source-<version>.zip` | developers | the repo as it is (what this tool made before the player zips) |

Players need no Python: the zips bring their own (see [The runtime](#the-runtime-players-need-no-python)).
How players use them: [launcher](launcher.md).

## Build the player zips

Once per release, on the build PC, in this order:

1. Commit the finished code. The player zips come from one commit, never from the working tree (see below).
2. `python tools/packaging/build_runtime.py` makes our own Python in `out\packaging-work\runtime` and records it.
3. `python tools/packaging/build_starter.py` makes `Start Breathalyzer.exe` in `out\packaging-work\starter`.
4. Then:

```
python tools/make_release.py --edition players --version 0.2.0-beta.1 --dry-run   # list both; write nothing
python tools/make_release.py --edition players --version 0.2.0-beta.1             # write both
python tools/make_release.py --edition core --version 0.2.0-beta.1                # only the one without mods
python tools/make_release.py --edition plus --version 0.2.0-beta.1                # only the one with our mods
python tools/make_release.py --edition plus --version 0.2.0-beta.1 --with-experimental   # + mods not ready yet
python tools/make_release.py --dry-run                                             # the source zip: list only
python tools/make_release.py --version 0.1.0-dev                                   # the source zip
python tools/make_release.py --version 0.1.0-dev --with-binaries                   # + the DLLs we built
```

**The player zips come from a commit.** `make_release.py` unpacks `--commit` (default `HEAD`) with `git archive`
into `out\packaging-work\src\<commit>\` and builds `modhost.dll`, the example mods and `modmenu.dll` from that copy
(its own `build_native.py` and `modmenu.py`, low priority) into its own `build\native`. Uncommitted files, other
jobs' half-done work and whatever `build\native` holds now never get in. The commit id is the first line of each
`SHA256SUMS.txt` (a `# ` comment, which `sha256sum -c` skips) and `"commit"` in `bundled-mods.json`.

- `--overlay <file>` (repeat it) lays one uncommitted file over the commit, for a test build of a fix that isn't
  committed yet. Each is listed with its SHA-256 in the first line of `SHA256SUMS.txt`, which then says "A test
  build: not for upload".
- `--working-tree` takes the working tree and `build\native` as they are (the tests use it); also stamped "not for
  upload".
- `--install <Savage Wind folder>`: whose zig builds the DLLs (default: as `build_native.py`).

The player zips leave out what only developers need (it stays in the source zip): `docs/PLAN.md`,
`docs/release.md`, `tools/make_release.py`, `tools/check_clean.py`, and the test drivers that start the game
(`*_ingame_check.py`, `*_items_check.py`).

The zips land in `out\release\`. `out\` is ignored by git.
Without `--version`, the version is the `VERSION` line at the top of the tool.
`--out` can name another folder: one outside the project, or one under `out\`.
A folder inside the project is refused, because the next release would pack this one.
`--runtime` and `--starter` name another runtime folder or starter (default: `out\packaging-work\...`).
With `--edition players`, both zips are checked first; if one is refused, neither is written.

Exit codes:

- 0: written (or, with `--dry-run`, it would be).
- 1: refused. Every reason is listed.
- 2: bad arguments (a bad version, an `--out` inside the project, `--with-binaries` with a player zip,
  `--with-experimental` without the plus zip).
- 3: the files couldn't be written. For example, the old zip is open in another program, or the disk is full.
  The message says what is left. No `.tmp` file stays behind.

## What's in the player zips

```
breathalyzer-plus-0.2.0-beta.1\
  Start Breathalyzer.exe            the program players open (28 KB, ours)
  READ ME FIRST.txt                 unpack, start, point at Savage Wind, Play
  SHA256SUMS.txt
  app\
    runtime\                        our own Python 3.13 and Tk (python.org's files)
    tools\  docs\  native\  assets\  castle\  README.md  LICENSE  NOTICE.md
    build\native\modhost.dll        the loader (not a mod)
    Start Breathalyzer (backup).cmd starts it without the exe, if an antivirus holds the exe back
    bundled-mods\<name>\            plus only: each of our ready code mods (mod.toml + DLL)
    bundled-mods.json               plus only: the list the mod picker shows
```

`app\` is what the tools call the repo folder, so `build\native` and `native\examples` are found as in the repo.
Never in a player zip: `tests\`, the native `selftest\` folders, `.github\`, `tools\packaging\`, `.gitignore`,
`.cleanallow`. A file `.cleanallow` lists is refused there: `check_clean.py` on the unpacked zip has no `.cleanallow`
and would block it.
The core zip has no DLL but `modhost.dll` and the runtime's; the plus zip adds exactly the bundled mods' DLLs.

## What goes in

Only an allow-list:

- `tools/`, `native/`, `docs/`, `tests/`, `.github/`, `assets/` (the app icon)
- `README.md`, `LICENSE`, `NOTICE.md`, `.gitignore`, `.cleanallow`
- any other file git tracks

Inside those, `.gitignore` still counts: what git ignores stays out.
So git is needed. If git is missing or fails (for example "dubious ownership"), the tool refuses
and shows git's message.
These never go in, whatever git says, in any letter case: `out/`, `build/`, `upstream/`, `__pycache__`, `.pyc` files.
The source zip holds all of it in one folder, `breathalyzer-source-<version>/`; the player zips put it in `app\`,
less the tests and the packaging files, plus the runtime, the starter and (plus) the bundled mods.

## The runtime (players need no Python)

`tools/packaging/build_runtime.py` builds `app\runtime`: python.org's Python for Windows (64-bit) in the shape of
python.org's "embeddable package", plus Tcl/Tk for the window, from the same python.org build.

- **Nothing is downloaded.** The files are copied from the build PC's install of the python.org installer (the
  Python that runs the script). Every `.exe`, `.dll` and `.pyd` must carry a valid signature of the Python Software
  Foundation (the two `vcruntime` DLLs: Microsoft), checked with Windows' `Get-AuthenticodeSignature`.
- **Kept apart from any other Python.** `python313._pth` lists only `python313.zip`, the runtime folder and
  `..\tools`; no `import site`, so no site-packages, no `PYTHONPATH`, no user packages, no registry.
- **The standard library** is compiled into `python313.zip` by the same Python: hash-based `.pyc` files (no file
  dates) whose file names are neutral (no path of the build PC). Left out: pip, site-packages, the tests, IDLE,
  turtledemo, venv, Tcl's time zone data, Tk's demos.
- **Checked by running it** once with an empty environment: `sys.path` stays inside `app\`, and tkinter (a hidden
  window), ssl, ctypes, sqlite3, asyncio and the rest import.
- **Recorded:** every file's SHA-256 goes into `tools/packaging/runtime.lock.json`. `make_release.py` ships the
  runtime only when it matches the lock file for file: nothing more, nothing less, nothing changed.
  `python tools/packaging/build_runtime.py --check` compares them.
- **Size:** 341 files, 26.6 MB unpacked; each player zip is about 15 MB (31 MB unpacked).
- **Licences:** Python's `LICENSE.txt` (PSF License v2, with the notices of OpenSSL, libffi, zlib, SQLite and the
  rest; Tcl's and Tk's licence text is in it too, around line 564) and Tk's `tcl\tk8.6\license.terms` go in. The
  READ ME files point at `app\runtime\LICENSE.txt`. A separate copy of Tcl's `license.terms` isn't needed;
  `python tools/packaging/build_runtime.py --tcl-licence <license.terms>` can still add one.

Why not PyInstaller: our tools start each other as `python tools/x.py`, which a frozen exe breaks; PyInstaller's
loader is shared with a lot of malware, so antivirus programs (Bitdefender, Malwarebytes) often flag what it makes;
the one-file kind also unpacks itself into `%TEMP%` at every start. The python.org files are signed by the Python
Software Foundation; only our small starter, `modhost.dll` and the mods' DLLs are unsigned.

## Start Breathalyzer.exe

`tools/packaging/starter/start.c`, about 150 lines of C, built by `tools/packaging/build_starter.py` with the zig
that ships with Savage Wind. It starts `app\runtime\pythonw.exe -I app\tools\launcher.py` (or `modman_gui.py` while
there is no `launcher.py`), passes on its own arguments, and ends. No network, no registry, no admin rights, no
files written, no C runtime: it uses only kernel32 and user32. Run from inside the zip (without unpacking), it says
"Unpack the whole zip first".

- Its resources (icon from `assets\breathalyzer.ico`, version info "Start Breathalyzer" by giruka, a manifest that
  asks for no admin rights) are written by `build_starter.py` itself: no resource compiler needed.
- It is built twice and both must be the same bytes. Its SHA-256 goes into `tools/packaging/starter.lock.json` with
  the hashes of `start.c`, the icon and `build_starter.py`. `make_release.py` refuses a starter that doesn't match,
  or one built from an older `start.c`. The starter's own version stays 1.0.0.0, so the same file (and hash) can
  serve many releases. `python tools/packaging/build_starter.py --check` builds it again and compares.
- It isn't signed. Windows SmartScreen says "Windows protected your PC" for a new unsigned program from the
  internet (More info, Run anyway) until it is known. Free code signing for open source (SignPath Foundation) can
  end that later. `READ ME FIRST.txt` says what to do.
- If an antivirus holds it back: `app\Start Breathalyzer (backup).cmd` does the same.

## Breathalyzer Plus: the bundled mods

`tools/packaging/bundled-mods.toml` lists our mods with the text players see. For each mod:

| Key | What |
|---|---|
| `id` | the mod's id; must start with `botw-mod-loader.` or `giruka.` (ours) |
| `title`, `description` | what the mod picker and the game's Mods page show: plain ASCII, the description at most 80 characters (the Mods page cuts there), without curly brackets, the bar, the backquote, ^, ~, % or # (the game font lacks them) |
| `default` | `on` or `off`. Changes nothing until you use it: on. Changes the game (gameplay, looks, a game file), costs fps or uses the network: off |
| `why`, `tags` | why that default; short notes for the mod's card |
| `ready` | `true`: in the plus zip. `false`: left out (`--with-experimental` adds the built ones, off, tagged Experimental) |
| `kind = "code"` | a DLL: `manifest` (its mod.toml) and `dll` (under `build/native`) |
| `kind = "recipe"` | built on the player's PC from their own game files when first turned on: `folder` (the recipe, shipped in `app\` anyway) and `build` (our tool commands, in order) |
| `after_add` | our tool commands to run once the mod is in the library |
| `egg` | `true`: an easter egg. Secret: the picker and Home never list it; one switch "Enable easter eggs?" (OFF at first) turns every `egg = true` mod on or off together. An egg always starts OFF |

Rules make_release checks: every path is a file git tracks or a DLL in `build\native`; never anything from `out\`
(stress-lib, showcase-lib), never another folder; the mod.toml's id matches; the DLL is ours (64-bit, exports
`swmod_init`, no game functions, not from the Savage Wind folder). Each bundled code mod gets its own folder with a
copy of its mod.toml whose `name` and `description` are the player text (the developer notes at its top are left
out), so the game's Mods page says the same as the picker.

Ready now: Mods page in the game (on), Show Position (on), Infinite Stamina (off), Teleport Up (off), Performance:
effects off (off, built from your game files). Not ready yet: Ghost multiplayer, the secret word easter egg, Castle
date, Performance: shorter shadows.

`app\bundled-mods.json` is what the launcher's mod picker reads:

```
{"format": 1, "edition": "plus", "version": "0.2.0-beta.1", "mods": [
  {"id": "botw-mod-loader.basics-show-pos", "title": "Show Position",
   "description": "Type where in the game's console (F12) to see where Link is.", "default": "on",
   "why": "...", "tags": [], "experimental": false, "kind": "code", "folder": "bundled-mods/basics-show-pos"},
  {"id": "botw-mod-loader.perf-effects", "kind": "recipe", "recipe": "native/examples/perf_effects",
   "build": [["quality.py", "toggle", "ssao", "off"], ...], ...}]}
```

Paths are inside `app\`. A code mod is added with `modman add app\<folder>`; a recipe runs `app\tools\<tool> <args>`
for each `build` command when it is first turned on; then `after_add`.

## Checks before writing

Every file must pass all of these. If one file fails, nothing is written, and each failing file is listed.

- All of `tools/check_clean.py`'s rules (the tool imports them): game file types, game file headers, keys,
  saves, generated game code, logs, screenshots, files over 10 MB. A path in `.cleanallow` skips only these rules.
- No leaks, in any file or file name (text or binary, UTF-8 or UTF-16, as a whole word):
  - your Windows user name and your home folder's name (very common ones such as `runner` are skipped as words);
  - your home folder's full path, in every spelling: `C:\Users\you`, `C:/Users/you`, `C:\\Users\\you`,
    `/c/Users/you` and the short 8.3 form (`C:\Users\ALEXAN~1`). This works for short or common names too;
  - the repo's own path;
  - your git e-mail and git name. The git name is fine when `LICENSE` holds it.
- No secrets: no private keys and no access tokens (GitHub, AWS, Google, Slack, Stripe, npm, PyPI, `sk-` API keys).
  No key files either: `.env`, `id_rsa`, `id_ed25519`, `.pem`, `.key`, `.pfx`, `.p12` and the like.
- Archives: zip and gzip files are opened, and every file in them gets these same checks (also the runtime's
  `python313.zip`). An archive inside one is refused. Other kinds (7z, rar, xz, tar, ...) are refused: what's inside
  can't be checked. A zip in `.cleanallow` skips check_clean's rules for its files too, but never the leak and secret
  checks.
- No links or junctions, also not in a folder above the file: a link could point at the game.
- No earlier release: a `breathalyzer-*.zip` or `*.SHA256SUMS.txt` is never packed again (nor one from before
  the rename, `botw-mod-loader-*`).
- No file over 20 MB.
- Player zips also: the runtime matches `runtime.lock.json` and keeps the rules above (`._pth` without
  `import site` or absolute paths, no site-packages or `.pth` files, 64-bit binaries only); the starter matches
  `starter.lock.json`, is a 64-bit window program that uses only kernel32 and user32 and exports nothing; the
  bundled mods follow their rules; no program or DLL but the allowed ones; the needed files are all there.

## Checks after writing

The tool opens the zip again and checks every entry:

- Names: no absolute paths, no `..`, no backslashes, all inside the one folder, nothing that wasn't planned,
  and no user name in a name.
- Content: the same SHA-256 as the file that was checked, `check_clean`'s rules again (on the files as they come
  out of the zip), the leak, secret and archive checks again, the starter's checks again, no file over 20 MB, and
  `SHA256SUMS.txt` matches the files.

If any check fails, the zip is deleted and the reasons are listed.

## The DLLs (--with-binaries, source zip)

`--with-binaries` adds the DLLs `tools/build_native.py build` made, if they exist:
`modhost.dll` and the example mods (`hello.dll`, `stamina_hook.dll`, `ghost.dll`).
DLLs that aren't built are left out, and the tool says which.
They keep their place in the zip, `build/native/...`, so the mod manager and `build_native.py install` find them.
(The player zips always hold `modhost.dll`, and the plus zip its bundled mods' DLLs, checked the same way.)

Each DLL must be:

- a 64-bit Windows DLL (the tool reads its export table);
- one of ours: the example mods export `swmod_init`; `modhost.dll` exports `swgame_tables` and holds modhost's
  own log text;
- inside `build\native`, with no link on the way, and not from the Savage Wind folder;
- not a hard link to a file in the Savage Wind folder (on Windows the tool lists the file's other names;
  elsewhere any hard link is refused);
- never `swgame.dll` (the game's code), and no exported game functions (`f_<address>`).

The self-test DLLs in `build\native\selftest` never go in.

## The SHA-256 lists

- Inside the zip: `<name>-<version>/SHA256SUMS.txt`, one line per file (UTF-8). The player zips' list starts with
  one `# ` line: the commit the files come from (and any `--overlay` files).
- Next to the zip: `<name>-<version>.SHA256SUMS.txt`, the same lines plus the zip's own hash.

To check a download in PowerShell:

```
Get-FileHash breathalyzer-0.2.0-beta.1.zip -Algorithm SHA256
```

## Same input, same zip

With the same Python version, the same files give a byte-identical zip: names are sorted, every date is
1980-01-01, every file mode is the same. A file's date on disk doesn't matter, only its content.
Another Python version can give other bytes: Python's zlib does the compressing, and Python 3.14 on Windows
uses zlib-ng. Make releases you want to compare with the same `python`.

## Before you upload

1. Commit, then build both zips from that commit with no `--overlay` and no `--working-tree`. The first line of each
   `SHA256SUMS.txt` must name the commit and must not say "not for upload".
2. Check each zip the way a new player starts it (one game at a time, a test save):
   - Give the zip a Zone.Identifier first (as a browser download has): `Set-Content <zip> -Stream Zone.Identifier
     -Value "[ZoneTransfer]`nZoneId=3"`. Unpack it with `Expand-Archive` into a path with a space and letters
     outside English, for example `out\fresh-install\Test Ünï\`. Then SmartScreen and the Mark of the Web are
     tested too.
   - Open `Start Breathalyzer.exe` with no arguments. Set `SWMOD_LIBRARY` to a scratch folder first, so the default
     library code still runs but your own library isn't touched, and `BREATHALYZER_HOME` to a scratch folder (both
     editions otherwise share `%LOCALAPPDATA%\Breathalyzer` and the library next to Savage Wind).
   - Play once with Developer commands OFF, as players get it. In the report call the core edition "Breathalyzer
     (no bundled mods)": it plays whatever mods the library holds, so "plain game" holds only with an empty library.
3. Scan both zips, and one unpacked folder after its first run, with Bitdefender and Malwarebytes. If one is
   flagged, report the false positive to that vendor before the upload.
4. Upload the same two zips and their `.SHA256SUMS.txt` everywhere.

## Where to publish

The same zips and hashes on every site. Never a game file; store pages may show gameplay.

| Site | Role | Notes |
|---|---|---|
| GitHub Releases | home: the code (MIT), the downloads and their hashes, bug reports | tag `v0.2.0-beta.1`, marked pre-release; both zips, both SHA-256 lists, short notes (needs Savage Wind 0.4.1-alpha.4 and your own 1.9.0 game files, which zip to take, the SmartScreen note) |
| itch.io | the player page: easy download, devlogs, comments | kind Tool, "No payments"; both zips tagged Windows, named "Breathalyzer" and "Breathalyzer Plus"; can start Restricted (testers with keys) |
| GameBanana | where BotW modders look | BotW > Tools; mark it beta; credit giruka, and Savage Wind Recomp as what it needs |

No servers of ours: nothing in the zips goes online by itself, and multiplayer stays player-hosted.
