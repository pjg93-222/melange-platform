"""Stored path spellings for the container runtime (D075): record the host's Windows spelling, read through the container's mounts.

A collector in a container sees /frontier, /hoststate and /export; the receipts, the START record, the ledger's archive paths and the chain of accepted
batches name the Windows paths of the laptop (C:\\Users\\...\\committee-activity\\responses-<id>\\attempt-...). MENTAT_STORED_PATH_MAP says how the two
spellings meet, as `CONTAINER=STORED` pairs joined by `;`:

    /hoststate=C:\\Users\\pgoff\\Mentat\\mentat\\state\\committee-activity;/frontier=C:\\Users\\pgoff\\Mentat\\mentat\\state\\committee-activity;/export=C:\\...\\exports

to_stored(path): a container path -> the spelling to RECORD (longest container prefix on a path boundary wins; anything else is returned as str(path)).
to_host(path):   a recorded spelling -> the container path to READ it through. Several containers may share one stored root (the live ledger and the read-only
                 host state view both are <stored root>); a stored path is then read through the FIRST pair listed, so list /hoststate first.
Unset or empty map: both are the identity (a host-native run records exactly what it always did). Import has no I/O; the map is read at call time.
"""
import os
import re

VARIABLE = 'MENTAT_STORED_PATH_MAP'
_WINDOWS = re.compile(r'^(?:[A-Za-z]:[\\/]|\\\\)')


def parse(text):
    """'a=b;c=d' -> [('a','b'),('c','d')]; every item needs both sides, a container side not ending in a separator, and no duplicate container side."""
    pairs = []
    for item in str(text or '').split(';'):
        if not item.strip():
            continue
        container, separator, stored = item.partition('=')
        container, stored = container.strip(), stored.strip()
        if not separator or not container or not stored or container.rstrip('/\\') != container and len(container) > 1:
            raise ValueError('MENTAT_STORED_PATH_MAP items are CONTAINER=STORED pairs joined by ;')
        if any(container == known for known, _ in pairs):
            raise ValueError('MENTAT_STORED_PATH_MAP names a container prefix twice: ' + container)
        pairs.append((container, stored))
    return pairs


def pairs(environment=None):
    return parse((os.environ if environment is None else environment).get(VARIABLE, ''))


def _fold(text):
    return text.replace('\\', '/').rstrip('/')


def _separator(spelling):
    return '\\' if _WINDOWS.match(spelling) or '\\' in spelling else '/'


def to_stored(path, mapping=None):
    mapping = pairs() if mapping is None else mapping
    text = str(path)
    if not mapping:
        return text
    folded = _fold(text)
    best = None
    for container, stored in mapping:
        prefix = _fold(container)
        if (folded == prefix or folded.startswith(prefix + '/')) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, stored)
    if best is None:
        return text
    rest = folded[len(best[0]):]
    root = best[1].rstrip('/\\')
    return root + rest.replace('/', _separator(best[1])) if rest else root


def to_host(path, mapping=None):
    mapping = pairs() if mapping is None else mapping
    text = str(path)
    if not mapping or not _WINDOWS.match(text):
        return text
    folded = _fold(text).lower()
    best = None
    for container, stored in mapping:
        root = _fold(stored).lower()
        if (folded == root or folded.startswith(root + '/')) and (best is None or len(root) > len(best[0])):
            best = (root, container)       # strict '>' keeps the first pair listed on a tie
    if best is None:
        return text
    rest = _fold(text)[len(best[0]):]
    base = best[1].rstrip('/\\') or best[1]
    return base + rest.replace('/', _separator(best[1])) if rest else base


def same(left, right):
    """Two spellings of one stored path: separators and case folded (Windows), trailing separators ignored."""
    return _fold(str(left)).lower() == _fold(str(right)).lower()
