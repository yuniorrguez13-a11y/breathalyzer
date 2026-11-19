"""Model targets: a neutral Scene (tools/model_in) in, a BotW mod out (converter stage 1, see docs/model-import.md).

Each target is a module here with its own command line. They hold the BotW knowledge (donor actor, size, stats,
texts) and reuse the builders that already work (ak47_build, dragonbreath_build) and the BFRES/BNTX writers.
Every target builds on the player's PC from the player's own game files; nothing here ships game files.

- weapon.py: a sword-like rigid model -> a one-handed weapon mod.
"""
