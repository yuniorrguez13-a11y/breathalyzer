"""Errors of the model converter. `Unsupported`: a variant we do not convert (never guessed). `Damaged`: bad data."""


class ModelError(Exception):
    """Base class: the file cannot be converted."""


class Unsupported(ModelError):
    """The file uses something the converter does not handle (format, tile mode, section...)."""


class Damaged(ModelError):
    """The file is cut short or inconsistent."""
