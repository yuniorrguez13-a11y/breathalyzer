# Mod pictures

Every mod has a picture. Mod Manager shows it big. In the game, pictures are **off** until you turn them on.

> **Not shown in the game yet.** The deploy makes the pictures and the Mods page reads them, but drawing them is still
> to come: turning them on shows nothing new yet. It still costs: Bootup.pack is replaced (Savage Wind's HUD button
> letters go back to the game's), and when no mod ships a size table a full one is shipped (Savage Wind's voice fix
> turns off). Leave them off for now. The title-screen picture (tools/menu_bg.py) is not affected: it works.

```
python tools/mod_pictures.py on                 # a big picture of the selected mod beside the Mods page
python tools/mod_pictures.py on --rows --background
python tools/mod_pictures.py off
python tools/mod_pictures.py status             # on or off, and where each mod's picture comes from
python tools/mod_pictures.py refresh [<mod>]    # make the pictures again
```

All of them take `--library <folder>`. A change is used at your next start (Mod Manager deploys by itself).

## Where a mod's picture comes from

The first one that exists:

1. the one you picked in Mod Manager (kept in `<library>\pictures\<id>\`, never in the mod's folder);
2. the mod's own: `[mod] icon` in its `mod.toml` ([mod-format](mod-format.md#the-keys));
3. a BCML package's picture, only when it is a file in the package (a web address is never downloaded);
4. `thumbnail.png` (or `icon.png`) at the mod's root;
5. a tile in a strong colour with the mod's first letter in white.

A picture that can't be read is skipped (the next one is used). PNG always works. JPG and WEBP need Pillow
(`py -m pip install pillow`). Without Pillow a PNG may have up to about 2 million pixels (1920 x 1080); with Pillow up
to 4096 x 4096. At most 8 MB. Square pictures look best: others are cut to a square around their middle.

A mod can also set the title-screen picture (`[mod] title_picture`): tools/menu_bg.py puts it on the main menu.

## In the game (planned: not drawn yet)

- **On:** a 256 px picture of the selected mod, right of the Mods page list. It follows the cursor.
- `--rows`: also a small picture at the left of each mod's row.
- `--background`: also a dark, soft picture behind the list: the picture you picked for the Mods page
  (`<library>\pictures\_mods-page\chosen.png`), or a mod's own `[mod] background`. Dark, so the words stay readable.

Up to 64 mods get a picture in the game. The pictures are made once and kept; a deploy with the same mods and pictures
only reads them.

## What turning pictures on costs

- Pictures go into `Pack/Bootup.pack` (the file holding the game's menus). They are built over the Bootup.pack the
  game would use anyway: a mod's, or the game's. **Every other file in that pack stays byte for byte the same**; this
  is checked after each build, and on any difference the pictures are left out (one line in the deploy says so). The
  pack also holds the save format, so this check keeps your saves safe.
- A bigger menu file needs a bigger **size-table (RSTB) entry**. When a mod (like Linkle) ships a size table, the
  entry goes into that table and its other entries stay. When no mod ships one, the game's table is shipped with the
  entry added. Then Savage Wind 0.4.1-alpha.4 turns one of its own fixes off: keep the voice language the same as the
  text language. With pictures off and no mod table, no size table is shipped.
- Savage Wind's HUD button letters stay as the game's while a Bootup.pack is in the overlay.

## Files

| File | What it holds |
|---|---|
| `<library>\pictures\<id>\chosen.png` | The picture you picked for a mod (or `.jpg`, `.jpeg`, `.webp`) |
| `<library>\pictures\_title\chosen.png` | The title-screen picture you picked |
| `<library>\pictures\_mods-page\chosen.png` | The picture you picked for behind the Mods page |
| `<library>\pictures\<id>\icon-64.png`, `icon-128.png`, `bg-320.png`, `pictures.json` | Made from the source, kept until it changes |
| `<library>\pictures\settings.json` | On or off, rows, background |
| `<library>\cache\pictures\` | The last Bootup.pack made (one, about 32 MB) |
| `deploy\pictures\atlas.json` | Where each mod's picture is, for the Mods page |

## For the Mods page code

`deploy\pictures\atlas.json` (format 1): `grids` (grid 0: the mod pictures, texture `BzModIcons_00^l`, 128 px cells,
4 x 4 or 8 x 8, shown by `P_ModPanel_00` and, with rows, `P_ModIcon_00`; grid 1: backgrounds, `BzModBg_00^l`, 320 x 180
cells, 4 x 4, shown by `P_ModBg_00`) and `cells` (`{"<mod id>": {"icon": n, "bg": n or -1}}`, `_mods-page` for the
picture behind the list). The new panes are hidden; the Mods-page mod shows them on its own pages only and points
them at a cell (texture coordinates). Layout and texture work is in `tools/layout_patch.py`.
