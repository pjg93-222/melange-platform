"""Source-backed person facts and ordered social-network overlap features.

Source identities, source dates and uncertain dates remain explicit. This is a
display/routing overlay; it never alters the sealed social-network artifacts.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
import calendar
import re
from fec.surfaces.staffer_network_scope import intern_record

try:
    from .staffer_identity_auto import normalized, name_parts
except ImportError:  # direct script execution: no package context
    import os as _bootstrap_os, sys as _bootstrap_sys
    _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
    if _bootstrap_root not in _bootstrap_sys.path:
        _bootstrap_sys.path.insert(0, _bootstrap_root)
    from fec.surfaces.staffer_identity_auto import normalized, name_parts

DIMENSIONS = (
    ('current_employment', 'Current employment', 128),
    ('past_employment', 'Past employment', 64),
    ('committee', 'Committee', 32),
    ('member', 'Member', 16),
    ('subject', 'Subject matter', 8),
    ('party', 'Office party', 4),
    ('education', 'Education', 2),
    ('major', 'Major', 1),
)
VERSION = 'ordered-person-overlap-v4-unified-people'


def title_key(value):
    return re.sub(r'\badviser\b', 'advisor', normalized(value))


def date_bounds(text):
    """Earliest/latest possible day for a source date; never invent precision."""
    text = str(text).strip()
    try:
        value = date.fromisoformat(text)
        return value, value
    except ValueError:
        pass
    if re.fullmatch(r'\d{4}', text):
        year = int(text)
        if 1 <= year <= 9999:
            return date(year, 1, 1), date(year, 12, 31)
    match = re.fullmatch(r'([A-Za-z]+)\.?\s+(\d{4})', text)
    if match:
        months = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
        month, year = months.get(match[1][:3].lower()), int(match[2])
        if month and 1 <= year <= 9999:
            return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    return None


def employment_dates(text, observed):
    """Use only the guaranteed interior of coarse work dates for identity proof."""
    parts = re.split(r'\s+(?:-|–|—|to)\s+', str(text).strip(' ()'), maxsplit=1)
    if len(parts) != 2:
        return {}
    start = date_bounds(parts[0])
    current = parts[1].strip().casefold() in ('present', 'current')
    end = date_bounds(str(observed)[:10] if current else parts[1])
    if not start or not end:
        return {'listed_current': current}
    # Source end dates are inclusive. Starting at the last possible start day
    # and stopping just after the earliest end day avoids false date overlap.
    stop = end[0] + timedelta(days=1)
    return {'listed_current': current, 'start': start[1].isoformat(),
            'end_exclusive': stop.isoformat(), 'start_earliest': start[0].isoformat(),
            'end_latest': end[1].isoformat(), 'date_precision': 'source_date_bounds'}


def clean_office(value):
    value = re.sub(r'(Democratic|Republican|Independent)(?:[A-Z]{2}.*)?$', '', str(value)).strip()
    value = re.sub(r'^\d{4}\s+', '', value)
    return value


def office_key(value, chamber=None):
    text = normalized(clean_office(value))
    if 'committee' in text or 'subcommittee' in text or 'intern compensation' in text:
        return None
    if text.startswith(('sen ', 'senator ')):
        chamber = 'senate'
    elif text.startswith(('rep ', 'representative ', 'hon ')):
        chamber = chamber or 'house'
    text = re.sub(r'^(senator|sen|representative|rep|hon)\s+', '', text)
    if chamber not in ('house', 'senate'):
        return None
    parts = name_parts({'label': text})
    return (chamber, parts[0], parts[1]) if parts else None


class OfficeResolver:
    """Full first/last + chamber, only when it identifies one source office."""
    def __init__(self, nodes):
        self.nodes = nodes
        self.candidates = defaultdict(set)
        self.by_member = defaultdict(set)
        for ident, node in nodes.items():
            if node.get('container_kind') != 'member_office':
                continue
            key = office_key(node.get('label', ''), node.get('chamber'))
            if key:
                self.candidates[key].add(ident)
            if node.get('member_bioguide'):
                self.by_member[node['member_bioguide']].add(ident)

    def resolve(self, value):
        key = office_key(value)
        candidates = self.candidates.get(key, set())
        return next(iter(candidates)) if len(candidates) == 1 else None

    def employer(self, label, ident=None):
        return ident or self.resolve(label) or 'employer:' + normalized(clean_office(label))

    def office(self, label):
        resolved = self.resolve(label)
        if resolved:
            return resolved
        if re.match(r'^(Rep\.|Sen\.|Representative\b|Senator\b)', label) or re.match(r'^(House|Senate)\b.*\bCommittee\b', label):
            return 'office:profile:' + normalized(clean_office(label))
        return None

    def member(self, employer):
        label = clean_office(employer).split(' for ')[-1]
        ident = self.resolve(label)
        if ident:
            return self.nodes[ident].get('member_bioguide') or ident
        key = office_key(label)
        return 'member-name:' + ':'.join(key) if key else None


def identity_profile_episodes(nodes):
    resolver = OfficeResolver(nodes)
    episodes = []
    for ident, node in nodes.items():
        for profile in node.get('profile_records', []):
            for entry in profile.get('employment_history', []):
                employer = resolver.resolve(entry.get('employer', ''))
                dates = employment_dates(entry.get('date_text'), profile.get('observed_at'))
                proof = profile.get('provenance', {})
                if employer and dates.get('start') and dates['start'] < dates['end_exclusive']:
                    episodes.append({'person_id': ident, 'employer_id': employer,
                        'title': title_key(entry.get('title')), **dates,
                        'employment_verified': True,
                        'provenance': {**proof, 'work_route_source': 'politico_profile_history', 'assertion': entry,
                            'office_match': 'unique first/last name and chamber',
                            'date_method': 'guaranteed interior of source date bounds'}})
    return episodes


def majors(degree):
    """Explicit study subjects only. A degree level or honors is not a major."""
    result = []
    for degree_part in str(degree).split(';'):
        parts = degree_part.split(',')
        if len(parts) < 2:
            continue
        for part in parts[1:]:
            if re.search(r'\b(honors?|laude|distinction|minor|concentration)\b', part, re.I):
                continue
            # Preserve compound fields (e.g. international and regional security).
            value = normalized(part)
            if value:
                result.append(value)
    return result


def latest_roles(current, stints, episodes):
    """One role contract, selected by evidence date rather than source format.

    A newer observation wins; employment status retains the source's actual
    scope. A reported firm association is never relabeled as employment.
    """
    observations = []
    for role in current:
        if role.get('basis') == 'Your current coworker statement':
            continue  # A relationship assertion is not a job title.
        observations.append({**role, 'evidence_date': role.get('observed_at') or '',
            'status': 'reported_current', 'status_label': 'Current as reported',
            'sources': [role.get('provenance', {})]})
    for stint in stints:
        end = stint.get('core_end_exclusive')
        try:
            observed = (date.fromisoformat(end) - timedelta(days=1)).isoformat()
        except (TypeError, ValueError):
            continue
        active = stint.get('status') == 'current'
        observations.append({**stint, 'evidence_date': observed,
            'status': 'last_recorded' if active else 'past',
            'status_label': 'Current status unconfirmed' if active else 'Past role',
            'sources': [stint.get('provenance', {})]})
    for episode in episodes:
        if episode.get('continuous_employment_asserted') is not False:
            continue
        observed = str(episode.get('as_of') or episode.get('provenance', {}).get('last_filing_posted_at') or '')[:10]
        if observed:
            observations.append({'title': 'Reported work association',
                'employer': episode.get('employer_label') or episode.get('employer_id'),
                'employer_id': episode.get('employer_id'), 'evidence_date': observed,
                'status': 'association', 'status_label': 'Job title and current employment unconfirmed',
                'sources': [episode.get('provenance', {})]})
    newest = max((r['evidence_date'] for r in observations), default='')
    selected = {}
    for role in observations:
        if role['evidence_date'] != newest:
            continue
        key = (role.get('employer_id') or normalized(role.get('employer')), title_key(role.get('title')))
        if key in selected:
            previous = selected[key]
            sources = previous['sources'] + [p for p in role['sources'] if p not in previous['sources']]
            # Same title, employer and observation date: keep the stronger
            # explicit current statement while retaining both sources.
            if role['status'] == 'reported_current':
                selected[key] = role
            selected[key]['sources'] = sources
        else:
            selected[key] = role
    return list(selected.values())


class PersonFacts:
    def __init__(self, nodes, episodes=(), as_of=None):
        self.nodes, self.as_of = nodes, as_of or date.today().isoformat()
        self.resolver = OfficeResolver(nodes)
        self.people, self.features = {}, {}
        latest = defaultdict(str)
        for node in nodes.values():
            for stint in node.get('stints', []):
                office = stint.get('container_id')
                latest[office] = max(latest[office], stint.get('core_end_exclusive') or '')
        episodes_by_person = defaultdict(list)
        for episode in episodes:
            episodes_by_person[episode.get('person_id')].append(episode)
        for ident, node in nodes.items():
            if node.get('kind') != 'person':
                continue
            self._build(ident, node, latest, episodes_by_person[ident])
        self.network_excluded = {ident for ident, person in self.people.items()
                                 if intern_record(nodes[ident], person)}
        self.coverage = {'version': VERSION, 'people_with_facts': len(self.people),
            'interns_excluded_from_network': len(self.network_excluded),
            'employment_status': dict(Counter(p['employment_status'] for p in self.people.values())),
            'current_staff': sum(p['is_current_staff'] for p in self.people.values()),
            'dimensions': {key: sum(bool(f[key]) for f in self.features.values()) for key, _, _ in DIMENSIONS}}

    def _build(self, ident, node, latest, episodes):
        features = {key: {} for key, _, _ in DIMENSIONS}
        roles, historical, payroll, work_records = [], [], [], []
        profiles = node.get('profile_records', [])
        def add(dimension, key, label, proof=None):
            if not key:
                return
            item = features[dimension].setdefault(str(key), {'key': str(key), 'label': label, 'sources': []})
            if proof and proof not in item['sources']:
                item['sources'].append(proof)
        def affiliations(employer, proof):
            member = self.resolver.member(employer)
            if member:
                add('member', member, clean_office(employer).split(' for ')[-1], proof)
            if 'committee' in employer.casefold():
                committee = clean_office(employer).split(' for ')[0]
                add('committee', normalized(committee), committee, proof)
        # Use the latest observation for current roles. Keep every older history.
        latest_profile = max((str(p.get('observed_at', '')) for p in profiles), default='')
        for profile in profiles:
            proof = profile.get('provenance', {})
            observed = str(profile.get('observed_at') or '')[:10]
            is_latest = str(profile.get('observed_at', '')) == latest_profile
            for position in profile.get('current_header_positions', []) if is_latest else []:
                if ' for ' not in position:
                    continue
                title, employer = position.split(' for ', 1)
                employer = clean_office(employer)
                office = self.resolver.office(employer)
                key = self.resolver.employer(employer)
                role = {'title': title, 'employer': employer, 'employer_id': key,
                        'office_id': office, 'observed_at': observed, 'basis': 'Currently listed in directory', 'provenance': proof}
                roles.append(role)
                add('current_employment', key, employer, proof)
                affiliations(employer, proof)
            for entry in profile.get('employment_history', []):
                employer = entry.get('employer', '')
                key = self.resolver.employer(employer)
                dates = employment_dates(entry.get('date_text'), observed)
                current = is_latest and dates.get('listed_current')
                if not current:
                    historical.append({**entry, 'employer_id': key, 'provenance': proof})
                    add('past_employment', key, employer, proof)
                elif not any(r['employer_id'] == key for r in roles):
                    office = self.resolver.office(employer)
                    roles.append({**entry, 'employer_id': key, 'office_id': office, 'observed_at': observed,
                                  'basis': 'Listed as present in directory history', 'provenance': proof})
                    add('current_employment', key, employer, proof)
                affiliations(employer, proof)
            for subject in profile.get('profile_issue_areas', []):
                add('subject', normalized(subject), subject, proof)
            # This field describes the source's office/committee partition. It
            # does not assert an employee's personal political identification.
            if is_latest and profile.get('partition') in ('Democratic', 'Republican', 'Independent'):
                party = profile['partition']
                add('party', normalized(party), party + ' office / committee', proof)
        for stint in node.get('stints', []):
            employer = stint.get('container_id')
            source = self.nodes.get(employer, {})
            label = source.get('label', employer)
            proof = {'node': node.get('provenance'), 'stint': stint}
            is_latest = stint.get('status') == 'current' and stint.get('core_end_exclusive') == latest[employer]
            entry = {**stint, 'employer_id': employer, 'employer': label,
                     'title': stint.get('title_last'), 'provenance': proof}
            work_records.append(entry)
            # A payroll compensation bucket is not a second workplace or a
            # separate member. Retain the record, without manufacturing extra
            # affinity from an unresolved office/account name.
            workplace_known = stint.get('container_kind') != 'unresolved' and 'INTERN COMPENSATION' not in str(label).upper()
            if is_latest:
                payroll.append(entry)
            # Payroll says what was recorded at its cutoff, never current today.
            if employer not in features['current_employment']:
                historical.append(entry)
                if workplace_known:
                    add('past_employment', employer, label, proof)
            if stint.get('member_bioguide'):
                add('member', stint['member_bioguide'], clean_office(label), proof)
            elif source.get('container_kind') == 'member_office' and office_key(label, source.get('chamber')):
                add('member', source.get('member_bioguide') or employer, clean_office(label), proof)
            if stint.get('container_kind') == 'committee':
                add('committee', normalized(clean_office(label)), clean_office(label), proof)
        for member in node.get('member_ids', []):
            if str(member).startswith('politico-affiliation:'):
                continue
            mapped = self.resolver.by_member.get(member, set())
            label = self.nodes[next(iter(mapped))].get('label', member) if len(mapped) == 1 else member
            add('member', member, clean_office(label), node.get('provenance'))
        for committee in node.get('committee_ids', []):
            label = self.nodes.get(committee, {}).get('label', committee)
            if not str(committee).startswith('politico-affiliation:'):
                add('committee', normalized(clean_office(label)), label, node.get('provenance'))
        for policy in node.get('policy_ids', []):
            label = self.nodes.get(policy, {}).get('label', policy)
            add('subject', normalized(label), label, node.get('provenance'))
        for entry in node.get('education', []):
            if not isinstance(entry, dict):
                continue
            institution = entry.get('institution')
            proof = entry.get('provenance') or node.get('provenance')
            if institution:
                add('education', normalized(institution), institution, proof)
            for major in majors(entry.get('degree', '')):
                add('major', major, major, proof)
        for episode in episodes:
            employer = episode.get('employer_id')
            label = episode.get('employer_label') or self.nodes.get(employer, {}).get('label', employer)
            proof = episode.get('provenance', {})
            if episode.get('relationship') == 'co_reported_lobbying_activity':
                continue  # The exact activity provider supplies this evidence.
            if episode.get('user_asserted') and episode.get('current_evidence'):
                if (episode.get('as_of') or '') <= self.as_of < (episode.get('assertion_end_exclusive') or '9999-12-31'):
                    # Pair assertions are indexed by their exact pair hub. They
                    # never confer current employment on a third person.
                    add('current_employment', employer, label, {**proof, 'user_asserted': True})
                    roles.append({'title': 'Coworker relationship confirmed by you', 'employer': label,
                        'employer_id': employer, 'office_id': None, 'observed_at': episode.get('as_of'),
                        'basis': 'Your current coworker statement', 'provenance': proof})
            elif not episode.get('continuous_employment_asserted') is False:
                add('past_employment', employer, label, proof)
        status = 'currently_listed' if any(r['basis'] != 'Your current coworker statement' for r in roles) else 'latest_payroll' if payroll else 'unknown'
        self.features[ident] = features
        self.people[ident] = {'employment_status': status,
            'is_current_staff': any(r.get('office_id') or 'committee' in str(r.get('employer', '')).lower() for r in roles),
            'current_roles': roles, 'past_roles': historical, 'latest_payroll_roles': payroll,
            'latest_roles': latest_roles(roles, work_records, episodes),
            'sections': [{'key': key, 'label': label, 'weight': weight, 'items': list(features[key].values())}
                         for key, label, weight in DIMENSIONS],
            'identity_source_count': len(node.get('source_identity_records') or [node]),
            'identity_note': 'Matched source records are combined. Unresolved name matches remain separate.'}
