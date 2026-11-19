"""Model readers: every format in, one neutral Scene out (see scene.py for the contract).

Groundwork only: nothing in the launcher, modman, charsel or the game uses this yet.

Each reader is a module here named after its format with ``read(path, **opts) -> Scene``. ``load(path)`` picks
one by the file's extension: .gltf/.glb/.vrm go to gltf.py; any other ``.ext`` goes to ``model_in/<ext>.py`` if
that module exists (so a new reader needs no change here).
"""

from __future__ import annotations

import importlib
from pathlib import Path

from .scene import ReadError, Scene, check_finite  # noqa: F401  (re-exported)

READERS = {".gltf": "gltf", ".glb": "gltf", ".vrm": "gltf"}


def load(path, **opts) -> Scene:
    ext = Path(path).suffix.lower()
    name = READERS.get(ext) or ext.lstrip(".")
    if not ext:
        raise ReadError(f"{Path(path).name} has no file extension, so Breathalyzer can't tell what kind of model it is.")
    if not name.isidentifier() or name.startswith("_") or name in ("scene",):
        raise ReadError(f"Breathalyzer can't read {ext} models yet.")
    try:
        mod = importlib.import_module(f"{__name__}.{name}")
    except ModuleNotFoundError as e:
        if e.name != f"{__name__}.{name}":
            raise
        raise ReadError(f"Breathalyzer can't read {ext} models yet.") from None
    if not callable(getattr(mod, "read", None)):
        raise ReadError(f"Breathalyzer can't read {ext} models yet.")
    p = Path(path)
    if not p.is_file():
        raise ReadError(f"Can't open the model file {p.name} ({'it is a folder' if p.is_dir() else 'it is not there'}).")
    try:
        scene = mod.read(path, **opts)
    except ReadError:
        raise
    except OSError as e:
        raise ReadError(f"Can't open the model file {p.name} ({(e.strerror or e.__class__.__name__).lower()}).") from None
    except RecursionError:
        raise ReadError(f"The model file {p.name} is nested too deeply to read; it is probably broken.") from None
    except MemoryError:
        raise ReadError(f"The model file {p.name} is too big to read on this PC.") from None
    except Exception as e:  # a reader bug or a very unusual file: still one sentence (the cause stays chained)
        raise ReadError(f"Breathalyzer couldn't read {p.name} ({e.__class__.__name__}); the file may be unusual "
                        "or broken.") from e
    if not isinstance(scene, Scene):
        raise ReadError(f"The {ext} reader gave nothing back for {p.name}.")
    return check_finite(scene)
