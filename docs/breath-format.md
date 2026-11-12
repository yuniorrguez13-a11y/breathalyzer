# The .breath file

A `.breath` file is one mod in one file, made to be shared. Breathalyzer makes one (**Export as .breath** on Home)
and adds one (**Add a .breath** on Home, or a double-click on the file once Settings > .breath files is ON).
`tools/breath.py` reads and writes them; the launcher's parts are in [the launcher](launcher.md).

## For players

- **Export as .breath**: pick a mod in your list, press **Export as .breath**, choose where to save it (your Desktop
  at first). Breathalyzer says what will go in ("12 files, 3.4 MB: romfs, mod.toml.") and asks **Make it** or
  **Cancel** before it writes anything. Send the file to a friend.
- **Add a .breath**: press it on Home and pick the file, or double-click the file. Breathalyzer checks every file in
  it first (**Cancel** stops that and keeps nothing), then shows its title, who made it, its version and what it
  does (a few lines), and asks **Add this mod?** Press **Add this mod** or **Not now** (Esc). Not now keeps nothing.
  "Not damaged" means the file is as it was made; it doesn't mean the mod is safe: only add mods from people you
  trust.
- **A mod with code** (a DLL or another program) gets a red warning right under its title: "Careful: this mod has
  code". So does a package Breathalyzer couldn't look all through ("Careful: this package may have code"). Code runs
  on your PC like a program, with your rights. Only add it if you trust the person who made it. Not now is picked
  first for these. **Code patches and cheats** (`.ips`, `.pchtxt`, a cheat `.txt`) get a milder warning: "This mod
  changes the game's code".
- **While you play** nothing is checked, added or shown: a `.breath` you double-click then is asked about once the
  game is closed.
- **What is never in a .breath**: your game's files. A mod Breathalyzer Plus builds from your own game files can't be
  exported (Breathalyzer Plus builds it on each PC). A BCML (`.bnp`) or UKMM mod is exported as the package you added,
  not as Breathalyzer's copy, which has your game's files merged in.
- **Double-click**: Settings > .breath files > **Register .breath** makes a double-click on a `.breath` file open
  Breathalyzer. Only for your Windows user, no admin rights. **Unregister .breath** undoes it.

## What is inside

A `.breath` is a zip file. It holds:

| Path | What |
|---|---|
| `breath.json` | the manifest: what the mod is and every file in it |
| `mod/...` | a mod's own files, as Breathalyzer adds a mod folder (kind `mod`) |
| `package/<one file or folder>` | the original package (kind `package`): a `.bnp`, a UKMM package, a Wii U mod, or the mod's own `.zip`, `.7z` or `.rar` download |

Nothing else: no other top-level files or folders, no empty names, no links.

### breath.json

UTF-8 JSON, at most 1 MB:

```json
{
 "format": "breathalyzer-mod",
 "format_version": 1,
 "id": "giruka.cool-mod",
 "title": "Cool Mod",
 "version": "1.2",
 "author": "giruka",
 "description": "What the mod does, in a few plain lines.",
 "kind": "mod",
 "package": "",
 "has_code": true,
 "code": ["mod/hooks.dll"],
 "files": [
  {"path": "mod/hooks.dll", "size": 18432, "sha256": "<64 lowercase hex digits>"},
  {"path": "mod/mod.toml", "size": 212, "sha256": "<64 lowercase hex digits>"}
 ],
 "made_with": "Breathalyzer"
}
```

| Key | Rule |
|---|---|
| `format` | always `breathalyzer-mod` |
| `format_version` | a whole number, 1 today. A newer one is refused with "made by a newer Breathalyzer" |
| `id` | the mod's id: lowercase letters, digits, `.`, `_` and `-`, at most 64, starting with a letter or digit, no `..` |
| `title` | the name shown (required); `version`, `author`, `description`, `made_with`: optional text |
| `kind` | `mod` (files under `mod/`) or `package` (one package under `package/`) |
| `package` | for kind `package`: `bnp`, `ukmm`, `wiiu` or `download`; empty for kind `mod` |
| `has_code` | `true` when any file is code (below), else `false` |
| `code` | every code file's path, sorted; empty when `has_code` is false |
| `files` | every file in the zip but `breath.json`: its path, its size in bytes and its SHA-256 |

Text is shown as plain text: control characters and hidden marks (those that turn text around, zero-width ones, soft
hyphens, the invisible tag letters U+E0000 to U+E007F) are replaced by spaces, and it is cut to 80 characters
(title), 40 (version), 120 (author), 80 (made_with) and 2000 (description; line breaks kept, at most one empty line
in a row). "Add this mod?" shows the description in 4 lines at most (cut with "..."), and file names cleaned the same
way and cut to 60 characters.

Code means a file ending in `.dll`, `.exe`, `.com`, `.scr`, `.cpl`, `.sys`, `.drv`, `.ocx`, `.msi`, `.msp`, `.bat`,
`.cmd`, `.ps1`, `.psm1`, `.vbs`, `.vbe`, `.js`, `.jse`, `.wsf`, `.wsh`, `.hta`, `.lnk`, `.py`, `.pyw`, `.pyd`,
`.jar`, `.so`, `.dylib` or `.reg`.

Code patches and cheats (`.ips`, `.pchtxt`, and `<16 hex digits>.txt` cheat files) aren't programs, so they are not
in `has_code` or `code`; Breathalyzer finds them by name itself and shows the milder warning.

For a kind `package`, Breathalyzer also looks inside the package, as `modman add` would take it in: every name in a
`.zip` (and in the `.zip` files inside it, 3 levels deep, as `modman add` opens a download's archives), and in a
`.7z`, `.rar` or `.bnp` with 7-Zip's or Windows' tar listing (`bnp.list_archive`: nothing is unpacked). Code and code
patches it finds there are named in the warnings. What it can't look into (a `.7z` or `.rar` inside, a nested zip
over 512 MB, a package no tool here can list) gives "this package may have code". As a backstop, after **Add this
mod** the mod's copy in your mods folder is looked through: a DLL or program the warning didn't name means the mod
is taken out again at once ("... had code it didn't show").

## The checks when one is added

A `.breath` comes from someone else, so Breathalyzer checks all of it before it writes anything, with the same rules
as for `.bnp` packages (`tools/bnp.py`). Any failure refuses the whole file, says why in plain words, and keeps
nothing.

1. **The zip**: a real zip, no bigger than the whole game (20 GB). Before Python's `zipfile` reads its list of files
   (it reads all of it into memory), the end records alone (the last 64 KB and 22 bytes, and the Zip64 record when
   there is one) must say at most 200,001 files and a list of at most 64 MB. Then: each name safe (`bnp.check_name`:
   nothing outside the folder, no drive letters or `..`, no names Windows can't use; also no name starting with `-`,
   no hidden marks that turn text around, no `COM1`-`COM3`/`LPT1`-`LPT3` written with raised digits); no two names
   Windows sees as one (letter case in any alphabet: Greek σ and ς too); no links; no password; only stored or
   deflated files; no file over 4 GB; no file claiming more than deflate can pack (about 1,100 to 1); no two files
   sharing the same bytes (the trick behind the worst zip bombs); 20 GB unpacked at most.
2. **The manifest**: every key as in the table above.
3. **The list**: `files` names exactly the files that are there (none more, none less), each with its size; every
   file is under `mod/` for kind `mod`, or under `package/` for kind `package`.
4. **The code**: `has_code` and `code` match the files. A file list that hides a DLL is refused ("says it has no code,
   but it has ...").
5. **No packed files inside**: a kind `mod` holds no `.zip`, `.7z`, `.rar`, `.bnp`, `.breath` or other packed file;
   a kind `package` holds exactly one package (one `.bnp`, `.zip`, `.7z` or `.rar` file, or one folder without
   packed files in it).
6. **The package fits its type**: its extension and its first bytes must fit what `package` says: `bnp` a `.bnp`
   (7-Zip or zip), `.7z` or `.zip`; `ukmm` a `.zip`; `wiiu` a `.zip`, `.7z`, `.rar` or `.bnp`; `download` a `.zip`,
   `.7z` or `.rar` (never a folder); a `.zip` must start like a zip, a `.7z` like 7-Zip, a `.rar` like RAR. A `bnp`
   package must hold BCML's `info.json`, a `ukmm` one UKMM's `meta.yml` and `manifest.yml` (checked once it is
   listed).
7. **Room**: what it unpacks to fits, with 2 GB (or 1% of it, when more) left free on the drive.
8. **While unpacking**: every file's SHA-256 and size must match its line in `files`. On any mismatch ("was changed
   after it was made, or is damaged") everything unpacked so far is removed. Names Windows can't write after all (a
   file and a folder with one name, a path too long) are refused in plain words too, and **Cancel** stops between
   two 1 MB blocks. Nothing is kept either way.

Then the player is asked. **Add this mod** runs `modman add` with `mod/` (or the package) under the mod's title
(`--name=<title>` in one piece, so a title starting with `-` is no option), and modman's own checks run as for any
mod. Nothing in a `.breath` is ever run by Breathalyzer. While the game is open nothing of this happens: a `.breath`
waits until the game is closed.

The unpacked copy waits in `%LOCALAPPDATA%\Breathalyzer\received\`: a kind `mod` copy is removed once the mod is in
the list, or at Not now; a kind `package` copy stays while a mod in the list comes from it (it is the original
package the mod list names, for the next export), and is removed the next time a `.breath` is opened after that mod
is gone. A copy still waiting after a day (the window was closed) is removed then too.

## How Breathalyzer makes one

`tools/breath.py export <mod> <file.breath>`, or **Export as .breath**:

- **A plain mod**: the files of its copy in your mods folder (without its dot folders), and nothing else: each file
  is taken from the folder the mod was added from while it is still there (the same path, below the `romfs` or
  title id folder names `modman add` puts above it), else from the copy. Files put in that folder since, or anything
  else in it, never go in. A mod added from a `.zip`, `.7z` or `.rar` download is exported as that download (kind
  `package`, `download`). A whole `Pack/*.pack` that only the copy in your mods folder still has is never exported:
  Breathalyzer may have filled it in from your game.
- **Asked first**: **Export as .breath** shows the number of files, the size and the names at the top ("12 files,
  3.4 MB: romfs, mod.toml.") and writes only after **Make it** (`breath.plan_export`, then `breath.write_export`; the
  command line prints the same line). A folder Windows won't save in gives "Can't save ...: pick another folder, like
  your Desktop".
- **A BCML, UKMM or Wii U mod**: the package you added, as it was (kind `package`). The title, author and description
  come from what the package said when it was added.
- **Never exported**: mods built from your game files (Breathalyzer Plus' recipes, the castle, any mod with
  `built.txt`), and a `.breath` saved inside your mods or Savage Wind folder.
- Packed files inside a plain mod are left out (the window says which). Files are deflated, packages stored as they
  are. The file is written next to its final name and renamed at the end, so a half-written `.breath` never exists.

## Command line

```
python tools/breath.py info <file.breath>                   what it holds, after every check (nothing written)
python tools/breath.py unpack <file.breath> <new folder>    checked, unpacked, every file's SHA-256 verified
python tools/breath.py export <mod id> <file.breath> [--library <folder>]
```

Exit code 0: done. 1: refused (the message says why). 2: no mod library, or a modman error.

## The double-click (Windows)

**Register .breath** writes, for this Windows user only (`HKEY_CURRENT_USER\Software\Classes`, no admin rights):

| Key | Value |
|---|---|
| `.breath` | `Breathalyzer.Mod`, and `OpenWithProgids\Breathalyzer.Mod` |
| `Breathalyzer.Mod` | `Breathalyzer mod` |
| `Breathalyzer.Mod\DefaultIcon` | `"<Start Breathalyzer.exe>",0` |
| `Breathalyzer.Mod\shell\open\command` | `"<Start Breathalyzer.exe>" "%1"` (from the repo: `pythonw.exe -I tools\launcher.py "%1"`) |

**Unregister .breath** removes `Breathalyzer.Mod`, and `.breath` too while it still names Breathalyzer (a `.breath`
another program took over since is left to it). Settings says whether a double-click opens this Breathalyzer, another
Breathalyzer folder (an older one: press Register .breath again), or nothing. When you once picked another program
for `.breath` files in Windows ("Always use this app"), that choice wins, and Settings says "Another program opens
.breath files: right-click one, Open with, Breathalyzer, Always". Breathalyzer only reads that choice, at
`HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.breath\UserChoice` (`ProgId`).
Tests write only under a made-up `HKEY_CURRENT_USER\Software\BreathalyzerTest-<random>` key and delete it.

A double-click starts `Start Breathalyzer.exe` with the file. When the launcher window is already open, the new
start leaves the file's path in `%LOCALAPPDATA%\Breathalyzer\open\` and ends; the open window picks it up within half
a second and asks. A request older than two minutes (the window had closed) is dropped. The new start brings the open
window to the front only when the game is closed, and only the window `window.json` in the same folder names (that
process's own Tk window, never another window called Breathalyzer). While you play, the file waits and is asked
about once the game is closed.
