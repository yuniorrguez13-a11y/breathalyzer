# Breathalyzer, the launcher

Install Breathalyzer, show it your Savage Wind folder once, then start the game from it. No Python to install, no
folders to dig through.

## Which download

| Download | Take it when |
|---|---|
| **Breathalyzer** (`breathalyzer-<version>.zip`) | you want Savage Wind with no mods, or only mods you add yourself |
| **Breathalyzer Plus** (`breathalyzer-plus-<version>.zip`) | you also want our own mods: you pick which ones are on before you play |

Both have the same launcher and mod manager. Neither has game files or mods made by other people.

You need Savage Wind 0.4.1-alpha.4, with its game files set up from your own copy of the game (version 1.9.0).

## Install

1. Unpack the whole zip: right-click it, choose **Extract All**.
   A good place is next to your Savage Wind folder, for example `Documents\SavageWind\Breathalyzer`.
   Not in Program Files: Breathalyzer must be able to write there.
2. Open **Start Breathalyzer** in the new folder.
3. The first time, show it your Savage Wind folder (the folder with `savagewind.exe` in it). Breathalyzer looks
   in the usual places first, so often you only press OK.
4. Breathalyzer Plus only: pick your mods. Each has a title, a short description and ON or OFF. Mods that change
   the game start OFF. Some are built from your own game files the first time you turn them on (about a minute).
5. Press **Play**.

After that, open Start Breathalyzer and press Play. The first Play after you change code mods can take 10 to 20
minutes (Breathalyzer rebuilds the game code once); the window says so.

## The window

Big text (20 pt to start; **Ctrl +** and **Ctrl -** change it, up to what fits on your screen), dark text on white or
light text on black, and everything works with the keyboard: **Tab** and the arrow keys move, **Space** or **Enter**
presses, **Esc** goes back, **F1** shows the keys, **F5** reads everything again. The latest result is always at the
top, in a line or two of plain words.

### The first start

Breathalyzer looks for Savage Wind in fixed places only (it never searches your disks): the folder it found last
time, a mod list you made earlier with the command line, the folders next to Breathalyzer (two levels down),
Documents, Desktop and Downloads (`Savage Wind`, `SavageWind\Savage Wind`), `Savage Wind` and `Games\Savage Wind` on
each drive, and a Savage Wind that is running. Found once: **Found Savage Wind** with the checks below and
**Continue**. Found in several places: pick one. Not found: **Choose your Savage Wind folder**. A pick one level too
high or too low (`SavageWind`, `extracted`, or `savagewind.exe` itself) is corrected.

It checks, and says what to do when one fails (one plain screen each, the technical text under **Details**):

| Check | When it fails |
|---|---|
| `savagewind.exe` is in the folder | That's not the Savage Wind folder (or: Savage Wind isn't in ... any more, **Find it again**) |
| Savage Wind is 0.4.1-alpha.4 (read from the exe) | This Savage Wind is version ...: **Open Savage Wind's download page**. An exe with the right version but not the official one is allowed ("a custom build") |
| The game files are set up | Set up the game files first: **Open Savage Wind**, press Set up game files, then **Check again** |
| The game files are BotW 1.9.0 | Your game files are not 1.9.0 |
| The game files weren't moved (`SAVAGEWIND_DATA_ROOT`) | Breathalyzer can't use a moved data folder yet |
| Breathalyzer can write next to Savage Wind and in its own folder | Move it out of Program Files |
| Not started from inside the zip | Unpack Breathalyzer first |
| 2 GB free for code mods | A warning only: **Go on anyway** |
| Your mod list can be read | **Start a fresh list** (the old one is kept as a backup; your mods stay in the mods folder) |

Then it uses your mod list next to Savage Wind (`modloader`), or makes one. Breathalyzer Plus then shows the mod
picker: each of our mods with **ON** or **OFF**, its title, what it does, notes like "Changes how the game plays"
and why it starts on or off; **Save**
adds them to your list (a mod built from your game files is built when you turn it on). Settings > Our mods >
**Pick again** shows it again.

### Home

- **Play** does everything: it waits if Savage Wind is already open; uses the changes you made on the game's Mods page;
  gets your mods ready when they changed since the last time ("Getting your mods ready... 2:31"; **Cancel** stops it);
  then starts the game. If your code mods can't be built, **Play without code mods** starts the game with the others.
- **Play without mods** starts Savage Wind as it is. Your mods stay as they are for next time.
- **Your mods**: ON or OFF, the mod's name and what it does; the whole text of the selected mod is under the list.
  **Space** (or a click on ON/OFF) turns a mod on or off. The next Play uses the change.
- **Add mod**, **Open mods folder**, **Settings**, and **More...**: the Mod Manager window (order, remove, check,
  details) for the same mods.
- After something failed, **Details** shows what happened, with **Copy** for a bug report (your home folder is
  written as `%USERPROFILE%`, so the report doesn't carry your name).

### Settings

Your Savage Wind folder (**Change...**, **Check**, **Open**), the frame rate the game uses (only read from
`savagewind.ini`; change it in the game: F1, Video, or with **Game settings**), your mod list (**Open**), text size,
dark, hide this window while you play, **Developer commands**, a desktop shortcut (**Make one**), and in
Breathalyzer Plus the mod picker again.

- **Developer commands** (OFF at first): some commands you type in the game's command box (F12) only work in the
  game's developer mode, like `up` (Teleport Up) and `infstamina off` (Infinite Stamina). While a mod that needs
  them is ON, Play turns developer mode on by itself, and Settings says so ("OFF, but ON while Teleport Up is on").
  **Turn on** keeps it on for every Play. Developer mode opens the game's control port 7777 on this PC only.
- **Make one** puts a shortcut to `Start Breathalyzer.exe` on your desktop. After you unpack a new Breathalyzer,
  make it again from the new one.

Tk windows don't work with screen readers yet.

## If Windows or your antivirus stops it

- **"Windows protected your PC"**: press **More info**, then **Run anyway**. Start Breathalyzer is a small new
  program that isn't signed yet, so Windows doesn't know it.
- **Your antivirus holds Start Breathalyzer back**: open `app\Start Breathalyzer (backup).cmd` instead. It does the
  same thing.
- **"Breathalyzer can't find its files"**: you opened it from inside the zip, or some files are missing. Unpack the
  whole zip again.
- **"Breathalyzer couldn't start"**: a file it needs couldn't be loaded (an antivirus may have taken one away).
  Unpack the zip again, or allow Breathalyzer in your antivirus. What went wrong is in
  `%LOCALAPPDATA%\Breathalyzer\launcher-error.txt` (attach it to a bug report).

## What's in the folder

| Path | What |
|---|---|
| `Start Breathalyzer.exe` | opens Breathalyzer. It only starts `app\runtime\pythonw.exe` with `app\tools\launcher.py` and closes |
| `READ ME FIRST.txt` | the steps above |
| `app\runtime\` | Breathalyzer's own Python and Tk (from python.org). It is used only by Breathalyzer and never touches another Python on your PC |
| `app\tools\` | the launcher, the mod manager and the other tools |
| `app\docs\` | these help pages |
| `app\build\native\modhost.dll` | the mod loader that Savage Wind loads (not a mod) |
| `app\bundled-mods\`, `app\bundled-mods.json` | Breathalyzer Plus only: our mods and the list the mod picker shows |

Your own things are not in this folder:

- your mods and mod list: the `modloader` folder next to your Savage Wind folder;
- the launcher's choices (your Savage Wind folder, your mod list, the window's text size): `%LOCALAPPDATA%\Breathalyzer`.

## Update or remove

- **Update**: unpack the new zip and use it; delete the old folder when the new one works. Your mods and choices
  stay, because they aren't in Breathalyzer's folder.
- **Remove**: delete the Breathalyzer folder. To remove your mods too, delete the `modloader` folder next to
  Savage Wind (Savage Wind itself is never changed). `%LOCALAPPDATA%\Breathalyzer` holds only the launcher's
  choices.

## Privacy

Breathalyzer never goes online by itself: no update check, no statistics, nothing sent anywhere. Multiplayer, when
you use it, connects only to the friend you choose.

## For developers

- `Start Breathalyzer.exe` runs `app\runtime\pythonw.exe -I app\tools\launcher.py` (or `app\tools\modman_gui.py`
  when there is no `launcher.py`) with `app\` as the working folder, and passes on its own arguments.
- `app\runtime\python313._pth` puts `python313.zip`, the runtime folder and `app\tools` on `sys.path`, and nothing
  else (no site-packages). `sys.executable` is the runtime's `pythonw.exe`; its `python.exe` is next to it for
  commands that need a console.
- `app\` is the repo folder the tools expect (`REPO` in `modman.py`): `build\native`, `native\examples` and
  `docs` are where they are in the repo.
- The mod picker reads `app\bundled-mods.json`: its format, and how the zips, the runtime and the starter are built
  and checked, are in [release](release.md).
- When the window can't start (an import error, Tk can't open, tkinter missing), `launcher.py` writes the traceback
  to `launcher-error.txt` in the settings folder and shows a Windows message box (ctypes, no Tk). It drops
  `TCL_LIBRARY` and `TK_LIBRARY` before Tk loads (another program's would break it).
- Developer mode: Play passes `--port 7777` to `modman run` when Settings > Developer commands is ON, while a mod
  that needs it is ON (`developer = true` in `bundled-mods.toml`, or `DEV_MODS` in `modman_gui.py`), or with
  `--port`; else `modman run` opens the port only for cheats.
- From the repo: `python tools/launcher.py [--library <folder>] [--size N] [--dark] [--port N]`. The launcher's
  choices go to `%LOCALAPPDATA%\Breathalyzer\launcher.json`, or to the folder `BREATHALYZER_HOME` names (use one
  for tests). `--search-in <folder>` looks for Savage Wind only there and one level below. One window per
  settings folder: a second start brings the open one to the front (a named mutex).
- `tools/launcher.py` has the parts without a window: finding and checking Savage Wind, the first-run steps
  (`FirstRun`), the settings file, the mod texts (mod.toml's `description`, else what the mod holds), the running
  game check (the process list, no `tasklist`), shortcuts (Windows' own shell link object, no PowerShell) and
  `read_bundled`. The window is `LauncherView` in `tools/modman_gui.py`; Play's steps are `LaunchController`
  (`modmenu.py apply` when the game's Mods page has changes, else `modman deploy` when the mods changed, then
  `modman run`). Tests: `tests/test_launcher.py`.
