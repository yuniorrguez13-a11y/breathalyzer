# The .breath file

A `.breath` file is one mod in one file, made to be shared. Breathalyzer makes one (**Export as .breath** on Home)
and adds one (**Add a .breath** on Home, or a double-click on the file once Settings > .breath files is ON).
`tools/breath.py` reads and writes them; the launcher's parts are in [the launcher](launcher.md).

## For players

- **Export as .breath**: pick a mod in your list, press **Export as .breath**, choose where to save it (your Desktop
  at first). Send the file to a friend.
- **Add a .breath**: press it on Home and pick the file, or double-click the file. Breathalyzer checks every file in
  it first, then shows its title, who made it, its version and what it does, and asks **Add this mod?** Press
  **Add this mod** or **Not now** (Esc). Not now keeps nothing.
- **A mod with code** (a DLL or another program) gets a red warning: "Careful: this mod has code". Code runs on your
  PC like a program, with your rights. Only add it if you trust the person who made it. Not now is picked first for
  these.
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

Text is shown as plain text: control characters and the hidden marks that turn text around are replaced by spaces,
and it is cut to 80 characters (title), 40 (version), 120 (author), 80 (made_with) and 2000 (description; line breaks
kept, at most one empty line in a row).

Code means a file ending in `.dll`, `.exe`, `.com`, `.scr`, `.cpl`, `.sys`, `.drv`, `.ocx`, `.msi`, `.msp`, `.bat`,
`.cmd`, `.ps1`, `.psm1`, `.vbs`, `.vbe`, `.js`, `.jse`, `.wsf`, `.wsh`, `.hta`, `.lnk`, `.py`, `.pyw`, `.pyd`,
`.jar`, `.so`, `.dylib` or `.reg`. A `.zip` package's code files are named in the warning too.

## The checks when one is added

A `.breath` comes from someone else, so Breathalyzer checks all of it before it writes anything, with the same rules
as for `.bnp` packages (`tools/bnp.py`). Any failure refuses the whole file, says why in plain words, and keeps
nothing.

1. **The zip**: a real zip, no bigger than the whole game (20 GB); at most 200,000 files; each name safe
   (`bnp.check_name`: nothing outside the folder, no drive letters or `..`, no names Windows can't use; also no name
   starting with `-`); no two names that differ only in letter case; no links; no password; only stored or deflated
   files; no file over 4 GB; no file claiming more than deflate can pack (about 1,100 to 1); no two files sharing the
   same bytes (the trick behind the worst zip bombs); 20 GB unpacked at most.
2. **The manifest**: every key as in the table above.
3. **The list**: `files` names exactly the files that are there (none more, none less), each with its size; every
   file is under `mod/` for kind `mod`, or under `package/` for kind `package`.
4. **The code**: `has_code` and `code` match the files. A file list that hides a DLL is refused ("says it has no code,
   but it has ...").
5. **No packed files inside**: a kind `mod` holds no `.zip`, `.7z`, `.rar`, `.bnp`, `.breath` or other packed file;
   a kind `package` holds exactly one package (one `.bnp`, `.zip`, `.7z` or `.rar` file, or one folder without
   packed files in it).
6. **Room**: what it unpacks to fits, with 2 GB (or 1% of it, when more) left free on the drive.
7. **While unpacking**: every file's SHA-256 and size must match its line in `files`. On any mismatch ("was changed
   after it was made, or is damaged") everything unpacked so far is removed.

Then the player is asked. **Add this mod** runs `modman add` with `mod/` (or the package) under the mod's title, and
modman's own checks run as for any mod. Nothing in a `.breath` is ever run by Breathalyzer.

The unpacked copy waits in `%LOCALAPPDATA%\Breathalyzer\received\`: a kind `mod` copy is removed once the mod is in
the list, or at Not now; a kind `package` copy stays (it is the original package the mod list names, for the next
export). A copy still waiting after a day (the window was closed) is removed the next time a `.breath` is opened.

## How Breathalyzer makes one

`tools/breath.py export <mod> <file.breath>`, or **Export as .breath**:

- **A plain mod**: the files it was added from, while they are still where they were (a folder), else the copy in
  your mods folder (without its dot folders). A mod added from a `.zip`, `.7z` or `.rar` download is exported as that
  download (kind `package`, `download`). A copy in your mods folder that has a whole `Pack/*.pack` is never exported
  when the original is gone: Breathalyzer may have filled it in from your game.
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
Breathalyzer folder (an older one: press Register .breath again), or nothing. Tests write only under a made-up
`HKEY_CURRENT_USER\Software\BreathalyzerTest-<random>` key and delete it.

A double-click starts `Start Breathalyzer.exe` with the file. When the launcher window is already open, the new
start leaves the file's path in `%LOCALAPPDATA%\Breathalyzer\open\` and ends; the open window picks it up within half
a second and asks. A request older than two minutes (the window had closed) is dropped.
