# zelda_view (a hidden easter egg)

What it does, how to install it and what was checked: [docs/easter-eggs.md](../../../docs/easter-eggs.md).

- `zelda_view.c`: the mod (one file). Four hooks (`hooks.txt`, also in `mod.toml`), so `modman deploy` rebuilds the
  game code once (10-15 minutes, cached after).
- `selftest/zv_selftest.c`: checks the mod's logic against a made-up game, no game needed
  (`python -m unittest tests.test_zelda_view`).
- Zelda's actor pack is built on your PC from your own game files by `python tools/zelda_view.py install`. It is
  never in this repo.
