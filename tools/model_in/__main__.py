"""python -m model_in MODEL [...]  (run from tools/): read each model and print its counts, notes and problems.

Prints counts only from the Scene (no names, no licence text), then the reader's notes and validate() problems.
Exit code 0 = every model read and checked clean, 1 = a problem or a model that can't be read.
"""

from __future__ import annotations

import json
import sys

from . import load
from .scene import ReadError


def main(argv=None) -> int:
    paths = list(sys.argv[1:] if argv is None else argv)
    if not paths:
        print(__doc__.strip())
        return 1
    bad = 0
    for p in paths:
        try:
            scene = load(p)
        except (ReadError, OSError) as e:
            print(f"{p}: can't read it: {e}")
            bad += 1
            continue
        problems = scene.validate()
        print(f"{p}: {json.dumps(scene.summary())}")
        for n in scene.meta.get("notes") or []:
            print(f"  note: {n}")
        for pr in problems[:20]:
            print(f"  problem: {pr}")
        bad += bool(problems)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
