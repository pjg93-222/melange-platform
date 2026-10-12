"""Dated industry alignment and champion-ideology features; no model fitting.

Relations are supplied evidence, never inferred from giving. All member weights
are equal. A current revised ideology file cannot masquerade as an old release.
The complete flag describes the declared source universe, not all real lobbying.
"""
from datetime import date
import math
from statistics import fmean, pstdev

RELATIONS = ('direct_contact', 'bill_alignment', 'champion')
PARTIES = ('D', 'R', 'Other', 'Unknown')
ASOF_VINTAGES = ('archived_as_of', 'truncated_estimate')


class FeatureError(ValueError):
    pass


def iso(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (ValueError, TypeError) as error:
        raise FeatureError('A canonical ISO date is required') from error
    return value


def dated(row, issue):
    # This date is availability of the entire supplied record/version, including
    # label assignment and effective_to changes, not merely the original event.
    start = iso(row['effective_from'])
    end = iso(row['effective_to']) if row.get('effective_to') else None
    available = iso(row['source_available_on']) if row.get('source_available_on') else None
    if end is not None and end < start:
        raise FeatureError('Reversed evidence interval')
    if not row.get('source_ref'):
        return False, 'missing_source_reference'
    if available is None:
        return False, 'unknown_source_availability'
    if available > issue or start > issue:
        return False, 'not_available_at_issue'
    if end is not None and end < issue:
        return False, 'outside_membership_interval'
    return True, None


def members_at_issue(relations, sector, chamber, issue, kind, scope_id):
    members, rejected = {}, {}
    for row in relations:
        if row['sector'] != sector or row['chamber'] != chamber or row['relation'] != kind or row['scope_id'] != scope_id:
            continue
        if row.get('party') not in PARTIES or not row.get('member_id'):
            raise FeatureError('Member identity and explicit party group are required')
        valid, reason = dated(row, issue)
        if not valid:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        member = row['member_id']
        # Multiple bill/press/contact records never multiply a member's weight.
        if member in members and members[member]['party'] != row['party']:
            raise FeatureError('Conflicting party evidence for an active member')
        members.setdefault(member, {'party': row['party'], 'evidence': set()})['evidence'].add(row['source_ref'])
    return members, rejected


def ideology_at_issue(scores, chamber, issue):
    chosen, rejected = {}, {}
    for row in scores:
        if row['chamber'] != chamber:
            continue
        valid, reason = dated(row, issue)
        if valid and row.get('vintage_basis') not in ASOF_VINTAGES:
            valid, reason = False, 'unverified_or_later_revised_vintage'
        if valid and (not row.get('votes_through') or iso(row['votes_through']) > issue):
            valid, reason = False, 'unknown_or_future_vote_cutoff'
        if not valid:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        value = row.get('dw_nominate_dim1')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > 1:
            raise FeatureError('Finite first-dimension DW-NOMINATE in [-1,1] required')
        if row.get('party') not in PARTIES or not row.get('member_id'):
            raise FeatureError('Ideology member identity and party are required')
        if not row.get('score_vintage_id') or type(row.get('score_congress')) is not int:
            raise FeatureError('A pinned score vintage and Congress are required')
        if row['score_congress'] <= 0 or 1787 + 2 * row['score_congress'] > int(issue[:4]):
            raise FeatureError('Score Congress cannot be invalid or begin after the issue year')
        member = row['member_id']
        stamp = row['source_available_on'], row['votes_through']
        old = chosen.get(member)
        if old is not None and stamp == old[0] and (value, row['party'], row['score_vintage_id'], row['score_congress']) != (old[1], old[2], old[4], old[5]):
            raise FeatureError('Conflicting scores within the same source vintage')
        if old is None or stamp > old[0]:
            chosen[member] = stamp, value, row['party'], row['source_ref'], row['score_vintage_id'], row['score_congress']
    # Do not compare positions recalibrated on different vote universes or use
    # a different reference release for one party. Supply a coherent snapshot.
    if len({(r[0], r[4], r[5]) for r in chosen.values()}) > 1:
        raise FeatureError('Ideology inputs must share one release, vote cutoff and Congress')
    return chosen, rejected


def composition(members):
    counts = {p: sum(m['party'] == p for m in members.values()) for p in PARTIES}
    two_party = counts['D'] + counts['R']
    return dict(member_count=len(members), party_counts=counts,
                republican_share_of_known_two_party=None if not two_party else counts['R'] / two_party,
                known_party_fraction=None if not members else (len(members) - counts['Unknown']) / len(members))


def build_features(relations, scores, *, sector, chamber, issue, target_relation,
                   scope_id, source_universe_complete):
    """Aggregate two supplied historical member sets, retaining missingness.

    source_universe_complete requires explicit coverage receipts from the caller
    for target_relations, champions and ideology_reference. It is not inferred
    from nonempty rows. No target dollars, outcome labels or fitted weights enter.
    Readiness is conditional on those evidence declarations; this aggregator
    cannot authenticate an archive or establish historical membership itself.
    """
    issue = iso(issue)
    if chamber not in ('House', 'Senate') or target_relation not in ('direct_contact', 'bill_alignment'):
        raise FeatureError('Explicit chamber and contact/association definition required')
    if not sector or not scope_id or set(source_universe_complete) != {'target_relations', 'champions', 'ideology_reference'}:
        raise FeatureError('Explicit scope and all three source coverage declarations required')
    if any(type(v) is not bool for v in source_universe_complete.values()):
        raise FeatureError('Coverage declarations must be booleans')
    targets, target_rejected = members_at_issue(relations, sector, chamber, issue, target_relation, scope_id)
    champions, champion_rejected = members_at_issue(relations, sector, chamber, issue, 'champion', scope_id)
    ideology, score_rejected = ideology_at_issue(scores, chamber, issue)
    values, centered, missing = [], [], []
    party_reference = {p: [r[1] for r in ideology.values() if r[2] == p] for p in ('D', 'R')}
    means = {p: fmean(v) if v else None for p, v in party_reference.items()}
    for member, champion in champions.items():
        score = ideology.get(member)
        if score is None or score[2] != champion['party']:
            missing.append(member)
            continue
        values.append(score[1])
        if score[2] in means and means[score[2]] is not None:
            centered.append(score[1] - means[score[2]])
    target_composition = composition(targets)
    champion_composition = composition(champions)
    unresolved_eligibility = any(rejections.get(reason, 0) for rejections in
                                 (target_rejected, champion_rejected, score_rejected)
                                 for reason in ('missing_source_reference', 'unknown_source_availability',
                                                'unverified_or_later_revised_vintage', 'unknown_or_future_vote_cutoff'))
    ready = (all(source_universe_complete.values()) and bool(targets) and bool(champions)
             and not unresolved_eligibility and not missing and not target_composition['party_counts']['Unknown']
             and not champion_composition['party_counts']['Unknown']
             and target_composition['republican_share_of_known_two_party'] is not None)
    return dict(sector=sector, chamber=chamber, issue=issue, scope_id=scope_id,
                target_relation=target_relation, weighting='one equal weight per distinct member',
                target_composition=target_composition, champion_composition=champion_composition,
                champion_dw_nominate_mean=fmean(values) if values else None,
                champion_dw_nominate_dispersion=pstdev(values) if len(values) >= 2 else None,
                champion_within_party_ideology_mean=fmean(centered) if centered else None,
                ideology_matched_champions=len(values), ideology_missing_or_party_conflicting_members=sorted(missing),
                ideology_coverage=None if not champions else len(values) / len(champions),
                ideology_reference_party_means=means,
                ideology_reference_party_counts={p: len(v) for p, v in party_reference.items()},
                ideology_vintage=None if not ideology else dict(
                    source_available_on=next(iter(ideology.values()))[0][0],
                    votes_through=next(iter(ideology.values()))[0][1],
                    score_vintage_id=next(iter(ideology.values()))[4],
                    score_congress=next(iter(ideology.values()))[5]),
                source_universe_complete=source_universe_complete, complete_primary_features=ready,
                complete_party_centered_feature=ready and len(centered) == len(champions),
                exclusions={'target_relations': target_rejected, 'champions': champion_rejected, 'ideology': score_rejected},
                interpretation='Associations do not prove direct contact or support; champions require independently supplied support evidence.',
                model_fitted=False, live_activation=False)
