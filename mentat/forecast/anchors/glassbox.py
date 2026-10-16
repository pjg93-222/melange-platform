"""Glass Box event emission for the anchor builder (Python port of refit/R/rf_glassbox.R: same event shape, schema_version 1).

Every process emits: run start, each stage start / finish, publish or refusal, failure. File name
evt-<UTC yyyyMMddTHHmmss + 7-digit fraction>Z-<10-digit pid>.json, written atomically (temporary file + rename), UTF-8 without BOM,
compact JSON. The folder is ANCHORS_GLASSBOX_DIR (default C:\\Users\\pgoff\\.claude\\glassbox\\events); tests point it at a temp folder.
An event that cannot be written is a warning on stderr, never a failure of the build.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import uuid

LANE = 'claude'
SOURCE = 'giving-forecast-anchors'


def events_dir():
    return Path(os.environ.get('ANCHORS_GLASSBOX_DIR') or (Path.home() / '.claude' / 'glassbox' / 'events'))


def _stamp(t):
    whole = int(t)
    micro = min(999999, max(0, round((t - whole) * 1e6)))
    base = datetime.fromtimestamp(whole, timezone.utc)
    frac7 = '%07d' % (micro * 10)
    return base.strftime('%Y%m%dT%H%M%S') + frac7 + 'Z', base.strftime('%Y-%m-%dT%H:%M:%S') + '.' + frac7 + 'Z', whole + micro / 1e6


def emit(kind, status, stage, summary, run_id, correlation_id='', parent_id='', detail='', duration_ms=-1, folder=None, metadata=None):
    """Write one event; returns its id, or None (with a warning on stderr) when it could not be written."""
    folder = Path(folder or events_dir())
    try:
        folder.mkdir(parents=True, exist_ok=True)
        t = time.time()
        while True:
            compact, iso, secs = _stamp(t)
            event_id = 'evt-' + compact + '-' + '%010d' % os.getpid()
            final = folder / (event_id + '.json')
            if not final.exists():
                break
            t = secs + 1e-6
        event = {'schema_version': 1, 'id': event_id, 'timestamp_utc': iso, 'run_id': run_id, 'correlation_id': correlation_id or event_id,
                 'parent_id': parent_id, 'lane': LANE, 'source': SOURCE, 'kind': kind, 'status': status, 'stage': stage,
                 'summary': summary[:320], 'detail': detail[:2400], 'cwd': os.getcwd(), 'duration_ms': int(duration_ms), 'bytes': -1,
                 'metadata': metadata}
        temp = folder / (event_id + '.json.' + uuid.uuid4().hex + '.tmp')
        temp.write_bytes(json.dumps(event, separators=(',', ':'), ensure_ascii=False).encode('utf-8'))
        os.replace(temp, final)
        return event_id
    except OSError as error:
        print('WARNING: Glass Box event not written (' + str(error) + ')', file=sys.stderr)
        return None


class Run:
    """Run events plus optional flushed stdout progress, independent of the event directory."""

    def __init__(self, run_id, folder=None, progress=False):
        self.run_id, self.folder, self.root, self.ids, self._t = run_id, folder, '', [], {}
        self.progress, self._active = progress, []

    def _emit(self, kind, status, stage, summary, detail='', duration_ms=-1, metadata=None):
        if self.progress:
            # Emit before filesystem events: an unavailable event directory must not hide the live stage.
            print(json.dumps({'source': SOURCE, 'timestamp_utc': datetime.now(timezone.utc).isoformat(),
                              'run_id': self.run_id, 'kind': kind, 'status': status, 'stage': stage,
                              'summary': summary[:320], 'duration_ms': duration_ms,
                              'elapsed_seconds': round(self._dur('run') / 1000, 3)}, ensure_ascii=False), flush=True)
        event_id = emit(kind, status, stage, summary, self.run_id, self.root, self.root, detail, duration_ms, self.folder, metadata)
        if event_id and not self.root:
            self.root = event_id
        if event_id:
            self.ids.append(event_id)
        return event_id

    def _dur(self, key):
        return int(round((time.monotonic() - self._t[key]) * 1000)) if key in self._t else -1

    def start(self, summary, detail=''):
        self._t['run'] = time.monotonic()
        return self._emit('run.start', 'running', 'start', summary, detail)

    def stage_start(self, stage, summary=None):
        self._t[stage] = time.monotonic()
        self._active.append(stage)
        return self._emit('stage.start', 'running', stage, summary or stage)

    def stage_finish(self, stage, summary=None, detail='', status='succeeded'):
        if stage in self._active:
            self._active.remove(stage)
        return self._emit('stage.finish', status, stage, summary or stage, detail, self._dur(stage))

    def finish(self, summary, detail='', status='succeeded'):
        return self._emit('run.finish', status, 'finish', summary, detail, self._dur('run'))

    def fail(self, summary, detail=''):
        for stage in list(reversed(self._active)):
            self.stage_finish(stage, summary, detail, status='failed')
        return self._emit('run.finish', 'failed', 'failure', summary, detail, self._dur('run'))
