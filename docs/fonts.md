# Fonts in the menus

`tools/font.py` fixes the symbols the game's menu font is missing, adds an easy-to-read font, and builds font mods.
It works at deploy, on your PC, with the released Savage Wind 0.4.1-alpha.4.

## What you get

- **Missing symbols fixed.** The Options and Mods pages can't show `{ } # $ % @ ^` `` ` `` `| ~` (Japanese also misses
  `" & ' * ; = \`). With the fix on, they show. They are drawn from Fira Sans to match the game's letters.
- **Easy-read font.** Fira Sans SemiBold in the menus: wider, bolder letters.
- **Font mods.** A mod can bring its own font for the menus (English, European languages and Japanese).
- **One place to choose.** You pick the font: the game's, easy-read, or a font mod's. Nothing is hidden.

Checked offline on the game's own font files (US, EU and JP): the archive keeps its exact size, the button icons
(External_00) stay byte for byte, and every letter that isn't redrawn keeps its exact pixels. Not yet checked in the
game: see [What is tested](#what-is-tested).

## Quick start

```powershell
python tools\font.py status                 # the font in use and what you can choose
python tools\font.py fix-symbols on         # draw the missing symbols
python tools\font.py choose easy-read       # or: game, auto, or a font mod's id
python tools\modman.py deploy               # builds the font files
```

After a change, Play deploys again by itself (or run `python tools\modman.py deploy`). `status` says whether the
last deploy has your settings. Fonts load once when the game starts, so the change shows at your next start.
`--library <folder>` picks the mod library (as for `modman.py`).

## Choosing the font

| Choice | What the menus use |
|---|---|
| `auto` (the default) | The last font mod that is on, in your mod order. Else the game's font. |
| `game` | The game's font. |
| `easy-read` | Fira Sans SemiBold. |
| a font mod's id | That mod's font. The mod must be on. |

Your choice wins over the mod order. When the chosen mod is off or removed, `auto` is used and deploy says so.
The font choice and the symbol fix are saved in `font.json` in the library.

There is one chooser. This tool, Mod Manager's window and the game's Mods page all set the same choice in the
same way, so they never disagree. A mod set ([profiles](profiles.md)) keeps its font choice and symbol fix with
its mods: switch sets, and the font switches too.

The fix for missing symbols is off by default until it is checked in the game. A font (easy-read or a mod's) adds
the missing symbols it has by itself.

## Making a font mod

```toml
[mod]
id = "yourname.my-font"
name = "My Font"
version = "1.0"

[font]
file = "fonts/MyFont.ttf"      # inside the mod, next to mod.toml or below it
for = "menu"                   # the Options, Mods, dialogue and button texts
languages = ["US", "EU", "JP"] # the default: all three
license = "OFL-1.1"            # optional
```

| Key | Needed | What it is |
|---|---|---|
| `file` | yes | A TrueType font (`.ttf`) inside the mod. Fonts with CFF outlines (most `.otf`) and collections (`.ttc`) are refused with a clear line: use the `.ttf` version. |
| `for` | no (`"menu"`) | `"menu"` redraws Caption_00, NormalS_00 and Normal_00: everything the Options and Mods pages draw. `"all"` (also place names, Special_00) comes later; it works as `"menu"` for now. |
| `languages` | no (all three) | `"US"`, `"EU"`, `"JP"`. Korean, Chinese and Thai use other fonts and come later. |
| `license` | no | Shown to the player. Without it, the font's own licence words are read. |

How the font is drawn:
- Every character the font has is redrawn in the game's own grid, at the size that gives its `H` the game's height.
  Characters the font lacks (kana and kanji, say) keep the game's drawing.
- Letters too wide for a cell are narrowed. Accents and tails too tall for a cell are pressed flat a little.
- NormalS_00 gets the game's dark ring around each letter (2 pixels).
- Licences: `python tools\font.py check <font.ttf>` shows what the font says about itself. A font that isn't free
  (Arial, Segoe UI) is fine on your own PC: deploy warns, and never blocks. Don't share what it builds.
- Sharing a font mod shares its font file. Share it only when the font's licence allows that (Fira Sans and other
  SIL Open Font License fonts do).

## What happens at deploy

modman's deploy asks `font.deploy_files(lib, plan)` for the font archives. It reads the archive in use: a RomFS
mod's `Font/Font_xx.sbfarc` when one ships it, else the game's own (both only read). It writes edited copies into
the overlay, for US, EU and JP:

- The missing printable ASCII characters go into free cells, in 4x4 blocks no other letter uses. The rest of each
  sheet keeps its bytes.
- The edited fonts are a few bytes longer. The padding between files takes them, so the archive keeps its size
  (Font_US: 3,641,808 bytes before and after) and **no size-table entry is needed**. If an archive ever grows,
  deploy names the entry it needs.
- External_00 (the button icons Savage Wind redraws) is never changed.
- Built archives are cached in `fonts\cache\` in the library, so a deploy with the same mods and settings only reads.
- Deploy also tells the Mods page whether every menu font now has all of ASCII. Then the Mods page stops writing
  `%` as "percent" and `@` as "at" (`symbols full` in menu.txt, docs/mod-menu.md).

The edited archives hold the game's own glyphs: they stay in your library and are never shared.

Fira Sans comes from Savage Wind's folder (`FiraSans-Medium.ttf`, `FiraSans-SemiBold.ttf`, next to
`savagewind.exe`), else from `assets\fonts\` in this repo. Both are the SIL Open Font License 1.1
(`assets\fonts\FiraSans-OFL.txt`).

## Checking offline

```powershell
python tools\font.py build "<Savage Wind folder>\extracted\romfs\Font\Font_US.sbfarc" `
    --out out\Font_US.sbfarc --fix-symbols
python tools\font.py preview out\Font_US.sbfarc --out out\caption.png --text "Mods {beta} 50% @you"
```

`build` writes one edited archive and says what changed: characters added, letters redrawn, the size before and
after. `preview` draws text with one font of an archive (`--font-name`, default Caption_00), 3 times bigger, white
on black. Write them in `out\`: they hold game data. `build` refuses to write into Savage Wind's game files.

## Good to know

- Savage Wind's "Sharper text" isn't in 0.4.1-alpha.4. A later release that has it may not sharpen edited fonts
  (it looks for the game's own sheets). The text then looks as sharp as the game's at 1080p.
- Every menu font change applies at the next start (the game loads fonts once).
- Korean, Chinese, Thai and Traditional Chinese text uses outline fonts, which this tool doesn't edit yet.

## For other tools

All in `tools/font.py`; modman loads it lazily, so without it fonts are simply off.

| Call | What it is for |
|---|---|
| `deploy_files(lib, plan)` | Deploy: the edited `Font/Font_xx.sbfarc` files over the plan's winner, as `(path, bytes, size-table entry or None)`. The list also carries `symbols_full`, `font`, `font_key`, `fix_symbols`, `notes`, `attention` (launch.json `made["fonts"]`) |
| `choices(lib)`, `current(lib)` | The chooser's rows (game, easy-read, each font mod that is on) and the font in use |
| `choose(lib, key)` | Set the font: `auto`, `game`, `easy-read` or a font mod's id. Refuses a font mod that is off or broken with one plain line |
| `load_settings(lib)`, `save_settings(lib, fix_symbols=...)` | `font.json` |
| `hash_inputs(lib)` | For modman's deploy check: what the fonts are made from, or None when deploy makes none |
| `profile_part(lib)`, `apply_profile_part(lib, part)` | A mod set saves and restores its font settings |
| `on_add(lib, entry, folder, manifest)` | Notes when a font mod is added (its licence, a font it can't draw) |
| `share_notes(mod folder)` | A warning when a shared mod would hand on a font that doesn't say it is free |

## What is tested

Offline (tests/test_font.py, with made-up fonts and archives; and on the game's own US, EU and JP archives):
- the 10 symbols (US, EU) and 17 (JP) are added; nothing else changes; the archive keeps its size;
- the easy-read font redraws 130 (US), 199 (EU) and 82 (JP) caption characters, about 335 in each other menu font,
  with no letter cut off;
- External_00 stays byte for byte; the cache is used on the second deploy;
- the chooser, the deploy check's inputs and the mod-set round trip.

In the game (2026-11-18, with the Mods page v2, commit 65003e3): the symbols # $ % @ ^ ` { | } ~ show on the Mods
page and the easy-read font works. Not yet checked in the game: that other texts look the same, and the look of
NormalS_00's ring.
