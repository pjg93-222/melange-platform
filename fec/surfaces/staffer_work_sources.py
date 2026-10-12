"""Source-preserving promotion of payroll intervals into workplace exposure."""
from collections import Counter, defaultdict
from datetime import date, timedelta

from fec.staffer_network.closeness_math import ExposureIndex, ordinal
from fec.surfaces.staffer_network_scope import intern_title


def normalize(row):
    proof = row.get('provenance', {})
    if proof.get('work_route_source') == 'politico_profile_history':
        source = 'directory'
    elif proof.get('source') == 'verified_static_trajectory':
        source = 'payroll_trajectory'
    elif proof.get('method') == 'payroll-identity-rows-v1':
        source = 'payroll_row'
    else:
        return None
    stint = proof.get('stint', {})
    result = {**row, 'work_source': source,
              'title': row.get('title') or stint.get('title_last', ''),
              'container_kind': row.get('container_kind') or stint.get('container_kind'),
              'chamber': row.get('chamber') or stint.get('chamber'),
              'role_scope': 'stint_summary_not_daily_assignment' if source == 'payroll_trajectory' else 'source_interval',
              'modeled_payroll_interval': row.get('date_precision') == 'REPORT_PERIOD'
                  or row.get('date_evidence') == 'presence_within_report'}
    return result


def uncovered(row, intervals):
    """Fallback trajectories fill gaps, rather than overriding detailed roles."""
    runs = [(row['start'], row['end_exclusive'])]
    for a,b in sorted(intervals):
        next_runs = []
        for start,end in runs:
            if b <= start or a >= end:
                next_runs.append((start,end))
            else:
                if start < a:
                    next_runs.append((start,a))
                if b < end:
                    next_runs.append((b,end))
        runs = next_runs
    return [{**row, 'start':a, 'end_exclusive':b} for a,b in runs]


def payroll_people(index):
    """The people with at least one payroll-sourced episode in the index's source evidence.

    Fixed by the index: it is computed when build_index finishes and kept on the index (index.payroll_people), so a
    route request reads it instead of scanning every person's episodes.
    """
    return frozenset(p for p, rows in index.source_episodes.items() if any(r['work_source'].startswith('payroll') for r in rows))


def build_index(router, options):
    rows, census = [], []
    metadata = dict(router.nodes)
    detailed = defaultdict(list)
    cutoff = (date.fromisoformat(router.as_of) + timedelta(days=1)).isoformat()
    for person, episodes in router.by_person.items():
        for original in episodes:
            if not router._allowed(original, options) or original.get('user_asserted') or original.get('continuous_employment_asserted') is False:
                continue
            if original['start'] >= cutoff:
                continue
            bounded = original
            if original['end_exclusive'] > cutoff:
                bounded = {**original, 'source_end_exclusive': original['end_exclusive'], 'end_exclusive': cutoff}
            row = normalize(bounded)
            census.append(row or bounded)
            if row is None:
                continue
            c = row['employer_id']
            metadata[c] = {**{k:v for k,v in row.items() if k in ('container_kind','chamber') and v},
                           **{k:v for k,v in metadata.get(c,{}).items() if v is not None}}
            if options['work_sources'] == 'directory' and row['work_source'] != 'directory':
                continue
            if not options['include_historical'] and not (row['start'] <= router.as_of < row['end_exclusive']):
                continue
            rows.append(row)
            if row['work_source'] != 'payroll_trajectory':
                detailed[person,c].append((row['start'],row['end_exclusive']))
    work = []
    for row in rows:
        work.extend(uncovered(row, detailed[row['person_id'],row['employer_id']])
                    if row['work_source'] == 'payroll_trajectory' else [row])
    index = ExposureIndex(work, metadata, census_episodes=census)
    index.source_episodes = defaultdict(list)
    for row in work:
        segments = index.cells.get(row['person_id'], {}).get(row['employer_id'], [])
        if not intern_title(row['title']) and any(ordinal(row['start']) < e and ordinal(row['end_exclusive']) > s for s,e,a in segments):
            index.source_episodes[row['person_id']].append(row)
    counts = Counter(r['work_source'] for rows in index.source_episodes.values() for r in rows)
    index.source_coverage = {'mode':options['work_sources'], 'people':len(index.cells),
        'workplaces':len(index.people_by_workplace), 'episodes_by_source':dict(counts),
        'modeled_payroll_intervals':sum(r['modeled_payroll_interval'] for rows in index.source_episodes.values() for r in rows),
        'semantics':'Payroll and directory evidence retain their source identities; report windows are modeled exposure, not observed daily overlap. Trajectory title is a stint summary.'}
    index.payroll_people = payroll_people(index)
    return index
