# Performance: more fps on a weak PC

**For more fps, in this order:**

1. **Frame rate 60, not 30.** In the game: F1 (or Esc) > Video > Frame rate: 60. At 30 the game never shows more
   than 30 fps, whatever else you change.
2. **Laptop:** plug in the charger and pick Windows' *Best performance* power mode.
3. **Frame generation off** and **upscaler on FSR 1, Ultra performance** (Video page). With frame generation on, the
   game fell to about 8 fps here.
4. **Try a quality preset:** `python tools/quality.py set low --deploy`, then start the game with
   `python tools/quality.py run`. Then choose the Savage Wind settings it prints, in the game's Video page. Its gain
   on this laptop is **not proven** (below).

## Measured (2026-11-07, this laptop)

**Short version:** frame rate 60, frame generation off and Ultra performance are sure. The presets' gain is not:
other programs kept the laptop busy during every test, and that load moved fps more than any preset did.

How: Ryzen 5 7535U with Radeon 660M, Savage Wind 0.4.1-alpha.4, 1280x720 window, FSR 1 Ultra performance, **no frame
limit** (to see what the PC can do), test save. Every run is its own game start: warp, turn to a fixed view, wait
25 s, then the average fps over 30 s. The setups took turns, in a different order in each of 3 rounds. Other
programs used **54-78 % of the processor** during these runs (other tools' tests and two disk-wide searches), and it
changed from run to run.

| Setup (all with Ultra performance) | Great Plateau (far view, grass): rounds 1 / 2 / 3 | Hyrule Field (Riverside Stable): rounds 1 / 2 / 3 |
|---|---|---|
| High (no mod; Original is the same with the upscaler Off) | 41 / 26 / 27 fps | 56 / 44 / 39 fps |
| Grass 40 % only | 43 / 35 / 36 | 57 / 57 / 34 |
| **Low** (grass 40 %, effects off) | 42 / 41 / 22 | 62 / 61 / 36 |
| Low + objects 60 % (in no preset) | 43 / 47 / 40 | 62 / 71 / 50 |
| Other programs' share of the processor | 54-78 % | 56-75 % |

- **Background load decides most:** each 1 % of the processor other programs take cost about 1 fps (0.8 on the
  Plateau, 1.5 in the Field). The same setup varied by up to 27 fps between rounds.
- **Low vs High:** in round 1 (the least busy), Plateau 42 vs 41 fps, Field 62 vs 56. Allowing for the load (a
  straight-line fit over all 24 runs), Low came out about 3 fps faster on the Plateau and 7 in the Field, each give
  or take 6: **likely a small gain, not proven.** In round 1 Low also had fewer slow frames (over 33 ms in 30 s:
  Plateau 15 vs 26, Field 4 vs 13).
- **Objects 60 %** came out 3-4 fps over Low, give or take 6: not proven either, and it has a visual and gameplay
  cost ([levers](#the-levers)), so it stays out of the presets.
- To measure the presets properly, close other programs (and other tools' tests) first.

The earlier rounds (13:00-14:25) are not used: the presets always ran in the same order, "Low" then also had Remove
Grass on, and several setting changes never applied. They stay, marked, in `out\perf\results.jsonl`.

Savage Wind's own settings, Hyrule Field, High, changed while the game ran (each about 20 s; one game start per
round):

| Setting | fps: rounds 1 / 2 / 3 |
|---|---|
| Upscaler Ultra performance (yours), measured twice per round | 60 and 56 / 46 and 43 / 42 and 29 |
| Performance | 54 / 38 / 30 |
| Quality | 51 / 35 / 35 |
| Upscaler Off (an earlier round, one run) | 52, against 61 with Ultra performance |
| Shadow resolution High (2048) instead of Console (an earlier round, Plateau, one run) | 50, against 50: no change seen |
| Frame generation On | **8**, twice: no frame limit (Plateau) and your settings, frame rate 60 + vsync fast (Field, 32-36 with it off) |

So keep **Ultra performance**: Quality and Performance were slower in rounds 1 and 2 (round 3 was too busy to tell).
Whether the processor or the graphics chip is the limit here we can't say from runs this busy; your other laptop (same
model) was limited by the graphics chip, and Ultra performance made it playable.

**Stability (Low as it ships):** 5 minutes on the Plateau (short walks, camera turning) and 5 minutes running a
165 m square in Hyrule Field: no crash; ground, grass, rocks and trees near Link were all there. The king's voiced
reveal on the Temple of Time roof also played with Low on ([below](#the-built-mods)).

Screenshots of every test: `out\shots\perf3_*.png` in your copy of this repo (they stay on your PC; screenshots of the
game never go into the repo).

## Presets

`tools/quality.py` turns the levers below on or off in your mod library, as a bundle, like a PC game's quality
setting. It never changes Savage Wind's folder or your `savagewind.ini`.

| | Low | Medium | High | Original |
|---|---|---|---|---|
| Grass distance | 40% | 60% | 100% | 100% |
| Object draw distance | 100% | 100% | 100% | 100% |
| Sun-shadow range | the game's | the game's | the game's | the game's |
| Ambient occlusion | off | on | on | on |
| Bloom (glow) | off | on | on | on |
| Far blur (depth of field) | off | on | on | on |
| Remove Grass mod | off | off | off | off |
| Replaces a game file | `Pack/Bootup.pack` ([cost](#the-built-mods)) | no | no | no |
| *Savage Wind:* Frame rate | 60 | 60 | 60 | 60 |
| *Savage Wind:* Upscaler | FSR 1 | FSR 1 | FSR 1 | Off |
| *Savage Wind:* Upscaler quality | Ultra performance | Ultra performance | Ultra performance | - |
| *Savage Wind:* Shadow resolution | Console | Console | Console | Console |
| *Savage Wind:* Draw distance | Original | Original | Original | Original |
| *Savage Wind:* Anti-aliasing | Off | Off | Off | Off |

**What each one is for:**

- **Low:** short grass, no glow, no ambient occlusion, no far blur. The ground looks lighter without ambient
  occlusion. It replaces `Pack/Bootup.pack`, so Savage Wind's HUD button letters and voice size fix are off.
- **Medium:** shorter grass only; no game file replaced. Not measured on its own (the grass lever was, at 40%).
- **High:** the game's look with no mod; only Savage Wind's upscaler (Ultra performance) differs from Original.
- **Original:** the game as it is: no quality mods, no start-up values.

```
python tools/quality.py list                        # the presets and what each one turns on
python tools/quality.py set low|medium|high|original --deploy
python tools/quality.py toggle ssao off             # change one lever on top of the preset
python tools/quality.py status                      # what the library uses now
python tools/quality.py run                         # start the game with the preset (as modman run)
python tools/quality.py env                         # the start-up values as PowerShell lines
python tools/quality.py json                        # the presets as JSON (for the in-game mod menu)
```

`--library <folder>` picks another library, as for modman (default: `SWMOD_LIBRARY`, else `modloader` next to
Savage Wind). After `set` or `toggle` without `--deploy`, run `python tools/modman.py deploy`.

**Start the game with `quality.py run`**, not `modman run` or Savage Wind's own window: the grass and object
distances are start-up values Savage Wind reads when it starts, and only `quality.py run` sets them (they are kept
in `quality.json` in the library). `modman run` started from a window where they are set passes them on too.

## The levers

| Lever | What it does | How | Risk |
|---|---|---|---|
| Grass distance | Grass ends nearer (the game: blades ~21 m, tufts ~46 m). Grass by Link stays: you can still cut it and burn it | Start-up values `SAVAGEWIND_GRASS_BLADES` and `SAVAGEWIND_GRASS_QUADS` | Low |
| Object draw distance (in no preset) | Trees, rocks, enemies and animals appear nearer. Far landmarks (Hyrule Castle, mountains) stay | `SAVAGEWIND_TRAVERSE_SCALE`, with `SAVAGEWIND_FAR_SCALE=1` | High: at 0.6 actor trees end at 90 m (the game: 150 m) while the distant trees still fade in at 100-200 m, so forests can show a gap; enemies and animals exist only within 90 m. Savage Wind marks these variables "tests" and only tried 1 and up. Gain over Low: 3-4 fps, give or take 6 (not proven). The forest gap is not checked yet: our Kakariko shots at 60 % and 80 % only caught a near view (a guard's talk held the camera) |
| Sun-shadow range (in no preset) | Meant to end sun shadows nearer | A RomFS mod built from your `Pack/Bootup_Graphics.pack` (recipe: `native/examples/perf_shadows`) | Unverified: no visible change, and `depth_shadow_far_0` may not be the range (the cascade splits are 2/32/64/200 m). It replaces the whole 20 MB pack |
| Ambient occlusion, bloom, far blur (Low) | The game's own switches for these effects, off, in the open world and shrines | A RomFS mod built from your `Pack/Bootup.pack` (recipe: `native/examples/perf_effects`) | A flatter image, no glow; and Savage Wind's HUD letters and voice size fix are skipped ([below](#the-built-mods)) |
| Remove Grass | No grass at all | Sen Uii's mod, which you add yourself (below) | No grass to cut, no grass fires, so no updrafts from burning grass |

Savage Wind's own settings (Video page) are levers too: see the results above for what each one did here.

- **Remove Grass** is not in any preset: in Hyrule Field, Low with it ran 67 fps and without it 69 (one run each, an
  earlier round: no gain seen over short grass), and it takes away grass fires and their updrafts. Turn it on
  yourself if you want no grass at all: `python tools/quality.py toggle no_grass on --deploy`.
- **Ambient occlusion off** changes the look most: at Ultra performance the game's ambient occlusion darkens the
  grass a lot, so Low looks lighter and flatter than the other presets.
- **Sun-shadow range:** the built mod loads and the game runs with it, but no change was visible in our screenshots,
  so it may not do much (Savage Wind may set the shadow range itself).

### The built mods

`perf_effects` and `perf_shadows` hold no game files: each is a `mod.toml` and a `recipe.json` (which values to
change, inside which file). `quality.py` reads your own pack, changes those values in place (every file inside keeps
its size, so the game's size table needs no change), and adds the result to your library as the mod
*Performance: effects off* or *Performance: shorter shadows*. It builds again only when the values or the pack it
starts from change.

When another mod in your library ships the same pack (`Pack/Bootup.pack`, say), the built mod starts from that mod's
copy and goes after it in the list, so both changes are kept. Set the preset again after you change that mod.

**The cost of the effects mod (Low only):** it ships `Pack/Bootup.pack`, and when a mod replaces that pack Savage
Wind 0.4.1-alpha.4 skips two of its own fixes, without a word in its log:

- its **HUD button letters** (the game's own letters show instead of the icon-font ones);
- its **voice size-table fix**. On this PC Savage Wind grows one voice bank's size entry (`[fs] voices: 1 files
  bigger than the game's own, 1 ResourceSizeTable entries grown` in every Original log, missing from every log with
  the effects mod). Without it that voice may fail to load: silent, or a crash in the scene that uses it. Which bank
  it is isn't in the log. With Low on, the king's voiced reveal on the Temple of Time roof played on into the
  prophecy film with no crash (one test; we can't check by test whether the voice was heard, and it may not be the
  bank Savage Wind grows).

So High and Medium replace no game file, `quality.py` prints this as a note whenever the effects mod is on, and the
mod menu gets it as `bootup_note`. The other built mod (shadows) ships `Pack/Bootup_Graphics.pack` and is in no
preset.

**"Files bigger than the game's" when you deploy:** the rebuilt packs are a few KB bigger than the game's (8 KB
for `Bootup.pack`, 5.5 KB for `Bootup_Graphics.pack`), because our Yaz0 compression is less tight than Nintendo's.
The files inside keep their sizes, and the size table (RSTB) has no entry for the packs themselves, so this warning
(it suggests merging with UKMM) is harmless here; the game loaded them in every test.

## Mods made by others

Only one existing mod could save GPU time: **Remove Grass** (one run on this laptop: no gain seen over short grass).
Nothing else was found for the Switch version (no "potato", simple-shader, no-shadow, no-bloom or LOD mod; checked
all Switch mods on GameBanana and the Switch patch archives on 2026-11-07). The 1.6.0 ExeFS patches there are "60 FPS"
and "disable dynamic resolution", which Savage Wind already does itself, so there was nothing to port to 1.9.0.

| Mod | Author | Licence | Use |
|---|---|---|---|
| [Remove Water & Grass](https://gamebanana.com/mods/328281) (Wii U), option *Remove Grass* | Sen Uii (contributor rick1234-) | CC BY-NC-ND 4.0: personal use; don't share changed copies | Optional (`toggle no_grass on`): no fps gain seen over short grass (one run). Repack it first: below |
| [No fog in Korok Forest](https://gamebanana.com/mods/418477) (Switch) | Echocolat (key author), submitted by HGStone | Personal use only | Not recommended: a gameplay change, not a performance one (the Koroks stop sending you back: `ForbidComeback` off), no fps measured, and this loader installs only part of it (its `map.yml` changes aren't merged) |
| [Depth of Field Mod](https://gamebanana.com/mods/553934) (Switch) | Logansam (from Silentverge's, original by Nograd) | CC BY-NC-ND 4.0 | No: it adds blur. It showed where the blur values live |
| [Wii U Console Performance Patch](https://gamebanana.com/mods/323044) | Exzap, Xalphenos, Torphedo | CC BY-NC-ND 4.0 | Not for Savage Wind (a Wii U code patch for the CPU/GPU wait, which Savage Wind does its own way) |
| [No Lost Woods Fog Walls](https://gamebanana.com/mods/329295) (Switch) | Lady Lunanova, Switch port HGStone | Personal use only | No: it only changes the DLC map, and it isn't a performance mod |

Thanks to all of them. Their files are not in this repo: download them yourself from the links.

### Adding Remove Grass

The download is a Wii U BCML package (`removewatergrass.bnp`) with three options; only *Remove Grass* is safe (the
water options break the game). It holds one file, `Terrain/A/MainField.tscb`, the terrain index. That file is the
same on Wii U and Switch (big-endian on both, same size, same 9,033 areas as 1.9.0's; only the grass lists differ),
so it works as it is, but modman refuses Wii U packages. So:

1. Open the `.bnp` with 7-Zip and take `options/Remove Grass/content/Terrain/A/MainField.tscb`.
2. Put it in a new folder as `romfs/Terrain/A/MainField.tscb`.
3. `python tools/modman.py add "<that folder>" --name "Remove Grass"`.

`quality.py` finds it by that file's SHA-256 (`9c17d2cb…`, the *Remove Grass* option's file), so another terrain
mod is never taken for it or turned off. No preset turns it on; `python tools/quality.py toggle no_grass on` does,
and setting a preset again turns it off.

## For the in-game mod menu

`tools/quality.py` can be imported:

- `presets()`: the levels, each preset's levers and Savage Wind settings, each lever's label, kind and range, and
  `bootup_note` (show it while ambient occlusion, bloom or far blur is off) (JSON-ready; `python tools/quality.py
  json` prints it).
- `set_quality(library, level, overrides=None, deploy=False)`: pick a preset (a folder, `None` for modman's default
  library, or a modman `Library`). Returns the levers, the start-up values, the Savage Wind settings, what changed and
  whether a deploy is needed.
- `toggle(library, lever, value)`, `status(library)`, `launch_env(library)` (the start-up values to set before
  starting the game), `run(library, port=None)`.

## What doesn't work (yet)

- **Lower shadow resolution:** not possible; Console (896) is already Savage Wind's lowest.
- **Lower render resolution than Ultra performance:** none left in the menu; `SAVAGEWIND_SCALE` also blurs the HUD.
- **Handheld mode:** Savage Wind always reports docked, and handheld would mainly lower the resolution, which the
  upscaler already does.
- **Texture detail (`SAVAGEWIND_LOD_MIN`):** no gain expected at these resolutions; not in the presets.
- **Draw distance under Original in Savage Wind's menu:** its Draw distance setting only goes further (Medium,
  High); nearer is the start-up values above.
- `modman run` and Savage Wind's own window don't set the start-up values: use `quality.py run`.

## What was tested

- **In the game** (2026-11-07, Savage Wind 0.4.1-alpha.4, test save, scratch mod library): High (no mod), grass
  40 %, Low and Low + objects 60 % at both places, 3 rounds taking turns (24 game starts, each set with
  `quality.py set` and started with `quality.py run`); the upscaler modes and frame generation changed while the game
  ran; Low for 5 minutes at each place and in the king's cutscene (no crash). Remove Grass removed the grass on the
  Plateau as expected. Medium (grass 60 %) was not measured on its own.
- **Made-up files only** (`tests/test_quality.py`): the presets, the start-up values, the in-place value changes
  (also values the file shares between switches), the built packs (only those values change, sizes stay), the
  library changes (built once, kept, rebuilt when needed, other mods left alone, built on another mod's pack), and
  `run` passing the start-up values on.
- Not tested: the presets on an idle laptop, the mod menu calling `set_quality` (not written yet), a laptop where the
  graphics chip is the limit.
