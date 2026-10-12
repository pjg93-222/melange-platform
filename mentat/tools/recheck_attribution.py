"""Reapply the shared employer matcher to legacy review holds, without DB writes."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
from attribution_policy import explicit_rejection, source_exclusion


PROJECT = Path(__file__).resolve().parents[1]
EXACT_REASONS = {'verified_employer_alias', 'verified_roster_alias', 'squashed_form_exact'}
INPUT_FIELDS = ('industry', 'company', 'contributor_employer', 'contributor_name',
                'contributor_state', 'contributor_occupation', 'excluded_reason')


def recheck_attribution(rows, database, directory, evidence=None):
    """Only exact approved roster identities can settle a heuristic hold.

    Raw observations and their old dispositions remain in source_records. A
    derived decision records its previous reason, the shared matcher's result,
    and the evidence artifact. Explicit adjudications/source exclusions, source conflicts,
    non-individual entities and prefix-only similarities never qualify.
    """
    groups = {}
    mismatches = 0
    evidence = evidence or {}
    for row in rows:
        if (str(row.get('company_match_status') or '').upper() == 'ACCEPTED'
                or explicit_rejection(row.get('company_match_reason'))
                or str(row.get('entity_type') or '').upper() != 'IND'
                or source_exclusion(row.get('excluded_reason')) or row.get('source_conflicts')):
            continue
        remote = evidence.get(str(row.get('sub_id')), {})
        # An archived response must not silently change the employer used to
        # release a historical hold. Formatting differences are immaterial;
        # competing employer/entity/state/occupation facts need review.
        fields = ('contributor_employer', 'contributor_state', 'contributor_occupation', 'entity_type')
        normalized = lambda value: re.sub('[^A-Z0-9]', '', str(value or '').upper())
        if any(row.get(field) not in (None, '') and remote.get(field) not in (None, '')
               and normalized(row[field]) != normalized(remote[field]) for field in fields):
            mismatches += 1
            continue
        effective = {**row, **{field: remote[field] for field in fields if remote.get(field) not in (None, '')}}
        effective['excluded_reason'] = source_exclusion(effective.get('excluded_reason'))
        key = tuple(str(effective.get(field) or '') for field in INPUT_FIELDS)
        groups.setdefault(key, []).append(row)
    receipt_count = sum(map(len, groups.values()))
    summary = {'eligible_records': receipt_count, 'unique_inputs': len(groups),
               'resolved_records': 0, 'conflicting_match_inputs': mismatches, 'status': 'not_needed'}
    if not groups:
        return summary
    executable = (os.environ.get('MENTAT_RSCRIPT') or shutil.which('Rscript')
                  or 'C:/Program Files/R/R-4.6.0/bin/Rscript.exe')
    if not Path(executable).is_file():
        return {**summary, 'status': 'deferred', 'reason': 'Shared R employer matcher runtime unavailable'}
    directory = Path(directory)
    requests = [dict(zip(INPUT_FIELDS, key), request_index=i) for i, key in enumerate(groups)]
    input_path, output_path = directory / 'attribution-inputs.json', directory / 'attribution-results.json'
    input_path.write_text(json.dumps(requests, ensure_ascii=True), encoding='utf-8')
    environment = os.environ.copy()
    library = PROJECT / '.cache/audit-r-library'
    if not environment.get('R_LIBS_USER') and all((library / name / 'DESCRIPTION').is_file()
                                                 for name in ('DBI', 'RSQLite', 'data.table', 'jsonlite')):
        environment['R_LIBS_USER'] = str(library)
    try:
        subprocess.run([executable, str(PROJECT / 'tools/recheck_attribution.R'),
                        '--database', str(database), '--input', str(input_path), '--output', str(output_path)],
                       check=True, capture_output=True, text=True, env=environment, timeout=300,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        return {**summary, 'status': 'deferred', 'reason': 'Shared employer matching exceeded its time budget'}
    except subprocess.CalledProcessError as error:
        if 'Required installed package is unavailable' not in (error.stderr or ''):
            raise
        return {**summary, 'status': 'deferred', 'reason': 'Shared R employer matcher dependencies unavailable'}
    output_bytes = output_path.read_bytes()
    output_sha = hashlib.sha256(output_bytes).hexdigest()
    results = json.loads(output_bytes.decode('utf-8-sig'))
    if not isinstance(results, list) or len(results) != len(requests):
        raise ValueError('Shared employer matcher returned an incomplete result')
    by_index = {}
    for result in results:
        index = result.get('request_index')
        if type(index) is not int or index < 0 or index >= len(requests) or index in by_index:
            raise ValueError('Shared employer matcher returned invalid or duplicate request identities')
        by_index[index] = result
    for index, originals in enumerate(groups.values()):
        result = by_index[index]
        if result.get('status') != 'accepted' or result.get('reason') not in EXACT_REASONS:
            continue
        for row in originals:
            row['attribution_resolution'] = {
                'previous_status': row['company_match_status'],
                'previous_reason': row.get('company_match_reason') or '',
                'status': 'accepted', 'reason': result['reason'],
                'evidence_artifact': output_path.name, 'evidence_sha256': output_sha, 'request_index': index,
                **({'alias_evidence': result['alias_evidence']} if result.get('alias_evidence') else {})}
            row['company_match_status'] = 'accepted'
            row['company_match_reason'] = 'automatic_exact_alias_recheck:' + result['reason']
            summary['resolved_records'] += 1
    return {**summary, 'status': 'completed', 'artifacts': {
        input_path.name: hashlib.sha256(input_path.read_bytes()).hexdigest(), output_path.name: output_sha}}
