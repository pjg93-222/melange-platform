"""Advance the money snapshot pin only from the previously protected bytes."""
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from moneyball_core import dump_atomic


def prepare_update(root, before_sha256):
    path = Path(root) / 'data/system/system_manifest.json'
    if not path.exists():
        return None
    original = path.read_bytes()
    manifest = json.loads(original)
    stores = [s for s in manifest['stores'] if s['id'] == 'repo.congestion']
    if not stores or stores[0].get('integrity_policy') != 'hash_always':
        return None
    store = stores[0]
    if store.get('hashes') != {'congestion.json': before_sha256}:
        raise ValueError('Congestion changed outside the protected money refresh; refusing to advance its pin')
    return path, original, manifest, store, before_sha256


def finish_update(prepared, after_sha256):
    if prepared is None or after_sha256 == prepared[4]:
        return
    path, original, manifest, store, before = prepared
    if path.read_bytes() != original:
        raise ValueError('System manifest changed during money application; pin retained')
    store['hashes']['congestion.json'] = after_sha256
    manifest['money_pin_advance'] = {
        'at': datetime.now(timezone.utc).isoformat(),
        'before_sha256': before, 'after_sha256': after_sha256,
        'previous_manifest_sha256': hashlib.sha256(original).hexdigest(),
        'producer': 'fec/engine/apply_fec_money_refresh.py',
        'scope': 'Verified FEC money fields only; all other roster fields preserved.'}
    dump_atomic(manifest, str(path))
