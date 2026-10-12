"""Where a location recorded in a receipt or manifest lives on this machine.

Receipts and accepted-source pointers are written on Windows, as 'C:/Users/x/Mentat/mentat/.cache/y/mentat.db'
or with backslashes (a drive, then Users, x, Mentat, mentat, state, ...). Anywhere else that text is a single
relative file name, so pathlib cannot find the file, its parent or its place under the project.
"""
import os
from pathlib import Path, PureWindowsPath

PROJECT = Path(__file__).resolve().parents[1]


def receipt_path(value):
    """The Path a recorded location names here. A drive path is read as a PureWindowsPath and rebased onto
    this checkout: what follows its 'Mentat' folder is looked up under PROJECT.parent, and a following
    'mentat' folder is PROJECT itself, so 'Mentat/mentat/.cache/y' lands in PROJECT/.cache/y whatever
    this checkout is called. On Windows, and for POSIX, relative or drive-less text, the value comes back
    as the Path it always was."""
    text = str(value)
    windows = PureWindowsPath(text)
    if os.name == 'nt' or not windows.drive:
        return Path(text)
    parts = windows.parts[1:]
    for index, part in enumerate(parts):
        if part.lower() == 'mentat':
            rest = parts[index + 1:]
            if rest and rest[0].lower() == 'mentat':
                return PROJECT.joinpath(*rest[1:])
            return PROJECT.parent.joinpath(*rest)
    return Path(text)
