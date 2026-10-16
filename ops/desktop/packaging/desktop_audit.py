"""Local write audit: no request paths, bodies, record IDs or personal data."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
from datetime import datetime, timezone


class DesktopAudit:
    def __init__(self, directory, app_id, district):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.app_id, self.district = app_id, district
        self.lock = threading.Lock()
        key_path = self.directory / 'audit-integrity.key'
        if not key_path.exists():
            with key_path.open('xb') as output:
                output.write(secrets.token_bytes(32))
            if os.name != 'nt':
                key_path.chmod(0o600)
        self.key = key_path.read_bytes()
        self.file = self.directory / 'desktop-write-audit.jsonl'
        self.last = '0' * 64
        if self.file.exists():
            with self.file.open(encoding='utf8') as source:
                for line in source:
                    record = json.loads(line)
                    digest = record.pop('integrity')
                    expected = self._digest(record)
                    if record.get('previous') != self.last or not hmac.compare_digest(digest, expected):
                        raise RuntimeError('Desktop write audit integrity check failed')
                    self.last = digest

    def _digest(self, record):
        return hmac.new(self.key, json.dumps(record, sort_keys=True, separators=(',', ':')).encode(), hashlib.sha256).hexdigest()

    def record(self, phase, method, request_id, status=None):
        with self.lock:
            event = {'utc': datetime.now(timezone.utc).isoformat(), 'appId': self.app_id,
                     'district': self.district, 'actor': 'desktop-owner', 'operation': 'http-write',
                     'phase': phase, 'method': method, 'requestId': request_id, 'status': status,
                     'previous': self.last}
            event['integrity'] = self._digest(event)
            descriptor = os.open(self.file, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(descriptor, 'a', encoding='utf8') as output:
                output.write(json.dumps(event, separators=(',', ':')) + '\n')
                output.flush()
                os.fsync(output.fileno())
            self.last = event['integrity']
