# Menu picture (your own picture on the main menu)

- **What it does:** `tools/menu_bg.py` puts a picture you pick (for example a shot of your character) on the game's
  main menu, in place of the game's own pictures. It is a mod in your mod library, so you can turn it off or remove
  it like any other mod.
- **Two looks:** `art` (default) puts your picture where the game's pictures are, on the left, with soft edges, over
  the game's blue pattern. `full` fills the whole screen, darker on the right so the menu words stay readable.
- **Works with** BotW 1.9.0 on Savage Wind only, when the game starts through Mod Manager or `python tools/modman.py
  run`. Not tried in the game yet (see [What is tested](#what-is-tested)).

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
| `--focus left` / `center` / `right` | which part of your picture to keep when it has to be cut to fit; default center |
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
Deployed. Start the game to see it.
```

Exit codes: 0 done, 2 error (one plain line says what to do), 130 stopped.

## The slideshow

The main menu is not one picture. It shows **7 pictures in a loop** (about 15 seconds each), and slowly pans each one.
By default your picture goes into all 7, so the menu always shows it. `--sets 2` changes only the second one (the
picture of the bow), so the other 6 stay the game's. With several pictures, slideshow picture 1 gets the first file,
picture 2 the second, and so on, starting again at the first file when there are fewer than 7.

The tool finds the slideshow in the menu file itself: which pictures there are, when each shows, where it sits on
screen and how it pans. No picture name of the game is written in the tool.

## The two looks

**`art`** (default): your picture goes where each original picture is: on the left, about half the screen wide, with
soft edges on the sides that are on screen, over the game's own blue pattern, with the logo and menu on the right. In
the three pictures that cover the whole screen, the tool draws the game's pattern itself and puts your picture in the
same place on the left.

**`full`**: your picture fills the screen and gets darker towards the right (from a bit past the middle down to half
the light at the edge), so the menu words stay readable. 3 of the 7 pictures cover the whole screen. The other 4 are
drawn smaller by the game, on the left: there your picture fades into the pattern on the right. (Covering the whole
screen there too needs a change to the menu layout: not done yet.)

The game adds a soft glow on top of each picture. The tool darkens your picture a little first, so the glow doesn't
wash it out.

Your picture is cut to fit each place (it keeps its shape; `--focus` says which part stays). A picture smaller than
the screen is made bigger and looks soft: in `full` mode the tool says so below 1280 x 720.

## Cut-outs

A PNG with see-through parts (more than 1 % of its pixels) is used as a **cut-out**: in `art` mode it is fitted whole
into each place, standing on the bottom edge, with its own soft edge and the pattern around it. In `full` mode the
see-through parts become black (the tool says so).

## Pictures and Pillow

- **PNG always works**, also without Pillow (the tool reads PNG itself, every kind of PNG).
- **JPG, WEBP, BMP and GIF need Pillow.** Without it the tool says: save it as PNG, or install Pillow with
  `py -m pip install pillow`.
- A picture must be at least 640 x 360 for `full` (360 pixels on the short side for `art`), and at most 8192 pixels a
  side and 36 million pixels.

**How long it takes** (`set`, a 1920 x 1080 PNG, all 7 pictures, measured on this PC with the game closed): about
30 s to 1 min with Pillow, about 45 s to 1.5 min without. A busy, detailed picture takes longest. While the game runs,
it takes about twice as long. Most of it is compressing the textures and packing the menu file (up to 30 s each). A
step line says what it is doing.

## What it changes, and other mods

- **Only `Pack/Title.pack` changes**, and inside it only the menu's texture file. Every texture keeps its name, its
  format and its size, so the menu file keeps exactly its size. The mod ships **no size table (RSTB)**: the game's own
  entry still fits. The tool checks that against the game's table and every enabled mod's table before it changes
  anything.
- One menu file serves every language.
- **Other mods that ship `Pack/Title.pack`** (another menu mod): the tool builds on the last one that is turned on, says
  so, and keeps the menu picture after it in the list. If you add such a mod later, run `set` again.
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

## Not done yet

- The boot picture before the menu (the tapestry).
- BC7 textures (better quality at the same size).
- `full` mode over the whole screen in all 7 pictures (needs a layout change).
- A DLC copy of the menu file (none on this PC; the tool refuses one).
- Calibrating the glow, soft edges and darkening in the game: the numbers are first guesses.

## What is tested

- **With made-up files** (`tests/test_menu_bg.py`): PNG of every kind, the texture codec, picture fitting and soft
  edges, the layout and animation readers on a made-up menu, both modes, the library steps, every refusal, deploy,
  preview, the window's command line, and that every command on this page parses.
- **On this PC's game files** (read only, results kept in `out\`): the tool finds 7 pictures in 10 textures, rebuilds
  the menu file at its exact size, and its preview of the game's own menu matches a real screenshot (pattern and
  side panels within 2 colour steps). Textures written and read back come out at 38-44 dB of what was meant.
- **In the game:** not yet.
