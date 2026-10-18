"""Identity evidence from individual payroll entries, never whole-career title spans."""
import gzip
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

CONFIG = Path(__file__).with_name('staffer_payroll_identity.json')
VERSION = 'payroll-identity-rows-v1'


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def exclusive(day):
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def employment_overlap(left, right):
    """An undated Senate payment window proves overlap only when fully covered.

    Two uncertain payment windows cannot prove contemporaneous employment.
    Exact House service intervals and directory date interiors use intersection.
    """
    if not all(ep.get('start') and ep.get('end_exclusive') and ep['start'] < ep['end_exclusive'] for ep in (left, right)):
        return None
    uncertain = [ep.get('date_evidence') == 'presence_within_report' for ep in (left, right)]
    if all(uncertain):
        return None
    if any(uncertain):
        window, certain = (left, right) if uncertain[0] else (right, left)
        if not (certain['start'] <= window['start'] and window['end_exclusive'] <= certain['end_exclusive']):
            return None
        return window['start'], window['end_exclusive']
    lo, hi = max(left['start'], right['start']), min(left['end_exclusive'], right['end_exclusive'])
    return (lo, hi) if lo < hi else None


def load_payroll_identity(root, config_path=None):
    """Read a pinned generated evidence bundle and verify its native inputs once."""
    root = Path(root)
    # This is an installation-presence check, never generation selection.
    # A newer spine must not enable the fixture fallback when its bundle is lost.
    installed = any(p.is_dir() for p in (root / 'audits/staffer_network').glob('hill_personnel_spine_*_primary'))
    config_path = Path(CONFIG if config_path is None else config_path)
    if not config_path.is_file():
        if installed:
            raise ValueError('Payroll identity configuration missing from installed spine: ' + str(config_path))
        return None
    config = json.loads(config_path.read_text(encoding='utf-8'))
    path = root / config['path']
    if not path.is_file():
        if not installed and not path.parent.is_dir():
            return None
        raise ValueError('Payroll identity bundle missing from the installed spine generation: ' + str(path))
    if sha_file(path) != config['sha256']:
        raise ValueError('Payroll identity bundle hash mismatch')
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        bundle = json.load(stream)
    if bundle.get('version') != VERSION:
        raise ValueError('Unsupported payroll identity evidence version')
    for binding in bundle['sources']:
        if sha_file(root / binding['path']) != binding['sha256']:
            raise ValueError('Payroll identity source hash mismatch: ' + binding['path'])
    proof = {'path': str(path), 'sha256': config['sha256'], 'method': VERSION}
    episodes = []
    for row in bundle['episodes']:
        person, employer, title, start, end, date_evidence, row_ids = row
        episodes.append({'person_id': person, 'employer_id': employer, 'title': title,
            'start': start, 'end_exclusive': end, 'employment_verified': True,
            'date_evidence': date_evidence, 'provenance': {**proof, 'source_row_ids': row_ids,
                'date_method': date_evidence, 'native_person_id': person}})
    return {'episodes': episodes, 'record_pairs': bundle['record_pairs'], 'sources': bundle['sources'],
            'proof': proof, 'counts': bundle['counts']}
