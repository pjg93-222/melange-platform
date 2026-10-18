"""Bound local source reads to private captured bytes before publication."""
from pathlib import Path
import hashlib
import os
import shutil
import tempfile

RESERVE_BYTES = 2 * 1024**3


def file_hash(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


class SourceCapture:
    """Private bounded-memory copies; source paths and hashes stay in a small ledger.

    Callers select every dependency explicitly. This is not directory copying or
    a transaction over multiple final output files.
    """
    def __init__(self, parent):
        self._temporary = tempfile.TemporaryDirectory(prefix='.source-capture-', dir=parent)
        self.root = Path(self._temporary.name)
        self.records = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._temporary.cleanup()

    def reserve(self, additional=0):
        if shutil.disk_usage(self.root).free < RESERVE_BYTES + additional:
            raise OSError('Source capture would cross the 2 GiB free-space reserve')

    def capture(self, source, relative, *, expected_bytes=None, expected_sha256=None, optional=False):
        source = Path(source)
        target = self.root / relative
        if target.resolve().is_relative_to(self.root.resolve()) is False:
            raise ValueError('Capture destination must remain inside its private directory')
        if str(source) in self.records:
            raise ValueError('Source already captured')
        if optional and not source.exists():
            self.records[str(source)] = {'path': str(source), 'present': False, 'capture': str(target)}
            return target
        with source.open('rb') as incoming:
            before = os.fstat(incoming.fileno())
            limit = before.st_size if expected_bytes is None else expected_bytes
            if type(limit) is not int or limit < 0 or before.st_size != limit:
                raise ValueError('Source capture size differs from its declaration')
            self.reserve(limit)
            target.parent.mkdir(parents=True, exist_ok=True)
            total = 0
            digest = hashlib.sha256()
            with target.open('xb') as captured:
                while True:
                    block = incoming.read(min(1024 * 1024, limit - total + 1))
                    if not block:
                        break
                    total += len(block)
                    if total > limit:
                        raise ValueError('Source grew beyond its declared capture size')
                    self.reserve(len(block))
                    captured.write(block)
                    digest.update(block)
                captured.flush()
                os.fsync(captured.fileno())
            after = os.fstat(incoming.fileno())
        value = digest.hexdigest()
        if total != limit or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('Source changed during capture')
        if expected_sha256 is not None and value != expected_sha256:
            raise ValueError('Source capture hash differs from its declaration')
        self.records[str(source)] = {'path': str(source), 'present': True, 'capture': str(target),
                                     'bytes': total, 'sha256': value, 'mtime_ns': before.st_mtime_ns}
        return target

    def verify_unchanged(self):
        """Refuse drift before publishing; receipts still bind the captured bytes."""
        for source, record in self.records.items():
            path = Path(source)
            if not record['present']:
                if path.exists():
                    raise ValueError('An absent optional source appeared during capture')
            elif not path.is_file() or path.stat().st_size != record['bytes'] or file_hash(path) != record['sha256']:
                raise ValueError('Source changed before publication')


def publish_new_files(pairs):
    """No-overwrite publication on one volume; preserve earlier files on refusal.

    A process/power failure between links can leave an incomplete pair. Consumers
    must continue refusing an absent or mismatched seal. No crash atomicity claim.
    """
    created = []
    try:
        for staged, final in pairs:
            staged, final = Path(staged), Path(final)
            os.link(staged, final)
            created.append((staged, final))
    except BaseException:
        for staged, final in reversed(created):
            # Cooperating publishers never replace existing paths. This identity check
            # avoids known replacement, but an external replace between samefile/unlink
            # remains a race; this is not a general concurrent-writer transaction.
            if final.exists() and os.path.samefile(staged, final):
                final.unlink()
        raise
