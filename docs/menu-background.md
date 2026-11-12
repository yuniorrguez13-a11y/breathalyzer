# Menu picture (your own picture on the main menu)

- **What it does:** `tools/menu_bg.py` puts a picture you pick (for example a shot of your character) on the game's
  main menu, in place of the game's own pictures. It is a mod in your mod library, so you can turn it off or remove
  it like any other mod.
- **Two looks:** `art` (default) puts your picture where the game's pictures are, on the left, with soft edges, over
  the game's blue pattern. `full` fills the whole screen, darker on the right so the menu words stay readable.
- **Works with** BotW 1.9.0 on Savage Wind only, when the game starts through Mod Manager or `python tools/modman.py
  run`. Tried in the game with tool version 1.0.0 (see [What is tested](#what-is-tested)); version 1.1.0 (the
  readable-words fix) is not tried in the game yet.

## Commands

```
python tools/menu_bg.py set linkle.png
python tools/menu_bg.py set linkle.png --mode full
python tools/menu_bg.py set a.png b.png c.png --sets all
python tools/menu_bg.py set linkle.png --sets 2 --focus left
python tools/menu_bg.py set linkle.png --name "My menu" --no-deploy
python tools/menu_bg.py reset
python tools/menu_bg.py status
python tools/menu_bg.py preview linkle.png --out preview.png
python tools/menu_bg.py preview linkle.png --mode full --set all --out sheet.png
python tools/menu_bg.py preview --out game-menu.png
```

| Option | What it does |
|---|---|
| `--mode art` / `--mode full` | where the picture goes (see below); default `art` |
| `--sets all` / `--sets 2` / `--sets 1,3,5` | which slideshow pictures get your picture; default all |
| `--focus left` / `center` / `right` / `top` | which part of your picture to keep when it has to be cut to fit; default center (a tall picture keeps its upper third, where the head usually is; `top` keeps the very top) |
| `--name NAME` | the mod's name in your library (default "Menu picture: <file name>") |
| `--no-deploy` | only put it in the library; deploy later with `python tools/modman.py deploy` |
| `--priority normal` | don't lower the tool's priority (it runs at below-normal priority, so the PC stays usable) |
| `--library <folder>` | the mod library, as for modman (else `SWMOD_LIBRARY`, else next to the Savage Wind folder) |

`set` deploys at once when the game is closed. When the game is running, it only puts the picture in the library and
says to deploy after closing the game. When your other mods need the game code rebuilt (10-20 minutes), it doesn't
deploy and says so. `reset` takes the picture out again and deploys the same way. Running `set` again replaces the
picture and keeps the mod in its place in the list.

The answer comes first, in short lines, for example:

```
Done: your picture is on the main menu (all 7 slideshow pictures).
Deployed. Start the game to see it: python tools/modman.py run
```

Only notes about the menu picture are shown; `python tools/modman.py deploy --dry-run` shows everything else. When a
title picture is chosen in the Mods page (or a mod brings one), that one is shown instead, and `set`, `reset` and
`status` say so.

Exit codes: 0 done, 2 error (one plain line says what to do), 130 stopped.

## The slideshow

The main menu is not one picture. It shows **7 pictures in a loop** (about 15 seconds each), and slowly pans each one.
By default your picture goes into all 7, so the menu always shows it. The numbers, in the order the menu shows them:

1. a rock at sunset (whole screen)
2. Link drawing a bow, on blue
3. a green field (whole screen)
4. a horse
5. climbing a cliff at sunset (whole screen)
6. Link drawing a bow, on gold
7. a guardian

`python tools/menu_bg.py preview --set all --out sheet.png` draws all 7 with their numbers, so you can check a number.
`--sets 2` changes only picture 2, so the other 6 stay the game's. With several pictures, the first chosen picture gets
the first file, the next chosen one the second file, and so on, starting again at the first file when there are more
chosen pictures than files (`--sets 2,4` with `a.png b.png`: 2 gets a.png, 4 gets b.png).

Between two pictures there is a short moment with no picture, where only the game's own pattern shows, in the
game's own colour for that moment (light cream around picture 5, for example). The game's own menu does the same; the
tool doesn't change it.

The tool finds the slideshow in the menu file itself: which pictures there are, when each shows, where it sits on
screen and how it pans. No picture name of the game is written in the tool.

## The two looks

**`art`** (default): your picture goes where each original picture is: on the left, about half the screen wide, with
soft edges on the sides that are on screen, over the game's own blue pattern, with the logo and menu on the right. In
the three pictures that cover the whole screen (1, 3 and 5), the tool draws the pattern itself, always in the dark
blue of picture 2, and puts your picture in the same place on the left, up to the screen's left edge. (The pattern's
own colour at those moments is hidden in the game's menu and can be light cream, where the words would be hard to
read.)

**`full`**: your picture fills the screen and gets darker towards the right (from a bit past the middle down to half
the light at the edge), so the menu words stay readable. A bright picture (sky, snow) gets darker still under the
words, until they are as readable as on the game's own menu. 3 of the 7 pictures cover the whole screen. The other 4
are drawn smaller by the game, on the left: there your picture fades into the pattern on the right. (Covering the
whole screen there too needs a change to the menu layout: not done yet.)

The game adds a soft glow on top of each picture. The tool darkens your picture a little first, so the glow doesn't
wash it out.

Your picture is cut to fit each place (it keeps its shape; `--focus` says which part stays). A picture smaller than
the screen is made bigger and looks soft: in `full` mode the tool says so below 1280 x 720.

## Cut-outs

A picture with see-through parts (more than 1 % of its pixels; PNG, WEBP, GIF or BMP) is used as a **cut-out**: in
`art` mode it is fitted whole into each place, standing on the bottom edge, with its own soft edge and the pattern
around it. In `full` mode the see-through parts become black (the tool says so). A picture with only a few
see-through pixels (rounded corners of a screenshot) is not a cut-out: those pixels become black, and the tool says
so.

## Pictures and Pillow

- **PNG always works**, also without Pillow (the tool reads PNG itself, every kind of PNG).
- **JPG, WEBP, BMP and GIF need Pillow.** Without it the tool says: save it as PNG, or install Pillow with
  `py -m pip install pillow`.
- A picture must be at least 640 x 360 for `full` (360 pixels on the short side for `art`), and at most 8192 pixels a
  side and 36 million pixels.

**How long it takes** (`set`, a 1920 x 1080 picture, all 7 pictures, measured on this PC with the game closed): about
30 s to 1.5 min with Pillow (a detailed photo took 1.5 min), up to about 2 min without. While the game runs, it takes
about twice as long. Most of it is compressing the textures (up to 45 s) and packing the menu file (30 to 45 s). A
step line says what it is doing.

## What it changes, and other mods

- **Only `Pack/Title.pack` changes**, and inside it only the menu's texture file. Every texture keeps its name, its
  format and its size, so the menu file keeps exactly its size. The mod ships **no size table (RSTB)**: the game's own
  entry still fits. The tool checks that against the game's table and every enabled mod's table before it changes
  anything.
- One menu file serves every language.
- **Other mods that ship `Pack/Title.pack`** (another menu mod): the tool builds on the last one that is turned on, says
  so, and keeps the menu picture after it in the list. The menu-picture mod keeps a copy of that menu file: if you
  add, update, turn off or remove such a mod later, run `set` again (`status` says when).
- `status` also says when the mod was made by an older version of the tool: run `set` again to get the fixes.
- Mods that change other files (Linkle, for example) don't clash with it.
- The library keeps `menu-picture.json` next to the mod: the tool version, the mode, the pictures (name and SHA-256),
  the game build and where the menu file came from.
- Savage Wind's own start screen (the window before the game) is something else: it shows `splash.png` from the folder
  of `savagewind.exe`, if there is one. No tool is needed for that.

## Preview

`preview` draws one frame of the menu (1280 x 720) to a PNG and changes nothing: the pattern, your picture (after the
same texture compression the game gets), the glow, the dark side panels, the logo, and plain white bars where the
menu words go. `--set all` draws a sheet of all 7 pictures at half size, with their numbers. Without a picture it
draws the menu as it is now. The PNG may not be written into the game folder or the mod library.

## For the Mods window

`menu_bg.gui_pick_and_set(parent, library, size=..., colors=..., done=...)` opens a file dialog, asks "Next to the
menu" or "Whole screen", then runs `set` in a separate process inside a window with big text, and calls `done()` when
the window is closed.

## Title picture at deploy (Mods page v2)

`modman deploy` also asks this tool for the **title picture**: the picture the player chose
(`<library>\pictures\_title\chosen.png`, `.jpg` or `.webp`), else the `[mod] title_picture` of the last enabled mod
that has one. `menu_bg.deploy_files(lib, plan, picture)` puts it into all 7 slideshow pictures (`art` look), over the
menu file in use, and gives it back as a file made at deploy (`Pack/Title.pack`, same size, no size-table entry).

- No title picture: nothing changes.
- A picture it can't use (too small, wrong game version, ...) gives one line under "Needs attention", and the menu
  stays as it is.
- The first deploy with a new picture takes as long as `set` (about 30 s to 1.5 min). The result is kept in
  `<library>\cache\menu-bg\` (the last 2), so later deploys with the same picture, menu file and tool version only
  read it.
- A title picture wins over the menu-picture mod from `set` (files made at deploy come last).

## Not done yet

- The boot picture before the menu (the tapestry).
- BC7 textures (better quality at the same size).
- `full` mode over the whole screen in all 7 pictures (needs a layout change).
- A DLC copy of the menu file (none on this PC; the tool refuses one).
- Calibrating the glow, soft edges and darkening in the game: the numbers are first guesses.
- The logo in `full` mode on a bright picture: only the words' area is measured and darkened (the logo is partly in
  the darker columns).

## What is tested

- **With made-up files** (`tests/test_menu_bg.py`): PNG of every kind, the texture codec, picture fitting and soft
  edges, the layout and animation readers on a made-up menu (with a set that runs past the loop's end), both modes,
  that the words' area stays dark (a white picture in `full` mode, a near-white pattern colour in `art` mode), the
  picture order, the library steps, every refusal, deploy and its notes, a chosen title picture, `status`, preview,
  the window's command line, the title picture at deploy (cache, problems as one line), and that every command on this
  page parses.
- **On this PC's game files** (read only, results kept in `out\`): the tool finds 7 pictures in 10 textures, rebuilds
  the menu file at its exact size, and its preview of the game's own menu matches a real screenshot (pattern and
  side panels within 2 colour steps). Textures written and read back come out at 38-44 dB of what was meant.
- **In the game (tool 1.0.0, 2026-11-12):** both looks and the reset show in the game, with Linkle, with no errors in
  the log; odd-size textures show right. Found there: in `art` mode picture 5 had a light cream pattern under the
  words (hard to read), and `full` mode keeps too much light under the words on a bright picture. Both are fixed in
  1.1.0, which is not tried in the game yet.
