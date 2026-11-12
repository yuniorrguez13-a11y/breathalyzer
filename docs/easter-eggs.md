# Easter eggs and secret extras (spoilers)

**Spoilers below.** Breathalyzer has a few hidden things. Nothing in the launcher or the game names them:

- **Easter eggs** (`egg = true` in `tools/packaging/bundled-mods.toml`): the mod picker has one switch, **Enable
  easter eggs?**, OFF at first, and Home one **Easter eggs** row; either turns every egg on or off together. An egg
  left OFF isn't put in your mod list at all. Details never shows an egg's id or title.
- **Secret mods** (`[mod] secret = true` in a mod.toml, [mod format](mod-format.md)): never listed on Home, in
  More... (Mod Manager) or on the game's Mods page, and the game can't turn them on or off. The eggs are secret, and
  so are the two parts of the AK-47. `python tools/modman.py list` still shows them, for developers.

## The secret word (`botw-mod-loader.zelda-view`)

An easter egg for the game's console (press F12). Type **`linkfromzeldasview`**: Link gets extremely buff, swings
two-handed weapons (claymores) as fast as one-handed swords, and carries Zelda piggyback. Type it again and all of it
goes away. It starts OFF at every game start and stays on through reloads; `linkfromzeldasview on`, `off` and
`status` work too. It is registered with an empty help line.

- Turn it on with the easter-egg switch (in the picker, or Home's Easter eggs row). Its four hooks make the first
  start after that 10-20 minutes longer (the game code is rebuilt once, then cached).
- Its settings: how buff (Extreme, Buff, Off), wider shoulders, fast heavy swings, Zelda on his back, and how far
  back she sits. A secret mod isn't listed anywhere, so they keep their defaults unless you set them by hand:
  `python tools/mod_settings.py set botw-mod-loader.zelda-view buff buff` (`show` lists them).
- Each part checks itself and switches off alone if something is wrong; the others keep working. Zelda needs her
  actor pack, built on your PC from your own game files (`tools/zelda_view.py install`, not in this repo yet):
  without it that part stays off.
- By hand: `python tools/build_native.py build`, `python tools/modman.py add native/examples/zelda_view`, then
  deploy. The source is [`native/examples/zelda_view/`](../native/examples/zelda_view/).

Status: not tested in the game yet.

## The AK-47 (comes with Ghost multiplayer)

A joke weapon that comes with the multiplayer mod ([ghost](ghost.md)): an **AK-47** that never fires. It is a
two-handed sword with a rifle's looks: you hold it by the stock and swing it like a claymore, with the Traveler's
Claymore's strength, durability, sounds and moves. Its description in the inventory:

> Nobody in Hyrule knows how to fire it, so you swing it like a claymore. It's heavy, it's loud-looking, and it's
> surprisingly good at bonking Bokoblins.

### Where is it?

One lies somewhere on the Great Plateau. A hint, and only a hint: *wake up, walk out, and look west. The highest hill
on that side keeps something that doesn't belong in Hyrule.* Once picked up it comes back after a Blood Moon, like
the game's own weapons. (The spot is in `assets/ak47/ak47.json`, if you really want to spoil it.)

### Two secret mods

It is built as two mods, both secret (never listed):

- **`giruka.ak47`, the item** ("Extra item"): the weapon's model, actor, texts and flags. Once built it **stays on**,
  even while the multiplayer mod is off, so a save that holds the AK-47 always loads. (A save with an item the game
  has no data for can break.)
- **`giruka.ak47-spot`, where it lies** ("Extra item spot"): one AK-47 on the hilltop. Its mod.toml says
  `part_of = "botw-mod-loader.ghost"`, so modman uses it only while the multiplayer mod is on: turn multiplayer off
  and no AK-47 lies on the hill any more (one you carry stays yours).

Breathalyzer Plus builds them the first time you turn the multiplayer mod on (`after_on` in
`tools/packaging/bundled-mods.toml`, about 30 seconds): at the picker's Save with it ON, else at the next Play (after
Home or the game's Mods page turned it on). A player who never turns multiplayer on never builds it. By hand:

```
python tools/ak47_build.py build              build both and add them to your library
python tools/ak47_build.py build --verbose    the same, and say where it lies (a spoiler)
python tools/ak47_build.py check              offline checks on your files (reads only, changes nothing)
python tools/ak47_build.py icon --out ak.png  draw its inventory icon to a picture (changes nothing)
python tools/ak47_build.py where              the game files it is built from
```

Then `python tools/modman.py deploy`. `build --no-add --out <folder>` only writes the two mod folders into that
folder; `--model <file.glb>` uses another model (a static, textured binary glTF, at most 60000 vertices; see
`assets/ak47/ak47.json` for how it is turned); `--optional` makes a build that can't be made, for any reason, end
with exit code 0 (the launcher uses it, so the multiplayer mod is never stopped by it); `--quiet` names nothing (the
launcher's Details). Build it again after a Savage Wind update.

### How it is made

Nothing from the game is in this repo or in a release: `tools/ak47_build.py` makes everything on your PC from your
own `extracted\romfs` (only read) and the model in `assets/ak47/`, as BCML-style packages that `modman add` merges
the normal way. At deploy, the files the AK-47 shares with other mods of that kind (the actor info, `Bootup.pack`'s
flags, the message packs: the castle date changes them too) are merged, not replaced: both mods' changes stay.

- **The actor** `Weapon_Lsword_AK47`: the Traveler's Claymore's actor pack (`Weapon_Lsword_001`) with its actor link
  and model list renamed and pointed at the new model. The amiibo and Hyrule Compendium tags are dropped, so it never
  turns up anywhere else. Its actor info is the claymore's (renamed, with its own model bounds and sort key).
- **The model**: the claymore's model file (its one `Root` bone and its material, so the game's two-handed grip, the
  carry-on-the-back spot and the effects stay right) with the AK-47's mesh. The rifle is turned so its barrel points
  where the blade does, scaled to the blade's full length (a 2 m AK-47: it is a claymore), and its stock sits where
  the hilt is, centred on it, so Link's hands are on the stock. Back faces are added for parts of the model with
  open edges.
- **The textures**: the model's pictures packed into one atlas in the claymore's formats and sizes (BC1 colour,
  BC5 flat normal map, BC5 specular from the claymore's own average, less shine on wood), with mipmaps.
- **The inventory icon**: drawn from the model (diagonal, like the game's icons), in the claymore's icon format.
- **Texts**: "AK-47" and its description (`ActorType/WeaponLargeSword`, every language gets the English text).
- **Flags**: `IsGet_Weapon_Lsword_AK47` and the placed one's pick-up flag (reset by the Blood Moon), shaped like the
  claymore's; their save data entries. Both are in the item mod, so a save keeps them.
- **The map** (the spot mod): one AK-47 lying on the hilltop (a loose `_Dynamic` map unit, not `TitleBG.pack`). Its
  height is read from your terrain files at build time (`Terrain/A/MainField.tscb` and the height maps), 25 cm above
  the ground, as the game places its own weapons; its HashId is one that neither the game's map nor another mod in
  your library uses. `check` makes sure the spot is a hilltop, gentle enough for a weapon to lie, with nothing the
  game places (static trees and rocks in `TitleBG.pack`, or dynamic objects) within 3 m, and that the spot's HashId
  is still free.
- **Size table entries** for the new files, from the game's entries for the files they come from.

The builder is pure Python (no packages to install): its glTF reader, the geometry and the icon's rasterizer are its
own; the BC encoders are `tools/menu_bg.py`'s and the Switch model and texture writers are `tools/wiiu_model`'s, and
both writers' self-checks run before anything is written.

In co-op, a friend whose game has no AK-47 (not built, or a build that failed) shows yours as nothing in the ghost's
hands: their game refuses the unknown name, tries 3 times and gives up (`ghost items` says why).

### Credits

- Idea: giruka. Mod: giruka (Breathalyzer).
- Model: ["PSX style AK-47 Model"](https://plewr.itch.io/psx-style-ak-47-model) by **Plewr**, CC0 (public domain).
  No credit is required; thank you anyway. Its metal texture is the U.S. Department of Defense photo DM-ST-89-01131
  (public domain). Its wood texture was a photo whose source isn't known, so ours is drawn by `tools/ak47_build.py`
  instead (`python tools/ak47_build.py rewood --model <download> --out <file>`); everything else is the download's.
  The hashes of both files are in `assets/ak47/MODEL-CREDITS.txt`.

### Status

Built and checked offline on the real game files (the model and textures pass the Switch writers' self-checks, the
logs merge into the game's files with no notes). Not yet seen in the game: what it looks like in Link's hands, on his
back and on the hilltop is the next check.
