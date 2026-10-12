"""The shared contract for the Giving Forecast (brain DE-021: one schema + one fixture set, asserted against every producer).

Two documents cross a lane boundary:

* giving-models-v2   written by the R refit pipeline (mentat/forecast/refit), read by mentat/app/giving_forecast.py
* giving-anchors-v1  written by mentat/forecast/anchors (Python, read-only over Mentat), read by mentat/app/giving_forecast.py

This module is the Python side of the contract: the schema loader, a validator for the JSON Schema keyword subset the two
schemas use (see README.md; the R pipeline implements the same subset by hand, because no JSON Schema package is installed
there or here), the shared negative controls, and the cross-field invariants of the anchor snapshot that a schema cannot say.

Nothing here touches Mentat's data or accounting code.
"""
from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path

CONTRACT_DIR = Path(__file__).resolve().parent
SCHEMAS = {'models': 'giving-models-v2.schema.json', 'anchors': 'giving-anchors-v1.schema.json'}
EXAMPLES = {'models': 'giving-models-v2.example.json', 'anchors': 'giving-anchors-v1.example.json',
            'models_cycle': 'giving-models-v2.cycle.example.json'}       # the same bundle fixture carrying the optional `cycle_model` (and the Pres term in the Senate model)
NEGATIVES = 'negative-controls.json'

SUPPORTED_KEYWORDS = {
    '$schema', '$id', '$defs', '$ref', 'title', 'description', 'type', 'enum', 'const', 'properties', 'required',
    'additionalProperties', 'propertyNames', 'minProperties', 'items', 'minItems', 'minimum', 'maximum', 'exclusiveMinimum',
    'minLength', 'pattern',
}


def read_json(path):
    with Path(path).open(encoding='utf-8-sig') as stream:
        return json.load(stream)


def schema(kind):
    return read_json(CONTRACT_DIR / SCHEMAS[kind])


def example(kind):
    return read_json(CONTRACT_DIR / EXAMPLES[kind])


def negative_controls():
    return read_json(CONTRACT_DIR / NEGATIVES)['controls']


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_integer(value):
    return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, float) and math.isfinite(value) and value.is_integer())


_TYPES = {
    'object': lambda v: isinstance(v, dict),
    'array': lambda v: isinstance(v, list),
    'string': lambda v: isinstance(v, str),
    'boolean': lambda v: isinstance(v, bool),
    'null': lambda v: v is None,
    'number': lambda v: _is_number(v) and math.isfinite(v),
    'integer': _is_integer,
}


def _resolve(ref, root):
    if not ref.startswith('#/$defs/'):
        raise ValueError('Only local $defs references are supported: ' + ref)
    return root['$defs'][ref[len('#/$defs/'):]]


def _check(value, node, root, path, errors):
    if '$ref' in node:
        _check(value, _resolve(node['$ref'], root), root, path, errors)
        return
    unknown = set(node) - SUPPORTED_KEYWORDS
    if unknown:
        raise ValueError('Schema keyword outside the supported subset at ' + (path or '$') + ': ' + ', '.join(sorted(unknown)))
    where = path or '$'
    if 'type' in node:
        wanted = node['type'] if isinstance(node['type'], list) else [node['type']]
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(where + ': non-finite number')
            return
        if not any(_TYPES[t](value) for t in wanted):
            errors.append(where + ': expected ' + '/'.join(wanted) + ', got ' + type(value).__name__)
            return
    if 'const' in node and not (value == node['const'] and type(value) is type(node['const'])):
        errors.append(where + ': must equal ' + json.dumps(node['const']))
    if 'enum' in node and value not in node['enum']:
        errors.append(where + ': must be one of ' + json.dumps(node['enum']))
    if isinstance(value, str):
        if 'pattern' in node and not re.search(node['pattern'], value):
            errors.append(where + ': does not match ' + node['pattern'])
        if 'minLength' in node and len(value) < node['minLength']:
            errors.append(where + ': shorter than ' + str(node['minLength']))
    if _is_number(value) and math.isfinite(value):
        if 'minimum' in node and value < node['minimum']:
            errors.append(where + ': below ' + str(node['minimum']))
        if 'maximum' in node and value > node['maximum']:
            errors.append(where + ': above ' + str(node['maximum']))
        if 'exclusiveMinimum' in node and value <= node['exclusiveMinimum']:
            errors.append(where + ': not above ' + str(node['exclusiveMinimum']))
    if isinstance(value, dict):
        props = node.get('properties', {})
        for key in node.get('required', []):
            if key not in value:
                errors.append(where + ': missing required "' + key + '"')
        extra = node.get('additionalProperties', True)
        if 'minProperties' in node and len(value) < node['minProperties']:
            errors.append(where + ': fewer than ' + str(node['minProperties']) + ' entries')
        names = node.get('propertyNames', {}).get('pattern')
        for key, item in value.items():
            child = (path + '.' if path else '$.') + key
            if names and not re.search(names, key):
                errors.append(child + ': key does not match ' + names)
            if key in props:
                _check(item, props[key], root, child, errors)
            elif extra is False:
                errors.append(child + ': unexpected property')
            elif isinstance(extra, dict):
                _check(item, extra, root, child, errors)
    if isinstance(value, list):
        if 'minItems' in node and len(value) < node['minItems']:
            errors.append(where + ': fewer than ' + str(node['minItems']) + ' items')
        if 'items' in node:
            for index, item in enumerate(value):
                _check(item, node['items'], root, where + '[' + str(index) + ']', errors)


def validate(instance, doc):
    """Problems of `instance` against schema `doc` (a loaded schema document); empty list = valid."""
    errors = []
    _check(instance, doc, doc, '', errors)
    return errors


def validate_kind(instance, kind):
    return validate(instance, schema(kind))


# --- shared negative controls -------------------------------------------------------------------------------------------
NONFINITE = {'NaN': float('nan'), 'Infinity': float('inf'), '-Infinity': float('-inf')}


def _walk(doc, steps):
    node = doc
    for step in steps:
        node = node[step]
    return node


def apply_mutation(document, mutation):
    """Apply one negative-control mutation to a deep copy. ops: delete, set, add_key. A value {"$nonfinite": "NaN"} means a
    non-finite number (JSON cannot carry one; each language substitutes its own NaN / Inf)."""
    out = copy.deepcopy(document)
    path = mutation['path']
    parent = _walk(out, path[:-1])
    key = path[-1]
    op = mutation['op']
    if op == 'delete':
        del parent[key]
        return out
    value = copy.deepcopy(mutation.get('value'))
    if isinstance(value, dict) and set(value) == {'$nonfinite'}:
        value = NONFINITE[value['$nonfinite']]
    parent[key] = value
    return out


# --- model bundle invariants (cross-field rules a schema cannot express) ---------------------------------------------------
def check_model_invariants(bundle, tolerance=1e-9):
    """Cross-field rules of a schema-valid model bundle (the anchor auto-pick of owner ruling 2026-09-30). Returns a list of problems (empty = consistent).

    * every industry with the control capability has an `anchors` entry in each share model, and every entry names a known industry;
    * `prediction.industries[id]` repeats the residual block of the anchor the entry chose (the consumer reads only that block);
    * `same_phase_gain` is 1 - MAE(same_phase) / MAE(previous_cycle) per design;
    * the choice follows the rule: same_phase only when it beats previous_cycle by `min_gain` in every design (and previous_cycle otherwise).
    """
    problems = []
    for model_id, model in bundle['share_models'].items():
        anchors, chosen_blocks = model['anchors'], model['prediction']['industries']
        for ident, industry in bundle['industries'].items():
            if industry['capabilities']['control'] and ident not in anchors:
                problems.append('share_models.' + model_id + '.anchors: no entry for ' + ident + ', which has the control capability')
        for ident, entry in anchors.items():
            tag = 'share_models.' + model_id + '.anchors.' + ident
            if ident not in bundle['industries']:
                problems.append(tag + ': not an industry of the bundle')
                continue
            block = entry['prediction'][entry['anchor']]
            if chosen_blocks.get(ident) is None:
                problems.append(tag + ': no prediction.industries block for the chosen anchor')
            else:
                for key in ('sigma2', 'pairs', 'df', 'critical_95'):
                    if abs(chosen_blocks[ident][key] - block[key]) > tolerance * max(1.0, abs(block[key])):
                        problems.append(tag + ': prediction.industries[' + ident + '].' + key + ' is not the ' + entry['anchor'] + ' block')
            back, gains = entry['backtest'], entry['backtest']['same_phase_gain']
            for design in ('loeo', 'chrono'):
                prev, same = back['previous_cycle'][design + '_mae'], back['same_phase'][design + '_mae']
                expected = None if prev is None or same is None or prev <= 0 else 1 - same / prev
                if (expected is None) != (gains[design] is None) or (expected is not None and abs(expected - gains[design]) > 1e-6):
                    problems.append(tag + ': same_phase_gain.' + design + ' does not follow from the two MAEs')
            wins = all(gains[d] is not None and gains[d] >= entry['min_gain'] - 1e-12 for d in ('loeo', 'chrono'))
            if entry['anchor'] == 'same_phase' and not wins:
                problems.append(tag + ': same_phase chosen without beating previous_cycle by min_gain in every design')
            if entry['anchor'] == 'previous_cycle' and wins:
                problems.append(tag + ': previous_cycle kept although same_phase beats it by min_gain in every design')
    if 'cycle_model' in bundle:
        problems.extend(check_cycle_model(bundle, tolerance))
    return problems


CYCLE_YEARS = ('2023', '2024', '2025', '2026', '2027')       # the IBIS reference years the 2026 and 2028 budget rules read (lag window c-3..c-1 of both horizons)


def check_cycle_model(bundle, tolerance=1e-9):
    """Cross-field rules of the optional `cycle_model` (owner rulings 2026-10-07). Returns a list of problems (empty = consistent).

    * per measure: d_h = b0 + b1 (k_max + sum_{j<=h} damp^j) for h = 1, 2; every step block has df = pairs - 1;
    * every industry the console offers for giving (calculator and a Mentat key, unless it is flagged `lobbying_only`) has a general-giving step block and a complete REAL IBIS growth path 2023..2027 (each above -100%);
    * the budget rule weights the model by 0.5; the pace series carry F > 0 and 0 < O15 <= O18 <= O21 <= F in every cycle;
    * the Senate share model carries the presidential-cycle term (owner ruling 2026-10-07, "2. Keep senate in").
    """
    problems = []
    cm = bundle['cycle_model']
    for mid, m in cm['measures'].items():
        tag = 'cycle_model.measures.' + mid
        drift = m['drift']
        for h in ('1', '2'):
            want = drift['b0'] + drift['b1'] * (drift['k_max'] + sum(drift['damp'] ** j for j in range(1, int(h) + 1)))
            if abs(drift['d'][h] - want) > tolerance:
                problems.append(tag + '.drift.d.' + h + ' does not follow from b0, b1, k_max and damp')
        for ident, step in m['step'].items():
            if step['df'] != step['pairs'] - 1:
                problems.append(tag + '.step.' + ident + ': df is not pairs - 1')
    offered = [i for i, row in bundle['industries'].items() if row['calculator'] and row['mentat_key'] and not row.get('lobbying_only')]       # only an explicit lobbying-only industry (none today; Telecom was one until the owner ruling of 2026-10-08) is not checked for giving inputs: a withheld PAC capability alone is not enough
    for ident in offered:
        if ident not in cm['measures']['general_giving']['step']:
            problems.append('cycle_model.measures.general_giving.step: no entry for ' + ident + ', which the console offers')
        growth = cm['budget_rule']['growth'].get(ident)
        if growth is None:
            problems.append('cycle_model.budget_rule.growth: no entry for ' + ident)
            continue
        for year in CYCLE_YEARS:
            value = growth['annual'].get(year)
            if value is None:
                problems.append('cycle_model.budget_rule.growth.' + ident + ': no growth for ' + year)
            elif not value > -1:
                problems.append('cycle_model.budget_rule.growth.' + ident + '.' + year + ': a growth of -100% or less')
    if cm['budget_rule']['weight_model'] != 0.5:
        problems.append('cycle_model.budget_rule.weight_model must be 0.5 (the 50/50 rule)')
    for ident, series in cm['pace']['series'].items():
        for cyc, row in series['cycles'].items():
            if not (0 < row['O15'] <= row['O18'] <= row['O21'] <= row['F']):
                problems.append('cycle_model.pace.series.' + ident + '.' + cyc + ': needs 0 < O15 <= O18 <= O21 <= F')
    if 'Pres' not in bundle['share_models'].get('control_Senate', {}).get('terms', []):
        problems.append('share_models.control_Senate: the presidential-cycle term Pres is required with a cycle_model (owner ruling 2026-10-07)')
    return problems


def validate_model(instance):
    """Schema problems of a model bundle, else its cross-field problems."""
    return validate_kind(instance, 'models') or check_model_invariants(instance)


# --- anchor snapshot invariants (cross-field rules a schema cannot express) ----------------------------------------------
def _sum_dr(block):
    return sum(block[k] for k in ('D', 'R', 'other'))


def check_anchor_invariants(snapshot, tolerance=0.011):
    """Cross-field rules of a schema-valid anchor snapshot. Returns a list of problems (empty = consistent)."""
    problems = []
    threshold = snapshot['thin_base_threshold_usd']
    for ident, ind in snapshot['industries'].items():
        tag = 'industries.' + ident
        cov = ind['coverage']
        if str(cov['current_cycle'] - 2) not in snapshot['cpi']['multipliers']:
            problems.append(tag + '.coverage: no CPI multiplier for the anchor cycle ' + str(cov['current_cycle'] - 2))
        if cov['coverage_end'] > cov['rule_coverage_end']:
            problems.append(tag + '.coverage: coverage_end is later than the rule coverage end')
        if cov['coverage_end'] > cov['collection_date']:
            problems.append(tag + '.coverage: coverage_end is after the collection date')
        for measure in ('general_giving', 'candidate_receipts', 'pac_to_candidates'):
            block = ind[measure]
            if block['status'] != ind['status'][measure]:
                problems.append(tag + '.' + measure + ': status differs from the industry status map')
            cuts = block.get('observed', [])
            if block['status'] == 'ready' and not cuts:
                problems.append(tag + '.' + measure + ': ready without observed cuts')
            dates = [c['through'] for c in cuts]
            if dates != sorted(dates) or len(set(dates)) != len(dates):
                problems.append(tag + '.' + measure + '.observed: cut dates must be strictly increasing')
            if dates and dates[-1] != cov['coverage_end']:
                problems.append(tag + '.' + measure + '.observed: the last cut must be the coverage end')
            if any(not re.search(r'-(01|02|03|04|05|06|07|08|09|10|11|12)-\d\d$', d) for d in dates):
                problems.append(tag + '.' + measure + '.observed: malformed cut date')
        # Snapshots built from the 2026-10-01 ruling report what the forecast left out - the names (`excluded_funding_sources`) AND the amounts
        # (`excluded_non_giving`) - in BOTH the general-giving and the candidate block; older snapshots carry none of it and are not checked.
        general, candidates, reporting = ind['general_giving'], ind['candidate_receipts'], {}
        for name, block in (('general_giving', general), ('candidate_receipts', candidates)):
            if block['status'] != 'ready':
                continue
            present = [key for key in ('excluded_funding_sources', 'excluded_non_giving') if key in block]
            reporting[name] = len(present) == 2
            if len(present) == 1:
                missing = 'excluded_non_giving' if present[0] == 'excluded_funding_sources' else 'excluded_funding_sources'
                problems.append(tag + '.' + name + ': ' + present[0] + ' is reported without ' + missing + ' (a block that reports the exclusion reports both)')
        if len(set(reporting.values())) > 1:
            problems.append(tag + ': general_giving and candidate_receipts must both report the non_giving exclusion or neither')
        if reporting.get('general_giving'):
            left_out, split, dropped = general['excluded_non_giving'], general['by_funding_source'], set(general['excluded_funding_sources'])
            for cyc in sorted(set(general['cycles']) | set(left_out) | set(split)):
                parts = split.get(cyc, {})
                if abs(general['cycles'].get(cyc, 0) + left_out.get(cyc, 0) - sum(parts.values())) > tolerance:
                    problems.append(tag + '.general_giving.cycles.' + cyc + ': cycles + excluded_non_giving differs from the by_funding_source total')
                if abs(left_out.get(cyc, 0) - sum(v for source, v in parts.items() if source in dropped)) > tolerance:
                    problems.append(tag + '.general_giving.excluded_non_giving.' + cyc + ': differs from the excluded funding sources in by_funding_source')
        if reporting.get('candidate_receipts'):
            # The candidate amounts cannot be cross-checked against a by-source split (there is none), so check their shape: one entry per candidate cycle,
            # and the same excluded sources as the general-giving block.
            if set(candidates['excluded_non_giving']) != set(candidates['cycles']):
                problems.append(tag + '.candidate_receipts.excluded_non_giving: its cycles differ from the candidate cycles')
            if reporting.get('general_giving') and sorted(candidates['excluded_funding_sources']) != sorted(general['excluded_funding_sources']):
                problems.append(tag + '.candidate_receipts.excluded_funding_sources: differs from general_giving.excluded_funding_sources')
        pac = ind['pac_to_candidates']
        # The odd-year split of the cycle model: an optional block per measure; each cycle's odd-year amount lies between 0 and its cycle total (a cycle the block no longer lists is not compared).
        for cyc, amount in general.get('odd_year', {}).items():
            if not 0 <= amount or (cyc in general['cycles'] and amount > general['cycles'][cyc] + tolerance):
                problems.append(tag + '.general_giving.odd_year.' + cyc + ': outside 0..the cycle total')
        for cyc, row in pac.get('odd_year', {}).items():
            parts = _sum_dr(row['House']) + _sum_dr(row['Senate'])
            if abs(parts - row['to_house_senate']) > tolerance:
                problems.append(tag + '.pac_to_candidates.odd_year.' + cyc + ': to_house_senate differs from the House + Senate parts')
            if not 0 <= row['to_house_senate'] or (cyc in pac['cycles'] and row['to_house_senate'] > pac['cycles'][cyc]['to_house_senate'] + tolerance):
                problems.append(tag + '.pac_to_candidates.odd_year.' + cyc + ': outside 0..the cycle total')
        if pac['committee_count'] != len(pac['committees']):
            problems.append(tag + '.pac_to_candidates: committee_count differs from the committee list')
        if pac['single_pac'] != (pac['committee_count'] == 1):
            problems.append(tag + '.pac_to_candidates: single_pac must be true exactly when one committee is listed')
        for cyc, row in pac['cycles'].items():
            hs = _sum_dr(row['House']) + _sum_dr(row['Senate'])
            if abs(hs - row['to_house_senate']) > tolerance:
                problems.append(tag + '.pac_to_candidates.cycles.' + cyc + ': to_house_senate differs from the House + Senate parts')
            if abs(row['to_house_senate'] + row['presidential'] + row['no_candidate'] - row['all_recipients']) > tolerance:
                problems.append(tag + '.pac_to_candidates.cycles.' + cyc + ': all_recipients differs from its parts')
        for cut in pac['observed']:
            hs = _sum_dr(cut['House']) + _sum_dr(cut['Senate'])
            if abs(hs - cut['to_house_senate']) > tolerance:
                problems.append(tag + '.pac_to_candidates.observed ' + cut['through'] + ': to_house_senate differs from its parts')
        for label, block in (('candidate_receipts', ind['candidate_receipts']), ('pac_to_candidates', pac)):
            for cyc, flags in block['thin_base'].items():
                for chamber in ('House', 'Senate'):
                    row = block['cycles'].get(cyc, {}).get(chamber)
                    if row is None:
                        problems.append(tag + '.' + label + '.thin_base.' + cyc + ': no cycle row')
                        continue
                    expected = (row['D'] + row['R']) < threshold
                    if flags[chamber] != expected:
                        problems.append(tag + '.' + label + '.thin_base.' + cyc + '.' + chamber + ': flag disagrees with the D+R total and the threshold')
        lda = ind['lda']
        if lda['status'] == 'ready':
            if set(lda['series']) != {'income_total', 'expenses_total'}:
                problems.append(tag + '.lda.series: both income_total and expenses_total are required')
            elif set(lda['series']['income_total']['annual']) != set(lda['series']['expenses_total']['annual']):
                problems.append(tag + '.lda.series: the two series must cover the same complete years')
            for sid, series in lda['series'].items():
                partial = series['partial']
                if partial is not None and abs(sum(partial['quarters'].values()) - partial['total']) > tolerance:
                    problems.append(tag + '.lda.series.' + sid + ': partial total differs from its quarters')
                if partial is not None and str(partial['year']) in series['annual']:
                    problems.append(tag + '.lda.series.' + sid + ': a partial year must not also be listed as a complete annual value')
                now = None if partial is None else partial.get('nowcast')
                if now is not None:
                    where = tag + '.lda.series.' + sid + '.partial.nowcast'
                    geo = math.exp(sum(math.log(v) for v in now['shares']) / len(now['shares']))
                    cut_quarters = ['Q' + str(i) for i in range(1, int(now['through_quarter'][1]) + 1)]
                    if len(now['shares']) != len(now['share_years']) or len(now['shares']) > 3 or now['year'] != partial['year'] or now['through_quarter'] != partial['through_quarter']:
                        problems.append(where + ': year, cut and the (at most three) prior shares must match the partial year')
                    if abs(geo - now['share']) > 1e-9:
                        problems.append(where + ': share is not the geometric mean of shares')
                    if abs(sum(partial['quarters'].get(q, 0.) for q in cut_quarters) - now['observed']) > tolerance:
                        problems.append(where + ': observed differs from the filed quarters through the cut')
                    if str(now['year'] - 1) in series['annual'] and abs(series['annual'][str(now['year'] - 1)] - now['prior_total']) > tolerance:
                        problems.append(where + ': prior_total differs from the annual value of the prior year')
                    if abs(now['observed'] + (1. - now['share']) * now['prior_total'] - now['point']) > max(tolerance, 1e-9 * now['point']):
                        problems.append(where + ': point is not observed + (1 - share) * prior_total')
    errors = snapshot.get('lda_nowcast_errors')
    for cut, row in ({} if errors is None else errors['by_cut']).items():
        if row['q80'] > row['max'] + 1e-12:
            problems.append('lda_nowcast_errors.by_cut.' + cut + ': the 80th percentile exceeds the largest miss')
    return problems
