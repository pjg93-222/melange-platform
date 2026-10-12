"""Read-only freshness checks for the inputs selected by compute_pillars."""
from datetime import datetime
import hashlib
import json
from pathlib import Path


LEGACY_CHECK = 'pillar snapshot vintage <= 14 days (stale-basis tripwire)'
LIVE_CHECK = 'live pillar inputs current (source hashes + authority parity)'
LIVE_INPUTS = (
    'legislation_routing_audit.json',
    'bill_repository_live.json',
    'archive_leg.json',
    'data/engine/sentinel_admission_policy.json',
)


def check_pillar_freshness(root, *, authority_parity, now=None):
    """Return (check name, passed, detail), preserving the legacy vintage rule.

    Live mode consumes current evidence and typed workbook inputs, not O/E/F
    snapshot caches. Hashes bind the shipped authority to its evidence; the
    existing frozen shadow recompute covers workbook inputs and score/rank drift.
    Neither mode refreshes or stamps any artifact during validation.
    """
    root = Path(root)
    check = 'pillar freshness input selection'
    try:
        policy_path = root / 'data/engine/sentinel_admission_policy.json'
        policy = json.loads(policy_path.read_text(encoding='utf-8')) if policy_path.is_file() else {}
        # Match compute_pillars exactly: truthy strings/numbers do not enable live mode.
        if policy.get('live_legislative_pillar') is True:
            check = LIVE_CHECK
            authority = json.loads((root / 'ranks_python.json').read_text(encoding='utf-8'))
            pillar = authority.get('legislative_pillar') or {}
            if pillar.get('schema') != 'live-legislative-pillar-v1':
                return check, False, 'rank authority lacks live legislative pillar provenance'
            hashes = pillar.get('inputs_sha256') or {}
            mismatches = [path for path in LIVE_INPUTS
                          if hashes.get(path) != hashlib.sha256((root / path).read_bytes()).hexdigest()]
            problems = ['changed or unrecorded input: ' + path for path in mismatches]
            if not authority_parity:
                problems.append('rank authority does not match current recomputed inputs')
            return check, not problems, '; '.join(problems) or 'live sources and recomputed authority agree'

        check = LEGACY_CHECK
        snapshot_path = root / 'data/engine/pillar_inputs_snapshot.json'
        snapshot = json.loads(snapshot_path.read_text(encoding='utf-8')) if snapshot_path.exists() else {}
        stamp = snapshot.get('stamped')
        if not stamp:
            return check, True, 'unstamped pre-guard snapshot (stamps on next compute run)'
        age = ((now or datetime.now().astimezone()) - datetime.fromisoformat(stamp)).days
        return check, age <= 14, 'stamped %s (%dd)' % (stamp, age)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return check, False, '%s: %s' % (type(exc).__name__, exc)
