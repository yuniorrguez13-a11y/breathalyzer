# Mod pictures

Being built (Mods page v2). Every mod can have a picture:

1. the one you picked in Mod Manager (kept in `<library>\pictures\<id>\`, never in the mod's folder);
2. else the mod's own: `[mod] icon` in its `mod.toml` ([mod-format](mod-format.md#the-keys));
3. else a BCML package's picture, only when it is a file in the package (a web address is never downloaded);
4. else a `thumbnail.png` at the mod's root;
5. else a tile with the mod's first letter.

Mod Manager shows the pictures big. In the game, pictures are off until you turn them on: first a big picture of the
selected mod beside the list, later small row icons and a picture behind the list (`[mod] background`, or one you
pick). A mod can also set the title-screen picture (`[mod] title_picture`).

This page is a stub: `tools/mod_pictures.py`, `tools/layout_patch.py` and this page are being written.

## What turning pictures on costs

Pictures in the game need a size-table (RSTB) entry. When no mod ships a size table, adding one turns off one of
Savage Wind 0.4.1-alpha.4's own fixes: then keep the voice language the same as the text language. When a mod (like
Linkle) ships one already, the entry goes into that table and nothing else changes.

## Files

| File | What it holds |
|---|---|
| `<library>\pictures\<id>\chosen.png` | The picture you picked for a mod (or `.jpg`, `.jpeg`, `.webp`) |
| `<library>\pictures\_title\chosen.png` | The title-screen picture you picked |
| `<library>\pictures\_mods-page\chosen.png` | The picture you picked for behind the Mods page |
| `<library>\pictures\<id>\icon-64.png`, `icon-128.png`, `bg-320.png`, `pictures.json` | Made from the source, kept until it changes |
| `deploy\pictures\atlas.json` | Where each mod's picture is, for the Mods page |
