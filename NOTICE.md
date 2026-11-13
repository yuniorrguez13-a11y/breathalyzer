# Notice

Breathalyzer, by giruka.

- *The Legend of Zelda: Breath of the Wild* is © Nintendo. *Nintendo Switch*, *Wii U* and *The Legend of Zelda*
  are trademarks of Nintendo. Breathalyzer is an unofficial fan project, not affiliated with, endorsed by or
  sponsored by Nintendo, or by the Savage Wind Recomp project.
- Breathalyzer's repository and its releases contain **no** game files, game code, assets, keys, firmware or save
  files. They also contain none of the C code Savage Wind generates from the game, and no files converted from the
  game.
- To use it you need your own copy of the game, dumped from your own console. Breathalyzer converts and merges mods
  on your PC, from your files. Nothing it makes from your game files may be shared.
- We don't explain how to dump, decrypt or obtain the game, and we don't link to anywhere that does.
- Issues, pull requests or messages that contain game files, keys, generated code or links to pirated copies are
  deleted.
- Mods made by others keep their authors' rights. We don't bundle other people's mods or data taken from them
  without written permission.
- Reference projects (zeldaret/botw, Savage Wind Recomp, UKMM, BCML) keep their own licenses. zeldaret/botw has
  no license file, so we link to it and don't copy from it.
- The Mod menu (`native/examples/modmenu`, docs/mod-menu.md) follows Savage Wind Recomp 0.4.1-alpha.4's
  `runtime/ui/game_options.mm`: the option entry and row layouts, how a text is handed to the game, the reopen logic
  of the title and pause menus, and `rt_opt_pointer_kind`'s logic. Savage Wind Recomp is MIT licensed: Copyright (c)
  2026 Revivedx and the Savage Wind Recomp contributors; Copyright (c) 2026 Peiikai. Its full licence text is in
  THIRD-PARTY-NOTICES.md.
- Mods page v2 (being built): the easy-to-read font and the symbols the menu font lacks use Fira Sans (SIL Open
  Font License 1.1, Copyright (c) 2012-2015 The Mozilla Foundation and Telefonica S.A.), taken from your Savage Wind
  folder when it ships `FiraSans-*.ttf`, else from `assets/fonts/` with its license file. Ports of Savage Wind Recomp
  code for the Mods page (the HUD-letter layout patch, the grey text tag) are credited here under the MIT license
  above when they land. A font a mod brings keeps its own license; fonts built from it stay on your PC.
- The Wii U texture tiling in `tools/wiiu_model/gx2.py` is our own code that follows the surface math of AMD's
  addrlib as adapted for R600/R700 by decaf-emu/addrlib (MIT licensed: Copyright (c) 2014 Advanced Micro Devices,
  Inc.) and the Wii U constants and per-level rules of wiiu_swizzle (MIT licensed: Copyright (c) 2024 SMG). The Wii U
  file layouts and enum values in `tools/wiiu_model/` (models, materials, animations), the BotW render-info strings and
  the GX2 sampler bit layout come from BfresLibrary (MIT licensed: Copyright (c) 2017-2018 Syroot) and wut (zlib
  licensed); the GX2 register bit positions in `tools/wiiu_model/materials.py` follow Mesa's `r600d.h` (MIT
  licensed). No code was copied from them.
- A CC0 model by Plewr (itch.io), with our own wood texture, is used by a secret extra (see docs/easter-eggs.md); its
  full credits are in the MODEL-CREDITS.txt next to it in `assets/`. What is built from it on your PC also holds data
  from your own game files and stays on your PC.
