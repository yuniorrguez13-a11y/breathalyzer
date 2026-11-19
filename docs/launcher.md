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
| Savage Wind is 0.4.1-alpha.4 (read from the exe) | This Savage Wind is version ...: **Get Savage Wind 0.4.1-alpha.4** (opens that release's page; newer ones, like 0.5.x, are refused). An exe with the right version but not the official one is allowed ("a custom build") |
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
**Pick again** shows it again. Easter eggs are secret: never listed one by one, only one switch, **Enable easter
eggs?** ("Secret extras. Some take longer the first time you press Play."), OFF at first. On Home they are one
**Easter eggs** row that turns all of them on or off. An egg left OFF isn't put in your list at all, and a secret
mod (`[mod] secret`) is never listed on Home, in More... or on the game's Mods page. A mod that comes with a bonus
(the multiplayer mod's) builds it the first time you turn the mod on: at Save, or at the next Play ("A part of a mod
you turned on is built from your game files first").

### Home

- **Play** does everything: it waits if Savage Wind is already open; uses the changes you made on the game's Mods page;
  gets your mods ready when they changed since the last time ("Getting your mods ready... 2:31"; **Cancel** stops it);
  then starts the game. If your code mods can't be built, **Play without code mods** starts the game with the others.
- **Play without mods** starts Savage Wind as it is. Your mods stay as they are for next time. If you ever turned
  on Ghost multiplayer, don't load a save that holds its surprise item without mods: the game has no data for it
  then, and that save can break. Use a save from before, or play with mods.
- **Your mods**: ON or OFF, the mod's name and what it does; the whole text of the selected mod is under the list.
  **Space** (or a click on ON/OFF) turns a mod on or off. The next Play uses the change.
- **Export as .breath**: the selected mod in one `.breath` file, to send to a friend (saved on your Desktop at
  first). Before anything is written it says what goes in ("12 files, 3.4 MB: romfs, mod.toml.") and asks **Make
  it** or **Cancel**. A mod built from your own game files can't be exported.
- **Add mod**, **Add a .breath** (or a double-click on a `.breath` file), **Open mods folder**, **Settings**, and
  **More...**: the Mod Manager window (order, remove, check, details) for the same mods.
- **Add this mod?**: a `.breath` file is checked first (every file in it must be as it was made; **Cancel** stops
  that and keeps nothing), then its title, who made it, its version and what it does (a few lines) are shown, with
  **Add this mod** and **Not now** (Esc). A mod with code (a DLL), or a package Breathalyzer couldn't look all
  through, gets a red warning right under its title, "Careful: this mod has code" ("... may have code"), and Not now
  is picked first. Code patches and cheats get a milder one, "This mod changes the game's code". "Not damaged" only
  means the file is as it was made, not that it is safe. Not now keeps nothing. A `.breath` that fails a check is
  refused in plain words ("... wasn't added"). [The .breath file](breath-format.md) has the details.
- **While you play** (the game is open), Add mod, Add a .breath and Export as .breath wait, and nothing brings the
  window up: a `.breath` you double-click then is asked about once the game is closed.
- After something failed, **Details** shows what happened, with **Copy** for a bug report (your home folder is
  written as `%USERPROFILE%`, so the report doesn't carry your name).
- **Multiplayer** (Breathalyzer Plus with Ghost multiplayer): the page below.

### Multiplayer

Play with friends on your home network, without a command line (Experimental; the steps for players are also in
[multiplayer.md](multiplayer.md)). The page has big rows and buttons:

- **Ghost multiplayer** ON/OFF (**Turn on** adds it as the mod picker does, and builds its surprise the first time).
- **Your name**: what your friends see (**Change**: 1 to 20 letters, digits or spaces). Never your Windows name:
  the first Host or Join asks for one (everyone on your home network sees it; your world is called by it).
- **Who am I**: the character you picked (**Change** opens the Who am I page).
- **Story together** ON/OFF (for the host): ON, your friends' co-op saves get your towers, quests and freed Divine
  Beasts; each friend is asked first ("Share story progress with Ana?") and nothing is ever taken away.
- **Room code**: **Make one** gives 3 words your friends type to join; OFF, anyone on your home network can join.
- **Host**: opens your world on this PC, then starts the game through Play (so your mods, the multiplayer mod too,
  are ready; the page shows Play's progress). The first Host on a PC waits at **Your world is open**: Windows asks
  whether Python may use the network (press Allow; the Wi-Fi must be Private), then **Start the game**. Until a
  friend has come in, the page says where to allow Python (Windows Security > Firewall & network protection > Allow
  an app). Press Continue in the game. **Join**: the worlds on your home network show up by themselves; pick one
  and Breathalyzer starts the game. **Stop** ends it: while the mods are still being made ready the game is cancelled
  too; an open game keeps running on your multiplayer copy. Closing the game and closing Breathalyzer (it asks
  first) end it too. **Details**: what the multiplayer tools said, your home folder as `%USERPROFILE%`.
- Host and Join wait while the game is open (Breathalyzer starts it for you), and while Infinite Stamina is on.
- Multiplayer plays on a **copy** of your save, made once in `modloader\multiplayer` (the host's world, a friend's
  copy per host, story backups): your own save is only read. Play plays your own save, as always.
- **Who am I** shows the characters in big buttons by group (Hylians, Races, Monsters), each with Ready, Not tried
  yet (built, not yet seen in the game), Build first, Stand-in (Link: Breathalyzer doesn't build the Link body yet)
  or Needs a mod (Linkle needs the Linkle mod). Linkle is your own body: picking her turns The Linkle Mod on (anyone
  else turns it off again, only when Who am I turned it on), with one restart: the next Play, Host or Join, or
  "Restart the game now?" while it is open (default Later; it says when friends get disconnected; yes: the game
  closes itself and starts again). Friends still see her as her costume (each PC builds it from The Linkle Mod's
  files, on or off). A press picks it: it is used from your next Host or Join, and while
  your game is open it is sent at once (the page says "In your game now." once the game took it). **Each PC shows
  the others from its own built characters**: **Build the characters** (once, a few minutes, from your own game
  files; the line under the status shows how long it has run) lets you see your friends as who they picked, and they
  see your pick if they built them too. They are one mod in your list, "Characters for multiplayer", used only while
  Ghost multiplayer is on. A build that fails says why, with **Details**.

### Settings

Your Savage Wind folder (**Change...**, **Check**, **Open**), the **Frame rate**, your mod list (**Open**), text
size, dark, hide this window while you play, **Developer commands**, a desktop shortcut (**Make one**), **.breath
files**, and in Breathalyzer Plus the mod picker again.

- **Frame rate**: a big dropdown with Savage Wind's own choices: 30, 60, 90 or 120 fps, or No limit. It shows what
  `savagewind.ini` says now (no `frame_rate` line: 60, the game's default). A pick writes only the `frame_rate` line
  of `savagewind.ini` (No limit is `off`, as the game writes it); every other byte of the file stays as it was. The
  first change keeps the file as it was in `savagewind.ini.breathalyzer-backup`, once. It says "Used from your next
  start". While Savage Wind is open the dropdown is grey ("Close the game to change it."): the game writes the file
  itself when it closes. Without `savagewind.ini` it says "Start Savage Wind once first." A file saved in another
  format (UTF-16, "Unicode" in Notepad) is left alone: "use F1, Video in the game". The mouse wheel over the
  dropdown does nothing: a frame rate is written only when you pick one from its open list. You can still change it
  in the game too (F1, Video), or with **Game settings**.
- **.breath files**: **Register .breath** makes a double-click on a `.breath` file open this Breathalyzer, which
  asks before it adds the mod. Only for your Windows user (no admin rights). **Unregister .breath** undoes it. The
  line says ON, OFF, "ON, for another Breathalyzer folder" (after you unpacked a new one: press Register .breath
  again), or "Another program opens .breath files: right-click one, Open with, Breathalyzer, Always" (you once picked
  another program for them in Windows, which wins).

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

- your mods and mod list: the `modloader` folder next to your Savage Wind folder (with `multiplayer\`: the co-op
  save copies, their story backups, your multiplayer key and bans, the story list);
- the launcher's choices (your Savage Wind folder, your mod list, the window's text size, your multiplayer name and
  choices; `charsel.json`: who you are in multiplayer): `%LOCALAPPDATA%\Breathalyzer`,
  with `received\` (`.breath` files being added, and the packages added from them: one goes when no mod in your list
  comes from it any more), `open\` (a double-clicked `.breath` on its way to the open window) and `window.json`
  (while the window is open: which window it is, and whether you play).

## Update or remove

- **Update**: unpack the new zip and use it; delete the old folder when the new one works. Your mods and choices
  stay, because they aren't in Breathalyzer's folder.
- **Remove**: press Settings > **Unregister .breath** if you registered it, then delete the Breathalyzer folder. To
  remove your mods too, delete the `modloader` folder next to Savage Wind (if you ever turned on Ghost
  multiplayer: a save that holds its surprise item can break without it, so keep a save from before). In the Savage
  Wind folder Breathalyzer
  changes only the `frame_rate` line of `savagewind.ini`, and only when you pick a frame rate in Settings
  (`savagewind.ini.breathalyzer-backup` is the file as it was). `%LOCALAPPDATA%\Breathalyzer` holds only the
  launcher's choices and the `.breath` files being added.

## Privacy

Breathalyzer never goes online by itself: no update check, no statistics, nothing sent anywhere. Multiplayer, when
you use it, stays on your home network: Join asks your home network for worlds, Host answers those questions and
takes friends from your home network only, and only what the game needs goes to them (your name, who you picked,
where Link is and what he does; from the host, the time of day and the story).

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
- The Multiplayer page: `tools/mp_page.py` (`MpSession` without widgets, `MpPage` in `LauncherView`). Its tools run
  with `console_python()` (the runtime's `python.exe`, `CREATE_NO_WINDOW`), `--library` last, and end through their
  stdin (`coop_agent.py --stdin`, `client.py --stdin`); the game goes through `LaunchController.launch(env=...)`,
  which gives only that game `SAVAGEWIND_SAVE_DIR` (`Backend.game_env_extra`); Stop before the game opened uses
  `LaunchController.cancel()`. Its choices are launcher.json's `"multiplayer"` (name, story, code, firewall: the
  first Host's wait was seen). Tests: `tests/test_mp_page.py` (stand-in tools, a hidden window).
- Developer mode: Play passes `--port 7777` to `modman run` when Settings > Developer commands is ON, while a mod
  that needs it is ON (`developer = true` in `bundled-mods.toml`, or `DEV_MODS` in `modman_gui.py`), or with
  `--port`; else `modman run` opens the port only for cheats.
- From the repo: `python tools/launcher.py [--library <folder>] [--size N] [--dark] [--port N] [<file.breath>]`.
  The launcher's choices go to `%LOCALAPPDATA%\Breathalyzer\launcher.json`, or to the folder `BREATHALYZER_HOME`
  names (use one for tests). `--search-in <folder>` looks for Savage Wind only there and one level below. One window
  per settings folder (a named mutex): a second start hands a `.breath` file over through `open\` (`la.pass_file`,
  `la.take_files`) and brings the open window to the front (`la.bring_to_front`): only the window `window.json`
  names (`la.publish_window`: its process id and window handle, checked to be that process's Tk window; never a
  window found by its title), and never while the game is open or the window hid itself for play. While you play
  the window takes in handed-over files but checks and shows them only once the game is closed
  (`LauncherView.next_breath`), and `LaunchController.can` keeps add, export and `.breath` waiting.
- The frame rate: `la.fps_info`, `la.read_fps`, `la.set_frame_rate` (`FPS_CHOICES`; only the `frame_rate` line, a
  temp file and a rename, never while `savagewind.exe` runs, never in a UTF-16 file or one with NUL bytes). The
  dropdown's own `<MouseWheel>` bindings return `break` (ttk's would pick the next value and write it). The `.breath`
  file type: `la.register_breath`, `la.unregister_breath`, `la.breath_state` (a `base` key other than
  `Software\Classes` for tests; it also reads, only reads, Explorer's `FileExts\.breath\UserChoice`). The format and
  its checks: `tools/breath.py`, [the .breath file](breath-format.md).
- `tools/launcher.py` has the parts without a window: finding and checking Savage Wind, the first-run steps
  (`FirstRun`), the settings file, the mod texts (mod.toml's `description`, else what the mod holds), the running
  game check (the process list, no `tasklist`), shortcuts (Windows' own shell link object, no PowerShell) and
  `read_bundled`. The window is `LauncherView` in `tools/modman_gui.py`; Play's steps are `LaunchController`
  (`modmenu.py apply` when the game's Mods page has changes, else `modman deploy` when the mods changed, then
  `modman run`). Tests: `tests/test_launcher.py` and `tests/test_breath.py`. The window tests in
  `tests/test_breath.py` use a hidden window (`root.withdraw()`) and always run. The ones that put a see-through
  window on the screen (`Window` in `tests/test_launcher.py`, `Layout` in `tests/test_modman_gui.py`) run only with
  `BREATHALYZER_WINDOW_TESTS=1`: never while someone uses the PC (the window can take the focus from their game).
