"""Dated industry scenarios from the reviewed growth/giving models.

Pure read-only calculation. No workbook mutation, collection or live-data claims.

Schema 1 (giving-models-v1.json): whole-industry OpenSecrets scenarios; company receipts never substitute for the research
industry's accounting. Kept so the console keeps working until a schema-2 bundle exists.

Schema 2 (giving-models-v2.json): the models only supply the RELATIONSHIPS (estimated on OpenSecrets); every LEVEL comes from Mentat's
own anchor snapshot (owner rulings of 2026-09-30, see the section "Schema 2" below). Both bundle and snapshot are validated against the
shared contract in mentat/forecast/contracts (brain DE-021).
"""
import copy
from datetime import date, datetime, timedelta
from functools import lru_cache
import hashlib
import json
import math
import os
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo
import zipfile
import zlib

MODEL_FILE = Path(__file__).parent / 'data' / 'giving-models-v1.json'
MODEL_FILE_V2 = Path(__file__).parent / 'data' / 'giving-models-v2.json'
CHAMBERS = ('House', 'Senate')


@lru_cache(maxsize=4)
def _load(path, stamp, size, expected_sha256=None):
    raw = Path(path).read_bytes()
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise ValueError("The active forecast model package does not match its published SHA-256.")
    out = json.loads(raw)
    version = out.get('schema_version') if isinstance(out, dict) else None
    if type(version) is not int or version not in (1, 2):
        raise ValueError('Unsupported forecast model package. Rebuild the reviewed model package.')
    if version == 2:
        problems = _contract().validate_model(out)                   # schema, then the cross-field invariants (the anchor auto-pick)
        if not problems:
            problems = _v2_contract_problems(out)
        if problems:
            raise ValueError('The forecast model package does not satisfy its contract (' + '; '.join(problems[:3]) + '). Rebuild the reviewed model package.')
    out['sha256'] = actual_sha256
    return out


def _model_selection():
    """Read the atomic publication pointer once; an explicit smoke-test override wins."""
    override = os.environ.get('MENTAT_GIVING_MODELS', '').strip()
    if override:
        return Path(override), None
    pointer_path = MODEL_FILE.parent / 'giving-models-current.json'
    if not pointer_path.exists():
        return (MODEL_FILE_V2 if MODEL_FILE_V2.is_file() else MODEL_FILE), None
    try:
        pointer = json.loads(pointer_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ValueError('The forecast publication pointer cannot be read.') from error
    if not isinstance(pointer, dict) or type(pointer.get('schema_version')) is not int or pointer['schema_version'] != 1:
        raise ValueError('Unsupported forecast publication pointer schema.')
    active = pointer.get('active')
    if not isinstance(active, str) or not re.fullmatch(r'(?:versions/[0-9a-f]{64}/)?giving-models-v[12]\.json', active):
        raise ValueError('The forecast publication pointer has an invalid active path.')
    if not isinstance(pointer.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', pointer['sha256']):
        raise ValueError('The forecast publication pointer has an invalid SHA-256.')
    if not isinstance(pointer.get('model_version'), str) or not pointer['model_version'].strip():
        raise ValueError('The forecast publication pointer has no model version.')
    expected_schema = int(active[-6])
    if 'model_schema_version' in pointer and (type(pointer['model_schema_version']) is not int or pointer['model_schema_version'] != expected_schema):
        raise ValueError('The forecast publication pointer has an inconsistent model schema.')
    target = (MODEL_FILE.parent / active).resolve()
    if not target.is_relative_to(MODEL_FILE.parent.resolve()):
        raise ValueError('The forecast publication pointer escapes the model directory.')
    if not target.is_file():
        raise ValueError('The active forecast model package is missing.')
    return target, pointer


def model_path():
    """Explicit override, published pointer, or legacy v2/v1 discovery when no pointer exists."""
    return _model_selection()[0]


def models():
    path, pointer = _model_selection()
    stat = path.stat()
    bundle = _load(str(path), stat.st_mtime_ns, stat.st_size, pointer['sha256'] if pointer else None)
    if pointer:
        if bundle.get('model_version') != pointer['model_version']:
            raise ValueError('The active forecast model version does not match its publication pointer.')
        if bundle.get('schema_version') != int(pointer['active'][-6]):
            raise ValueError('The active forecast model schema does not match its publication pointer.')
    return bundle


def _date(value, label):
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise ValueError(label + ' must be a calendar date (YYYY-MM-DD).') from None


CALENDAR = ZoneInfo('America/New_York')       # the console's calendar: the as-of date and date.today() are Eastern days (tzdata is pinned in requirements-console.txt)


def _model_vintage(b):
    """The first calendar day the model package is usable. `built_on` is the UTC date of `built_at_utc`, which is one day ahead of the console's calendar from 20:00 to 24:00
    Eastern, so a refit finished that evening would be refused "at that historical cutoff" until Eastern midnight (2026-10-09). The build instant is read in the console's
    calendar instead; a bundle with no instant (schema 1) keeps its built_on, and a built_on dated earlier than the build (an owner-dated vintage) stays earlier."""
    day = _date(b['built_on'], 'Model vintage')
    stamp = b.get('built_at_utc')
    if stamp:
        day = min(day, datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone(CALENDAR).date())
    return day


def _number(value, label, low=None, high=None):
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError(label + ' must be a number.')
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(label + ' must be a number.') from None
    if not math.isfinite(value) or (low is not None and value < low) or (high is not None and value > high):
        raise ValueError(label + ' is outside its supported range.')
    return value


def _text(value, label, limit=500):
    if value is None:
        return ''
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(label + ' must be text of at most ' + str(limit) + ' characters.')
    return value.strip()


def _mapping(value, label):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(label + ' must be an object.')
    return value


def _finite(value, label):
    """Reject NaN/Infinity anywhere in the request, including fields the calculation never reads."""
    pending = [(value, label)]
    while pending:
        item, path = pending.pop()
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError(path + ' is outside its supported range.')
        if isinstance(item, dict):
            pending.extend((v, path + ' ' + str(k)) for k, v in item.items())
        elif isinstance(item, (list, tuple)):
            pending.extend((v, path) for v in item)


def _reference(history, cycle, industry=None):
    candidates = [r for r in history if r['cycle'] < cycle and (industry is None or r.get('industry') == industry)]
    same_phase = [r for r in candidates if r['cycle'] % 4 == cycle % 4]
    if not candidates:
        raise ValueError('No earlier reference cycle is available for this industry.')
    return max(same_phase or candidates, key=lambda r: r['cycle'])


def _period(cycle):
    return str(cycle - 1) + '–' + str(cycle)


def catalog(as_of=None):
    b = models()
    today = _date(as_of, 'As-of date') if as_of else date.today()
    if b['schema_version'] == 2:
        return _catalog_v2(b, today)
    cycle = today.year + today.year % 2
    return dict(model_version=b['model_version'], model_sha256=b['sha256'], built_on=_model_vintage(b).isoformat(),
                as_of=today.isoformat(), current_cycle=cycle, next_cycle=cycle + 2,
                currency=b['currency'], industries=list(b['industries'].values()),
                share_models=b['share_models'], pac_model=b['pac_model'], lobby_model=b['lobby_model'],
                evidence=b['evidence'], industry_slopes=b['industry_slopes'], validation_metrics=b['validation_metrics'], sources=b['sources'], saved_outlook=b.get('saved_outlook'),
                scope='Research industry totals. Report company, donor-role and subindustry filters do not apply.',
                interval_label='Ordinary 95% coefficient interval; not a future-outcome prediction interval')


def _interval(model, vector):
    effect = sum(a * c for a, c in zip(vector, model['beta']))
    var = sum(vector[i] * model['covariance'][i][j] * vector[j]
              for i in range(len(vector)) for j in range(len(vector)))
    half = model['critical_95'] * math.sqrt(max(0., var))
    return effect, effect - half, effect + half


def _recorded(entry, as_of, fields, label):
    """A source-labeled total must cover precisely the calculation cutoff."""
    entry = _mapping(entry, label)
    values = {key: _number(entry.get(key), label + ' ' + key, 0, 1e12) for key in fields}
    if any(v is None for v in values.values()):
        return None, 'Add comparable observed amounts to calculate the remainder.'
    through = entry.get('through')
    source = _text(entry.get('source'), label + ' source')
    if not source or not through:
        return None, 'Add the observed amount’s source and coverage-through date.'
    through = _date(through, label + ' coverage-through date')
    if through != as_of:
        return None, 'Observed coverage must match the as-of date. Update the date or observed amounts.'
    return dict(**values, through=through.isoformat(), source=source, verification='User-supplied comparable total; not independently certified'), None


def _pac(industry, assumptions, cycle, current_cycle, as_of, b):
    ref = _reference(industry['history'], cycle)
    out = dict(status='needs_inputs', baseline_cycle=ref['cycle'], baseline_real_2025=ref['pac_real_2025'],
               baseline_growth_pct=ref['growth_pct'], ordinary_p=b['pac_model']['ordinary_p'],
               projected_cycle_real_2025=None, projected_remaining_real_2025=None,
               interval_kind='Ordinary coefficient uncertainty, holding the reference cycle effect fixed')
    growth = _number(assumptions.get('growth_pct'), 'Cycle revenue growth', -99.9, 500)
    if growth is None:
        return out | dict(reason='Enter expected two-year cycle revenue growth.')
    m = b['pac_model']; delta = (growth - ref['growth_pct']) / industry['growth_sd']
    log_change = m['beta'] * delta
    half = m['critical_95'] * m['ordinary_se'] * abs(delta)
    values = [ref['pac_real_2025'] * math.exp(x) for x in [log_change, log_change - half, log_change + half]]
    out.update(status='ready', projected_cycle_real_2025=values[0], cycle_coefficient_low=values[1], cycle_coefficient_high=values[2],
               change_pct=100 * math.expm1(log_change), growth_pct=growth,
               extrapolation=growth < industry['growth_min'] or growth > industry['growth_max'])
    if cycle == current_cycle:
        if as_of == date(cycle, 12, 31):
            return out | dict(remainder_status='cycle_closed', remainder_reason='No days remain after the cutoff in this cycle.')
        observed, reason = _recorded(assumptions.get('observed_pac'), as_of, ['amount_real_2025'], 'Observed PAC giving')
        out['remainder_status'] = 'needs_observed' if reason else 'ready'
        out['remainder_reason'] = reason
        if observed:
            remaining = values[0] - observed['amount_real_2025'];out['observed'] = observed
            if remaining < 0:
                out.update(remainder_status='baseline_exceeded', remainder_reason='Observed giving already exceeds the projected cycle total. Revise the scenario; no zero or negative future-giving forecast is substituted.', excess_observed_real_2025=-remaining)
            else:
                out.update(projected_remaining_real_2025=remaining, remaining_coefficient_low=values[1]-observed['amount_real_2025'], remaining_coefficient_high=values[2]-observed['amount_real_2025'])
    return out


def _share(industry, assumptions, cycle, current_cycle, as_of, b, model_id, prior):
    m = b['share_models'][model_id];chamber=m['chamber'];ref=_reference(m['history'], cycle, industry['id'])
    out=dict(status='needs_inputs', chamber=chamber, model_id=model_id, baseline_cycle=ref['cycle'], baseline_share=ref['share'],
             ordinary_p=m['ordinary_p'].get('Forecast'), coefficient_p_scope='Reference-industry forecast term' if m['industry_slopes'] else 'Pooled forecast term',
             baseline_candidate_total_real_2025=ref['candidate_total_real_2025'], projected_cycle_R_share=None, projected_remaining_R_share=None,
             specification=m['specification'], n=m['n'], training_cycles=m['training_cycles'])
    if m['industry_slopes']:
        row=next(r for r in b['industry_slopes'] if r['signal']==m['signal'] and r['chamber']==chamber and r['sector']==industry['id'])
        out.update(ordinary_p=row['ordinary_p'], coefficient_p_scope='Industry-specific forecast slope')
    majority=_number(_mapping(assumptions.get('current_control'),'Current control').get(chamber), chamber+' current control',0,1)
    if majority is not None and majority not in (0.,1.):raise ValueError('Current control must be Democratic (0) or Republican (1).')
    growth=_number(assumptions.get('growth_pct'),'Cycle revenue growth',-99.9,500)
    prior=_number(prior,'Prior cycle revenue growth',-99.9,500)
    political=_mapping(assumptions.get('control_scores' if m['signal']=='control' else 'seat_gains'),'Political expectations')
    forecast=_number(political.get(chamber),chamber+' forecast',0 if m['signal']=='control' else -435 if chamber=='House' else -100,100 if m['signal']=='control' or chamber=='Senate' else 435)
    if forecast is not None and m['signal']=='control':forecast/=100
    mu=industry['growth_mean'];sd=industry['growth_sd']
    target={'Intercept':1.,'RMajNow':majority,'Forecast':forecast,'G_current':None if growth is None else (growth-mu)/sd,'G_prior':None if prior is None else (prior-mu)/sd,'Pres':float(cycle%4==0)}
    base={'Intercept':1.,'RMajNow':ref['current_control'],'Forecast':ref['forecast'],'G_current':(ref['growth_pct']-mu)/sd,'G_prior':(ref['prior_growth_pct']-mu)/sd,'Pres':ref['pres']}
    for term in m['terms']:
        if term.startswith('industry:'):target[term]=base[term]=float(term.split(':',1)[1]==industry['id'])
        if term.startswith('Forecast:'):
            match=float(term.split(':',1)[1]==industry['id']);target[term]=None if forecast is None else forecast*match;base[term]=ref['forecast']*match
    missing=[term for term in m['terms'] if target.get(term) is None]
    if missing:return out | dict(reason='Enter '+', '.join({'RMajNow':chamber+' current-control assumption','Forecast':chamber+' '+('control score' if m['signal']=='control' else 'expected seat gain'),'G_current':'cycle growth','G_prior':'prior-cycle growth'}.get(k,k) for k in missing if not k.startswith('Forecast:'))+'.')
    delta=[target[k]-base[k] for k in m['terms']];effect,low,high=_interval(m,delta);share=ref['share']+effect
    political_delta=[delta[i] if term=='Forecast' or term.startswith('Forecast:') else 0. for i,term in enumerate(m['terms'])]
    pe,pl,ph=_interval(m,political_delta)
    # Materiality is directional: a large move away from the expected winner is
    # not evidence that giving follows expected power. These are associations.
    favored=1 if forecast>(.5 if m['signal']=='control' else 0) else -1 if forecast<(.5 if m['signal']=='control' else 0) else 0
    directed=pe*favored*100
    directed_low=min(pl*favored,ph*favored)*100
    out.update(political_change_pp=pe*100,political_low_pp=pl*100,political_high_pp=ph*100,
               baseline_forecast=ref['forecast'],forecast=forecast,
               expected_advantaged_party='R' if favored==1 else 'D' if favored==-1 else 'Neither',
               materiality=[dict(threshold_pp=t,toward_expected_party_pp=directed if favored else None,
                                point_meets=directed>=t if favored else None,
                                ordinary_interval_meets=directed_low>=t if favored else None) for t in (1,3,5)])
    # As in the PAC path, flag an input outside the range the model was fitted on: the model's own training rows
    # (growth: this industry's rows). Scores are entered x100, so allow float rounding at the ends of the range.
    fitted=[('Forecast',forecast,[r['forecast'] for r in m['history']]),
            ('G_current',growth,[r['growth_pct'] for r in m['history'] if r['industry']==industry['id']]),
            ('G_prior',prior,[r['prior_growth_pct'] for r in m['history'] if r['industry']==industry['id']])]
    extrapolation=any(x is not None and (x<min(xs)-1e-9 or x>max(xs)+1e-9) for term,x,xs in fitted if any(t==term or t.startswith(term+':') for t in m['terms']))
    out.update(change_pp=effect*100,cycle_coefficient_low=ref['share']+low,cycle_coefficient_high=ref['share']+high,
               raw_projected_cycle_R_share=share,ordinary_interval_kind='Coefficient interval for the modeled change; reference share held fixed',
               extrapolation=extrapolation,interval_outside_share_range=ref['share']+low<0 or ref['share']+high>1)
    if not 0<=share<=1:return out | dict(status='outside_share_range',reason='These assumptions put the linear share estimate outside 0–100%. Revise the scenario; the result is not clipped.')
    out.update(status='ready',projected_cycle_R_share=share)
    allocations=_mapping(assumptions.get('candidate_budgets'),'Candidate budgets');allocation=_mapping(allocations.get(chamber),chamber+' candidate budget')
    budget=_number(allocation.get('cycle_total_real_2025'),chamber+' candidate cycle budget',0,1e12)
    if budget is not None:
        out.update(candidate_budget_real_2025=budget,projected_cycle_R_real_2025=budget*share,projected_cycle_D_real_2025=budget*(1-share),budget_basis='User-specified two-party candidate budget; independent of PAC totals')
    if cycle==current_cycle:
        if as_of == date(cycle, 12, 31):
            return out | dict(remainder_status='cycle_closed', remainder_reason='No days remain after the cutoff in this cycle.')
        obs,reason=_recorded(allocation,as_of,['observed_R_real_2025','observed_D_real_2025'],chamber+' candidate giving')
        if budget is None:reason='Add a comparable two-party cycle budget and observed D/R amounts for a remainder allocation.'
        out.update(remainder_status='needs_budget_and_observed' if reason else 'ready',remainder_reason=reason)
        if reason is None:
            r=budget*share-obs['observed_R_real_2025'];d=budget*(1-share)-obs['observed_D_real_2025'];out['observed']=obs
            if r<0 or d<0 or r+d<=0:out.update(remainder_status='budget_inconsistent',remainder_reason='The budget/share scenario is incompatible with giving already observed. Revise the scenario; no clipping is applied.')
            else:out.update(projected_remaining_R_share=r/(r+d),projected_remaining_R_real_2025=r,projected_remaining_D_real_2025=d)
    return out


def project(request):
    request=_mapping(request,'Forecast request');b=models()
    if b['schema_version']==2:return _project_v2(request,b)
    sector=request.get('industry')
    if not isinstance(sector,str) or sector not in b['industries']:
        raise ValueError('Choose a supported research industry. Company and subindustry models are not interchangeable.')
    industry=b['industries'][sector];as_of=_date(request.get('as_of') or date.today().isoformat(),'As-of date')
    if as_of>date.today():raise ValueError('The as-of date cannot be in the future.')
    if as_of<_model_vintage(b):raise ValueError('The model package was not available at that historical cutoff.')
    current=as_of.year+as_of.year%2;signal=request.get('signal','control');varying=request.get('industry_slopes',False)
    if signal not in ('control','seats'):raise ValueError('Choose control or seats; they are alternative models.')
    if not isinstance(varying,bool):raise ValueError('Industry slopes must be true or false.')
    note=_text(request.get('assumption_source'),'Assumption source') or 'User scenario assumptions; not an automatically updated live forecast'
    known=_date(request.get('assumption_date') or as_of.isoformat(),'Assumption date')
    if known>as_of:raise ValueError('Assumptions cannot be dated after the forecast cutoff.')
    horizons=_mapping(request.get('horizons'),'Horizon assumptions');allowed={str(current),str(current+2)}
    if set(horizons)-allowed:raise ValueError('Only the current cycle remainder and next complete cycle are supported.')
    output=[]
    for cycle in [current,current+2]:
        a=_mapping(horizons.get(str(cycle)),str(cycle)+' assumptions')
        prior=a.get('prior_growth_pct')
        if prior is None or prior=='':
            history=next((r for r in industry['history'] if r['cycle']==cycle-2),None)
            prior=history['growth_pct'] if history else _mapping(horizons.get(str(cycle-2)),'Previous horizon').get('growth_pct')
        prior=_number(prior,'Prior cycle revenue growth',-99.9,500)
        result=dict(cycle=cycle,period=_period(cycle),horizon='current_remainder' if cycle==current else 'next_complete',
                    starts=((as_of+timedelta(days=1)).isoformat() if as_of < date(cycle,12,31) else None) if cycle==current else f'{cycle-1}-01-01',ends=f'{cycle}-12-31',prior_growth_pct=prior,
                    pac=_pac(industry,a,cycle,current,as_of,b),shares={})
        for chamber in CHAMBERS:
            ident=signal+'_'+chamber+('_industry' if varying else '')
            result['shares'][chamber]=_share(industry,a,cycle,current,as_of,b,ident,prior)
        result['lobbying']=[];changes=_mapping(a.get('annual_forecast_changes'),'Annual growth-forecast changes');m=b['lobby_model']
        for year in range(as_of.year if cycle==current else cycle-1,cycle+1):
            value=_number(changes.get(str(year)),'Annual forecast-growth change',-100,100)
            lobby=dict(year=year,status='needs_inputs',ordinary_p=m['ordinary_p'],amount_real_2025=None,
                       scope='Annual lobbying-change sensitivity; not a remaining-cycle spending forecast')
            if value is not None:
                # + 0. turns the -0.0 that a zero change times the negative slope gives into 0.0.
                point=m['beta_million']*value*1e6+0.;half=m['critical_95']*m['ordinary_se_million']*abs(value)*1e6
                lobby.update(status='ready',amount_real_2025=point,coefficient_low=point-half,coefficient_high=point+half,forecast_change_pp=value)
            result['lobbying'].append(lobby)
        output.append(result)
    _finite(request,'Forecast request')   # `inputs` echoes the request; NaN/Infinity would make the response unreadable JSON
    return dict(status='ready',product='Conditional industry scenarios',model_version=b['model_version'],model_sha256=b['sha256'],
                industry=sector,industry_label=industry['label'],industry_code=industry['code'],scope=industry['scope'],currency=b['currency'],
                as_of=as_of.isoformat(),assumption_source=note,assumption_date=known.isoformat(),signal=signal,industry_slopes=varying,
                horizons=output,inputs=request,inference=b['inference'],interval_label='Ordinary 95% coefficient interval; not a prediction interval',
                notices=['Historical baselines and conditional associations are not validated future-giving predictions.',
                         'PAC amounts, combined PAC/individual candidate shares and lobbying are separate accounts and must not be added.',
                         'Current-cycle shares describe the full cycle unless comparable observed amounts and a candidate budget support a remaining allocation.',
                         'The reference cycle/year effect is held fixed; source-vintage and future-period shocks are outside the coefficient intervals.'])


# ----------------------------------------------------------------------------------------------------------------------
# Schema 2: Mentat-anchored projections (owner rulings of 2026-09-30)
#
# The models are estimated on OpenSecrets whole-industry data; they only supply the RELATIONSHIPS. Every level comes from Mentat's own
# anchor snapshot (mentat/forecast/anchors): % changes (PAC, lobbying) are carried over from Mentat's previous cycle (or, for lobbying,
# latest complete year); pp changes (shares) from the anchor the bundle records per industry x chamber (share_models.*.anchors: the
# previous cycle, or the cycle of the same phase). The snapshot is a separate file from the model bundle; the calculator refuses an
# industry whose snapshot workbook sha no longer matches the workbook published now (status anchor_stale).
# ----------------------------------------------------------------------------------------------------------------------
SLOPE_LABELS = {'R0': 'pooled slope', 'R1': 'industry-specific slope', 'R2': 'partially pooled slope'}
PAC_MEASURES = (('pac_to_candidates', 'Roster companies\' PACs: own giving to House and Senate candidates'),
                ('general_giving', 'Mentat general giving (all channels), excluding receipts Mentat marks non-giving (FEC interest, refunds, insurance claims, loans)'))
SHARE_BASES = (('employee_candidate', 'Employees\' candidate giving'), ('pac_candidate', 'Roster PACs\' candidate giving'))
LOBBY_SERIES = (('income_total', 'Outside-firm fees'), ('expenses_total', 'In-house expenses'))
SUPPORTED_CONTRACT = dict(pac_measures={'pac_to_candidates', 'general_giving'}, share_bases={'employee_candidate', 'pac_candidate'},
                          lobbying_series={'income_total', 'expenses_total'}, share_anchors={'previous_cycle', 'same_phase'})
# The share anchors this calculator implements (owner ruling 2026-09-30), as anchor_contract.share.anchor_options must declare them.
# previous_cycle: Mentat cycle A = open cycle - 2 for both horizons, the next cycle chained through the open one (steps (cycle - A) / 2).
# same_phase: Mentat cycle A = target cycle - 4, one step each (both anchors are observed Mentat cycles).
SHARE_ANCHORS = {'previous_cycle': dict(cycle_offset=2, chained=True, label='previous cycle'),
                 'same_phase': dict(cycle_offset=4, chained=False, label='same phase')}
ANCHOR_FILE = Path(__file__).resolve().parents[1] / 'state' / 'giving-anchors' / 'giving-anchors-v1.json'
CONTRACT_DIR = Path(__file__).resolve().parents[1] / 'forecast' / 'contracts'
SEATS_TEXT = 'Seat forecasts are evidence only ("no signal"); the calculator uses expected control.'
# Under the cycle model `growth_pct` is the AVERAGE ANNUAL IBIS-window growth, not the anchor cycle's two-year revenue growth it used to be; the page says which it means.
GROWTH_BASIS = 'ibis_window_avg'
STALE_PAGE_TEXT = 'This page is out of date. Reload the page to use the current growth inputs.'


@lru_cache(maxsize=1)
def _contract():
    """The shared contract module, loaded by path so no other module called `contract` on sys.path can stand in for it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('giving_forecast_contract', CONTRACT_DIR / 'contract.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def anchor_path():
    override = os.environ.get('MENTAT_GIVING_ANCHORS', '').strip()
    return Path(override) if override else ANCHOR_FILE


class AnchorProblem(Exception):
    """The anchor snapshot cannot be used: `status` is what the response reports, `args[0]` the reason."""

    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status


@lru_cache(maxsize=4)
def _load_anchors(path, stamp, size):
    try:
        with Path(path).open(encoding='utf-8-sig') as stream:
            snapshot = json.load(stream)
    except (OSError, ValueError):
        raise AnchorProblem('anchor_unavailable', 'The Mentat anchor snapshot could not be read. Rebuild it (mentat/forecast/anchors/build_giving_anchors.py).') from None
    contract = _contract()
    if contract.validate_kind(snapshot, 'anchors') or contract.check_anchor_invariants(snapshot):
        raise AnchorProblem('anchor_unavailable', 'The Mentat anchor snapshot does not satisfy its contract. Rebuild it (mentat/forecast/anchors/build_giving_anchors.py).')
    return snapshot


def anchors():
    path = anchor_path()
    try:
        stat = path.stat()
    except OSError:
        raise AnchorProblem('anchor_unavailable', 'No Mentat anchor snapshot exists yet. Run mentat/forecast/anchors/build_giving_anchors.py.') from None
    return _load_anchors(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=64)
def _hash_stamp(stamp):
    digest = hashlib.sha256()
    with open(stamp[0], 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 22), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _published_workbook_sha256(name):
    """sha256 of the workbook published now under this file name in the authoritative report directory; None when it is not there."""
    import sys
    tools = str(Path(__file__).resolve().parents[1] / 'tools')
    if tools not in sys.path:
        sys.path.insert(0, tools)
    from report_directory import report_directory
    try:
        path = (report_directory() / name).resolve()
        stat = path.stat()
        return _hash_stamp((str(path), stat.st_size, stat.st_mtime_ns))
    except (OSError, ValueError):
        return None


def _anchor_industry(industry_id):
    """(snapshot, this industry's anchors) or an AnchorProblem naming why the industry cannot be anchored now."""
    snapshot = anchors()
    entry = snapshot['industries'].get(industry_id)
    if entry is None:
        raise AnchorProblem('anchor_unavailable', snapshot['unavailable'].get(industry_id) or 'The anchor snapshot has no entry for this industry.')
    published = _published_workbook_sha256(entry['mentat']['workbook'])
    if published is None:
        raise AnchorProblem('anchor_unavailable', 'The published ' + entry['mentat']['workbook'] + ' is not readable, so the anchors cannot be checked against it.')
    if str(entry['coverage']['current_cycle'] - 2) not in snapshot['cpi']['multipliers']:
        raise AnchorProblem('anchor_unavailable', 'The anchor snapshot has no CPI multiplier for the ' + _period(entry['coverage']['current_cycle'] - 2) +
                            ' anchor cycle, so its levels cannot be put in 2025 dollars. Rebuild the anchors with a CPI file that covers it.')
    if published != entry['mentat']['workbook_sha256']:
        raise AnchorProblem('anchor_stale', 'The Mentat anchors were built from an earlier version of ' + entry['mentat']['workbook'] +
                            ' (anchor sha256 ' + entry['mentat']['workbook_sha256'][:8] + ', published ' + published[:8] + '). Rebuild the anchors.')
    return snapshot, entry


def _v2_contract_problems(b):
    c = b['anchor_contract']
    problems = []
    if {m['id'] for m in c['pac']['measures']} != SUPPORTED_CONTRACT['pac_measures']:
        problems.append('PAC measures')
    if {m['id'] for m in c['share']['bases']} != SUPPORTED_CONTRACT['share_bases']:
        problems.append('share bases')
    if {m['id'] for m in c['lobbying']['series']} != SUPPORTED_CONTRACT['lobbying_series']:
        problems.append('lobbying series')
    options = c['share']['anchor_options']
    ids = [o['id'] for o in options]
    if set(ids) != SUPPORTED_CONTRACT['share_anchors'] or len(ids) != len(set(ids)):
        problems.append('share anchor options (the console implements previous_cycle and same_phase)')
    elif any((o['cycle_offset'], o['chained']) != (SHARE_ANCHORS[o['id']]['cycle_offset'], SHARE_ANCHORS[o['id']]['chained']) for o in options):
        problems.append('share anchor offsets (the console implements previous_cycle: 2, chained; same_phase: 4, one step)')
    for model_id, model in b['share_models'].items():
        if 'Forecast' not in model['terms']:
            problems.append(model_id + ' has no Forecast term')
    if b.get('cycle_model') is not None and 'Pres' not in b['share_models'].get('control_Senate', {}).get('terms', []):
        problems.append('control_Senate has no Pres term (owner ruling 2026-10-07: the Senate share model keeps the presidential-cycle term)')
    return problems


# Owner ruling 2026-10-09 (Paul, option A: "keep it uniform"): Telecom's House / Senate party split is scored on the POOLED share models every industry uses (control_House, control_Senate),
# not withheld. The bundle withholds it only because Telecom has no share-model rows of its own (the refit captures no B09 recipients). The models are applied as a change from the
# anchor cycle's inputs (_v2_share_effect), and the sector terms are 0 in that change, so Telecom takes the reference level of the coding (all industry dummies 0; the refit's design, rf_stepwise.R
# rf_matrices, drops the alphabetically first sector, auto_light_duty_manufacturing) and its own level is its own Mentat anchor share. The residual variance is the pooled one, which the
# bundle records identically for every industry. Nothing else of the bundle changes.
POOLED_SHARE_SCORED = ('telecom',)
POOLED_SHARE_NOTE = ('Scored on the pooled share model: this industry has no share-model rows of its own, so it takes the pooled slopes and residual variance and the reference level of the sector term '
                     '(its own level is its Mentat share of the anchor cycle).')


def _pooled_scored(b, row):
    """`row` as the console serves it: an industry in POOLED_SHARE_SCORED that the bundle withholds the chamber split from only for want of share-model rows has the control capability on
    and no withheld reason, marked share_scoring 'pooled'. Any other row, and a bundle whose share models are not pooled, is returned as it is."""
    models = [b['share_models'].get('control_' + chamber) for chamber in CHAMBERS]
    if (row['id'] not in POOLED_SHARE_SCORED or row['capabilities']['control'] or not row['capabilities']['pac'] or row.get('lobbying_only')
            or not all(m and m['application'] == 'pooled' for m in models)):
        return row
    return dict(row, capabilities=dict(row['capabilities'], control=True), withheld={k: v for k, v in row['withheld'].items() if k != 'control'}, share_scoring='pooled')


def _pooled_step(m):
    """The pooled model's anchored-step residual (the previous-cycle step) for an industry with no step of its own: the one step the bundle records for every industry, or None when they differ."""
    steps = {json.dumps(e['prediction']['previous_cycle'], sort_keys=True) for e in m['anchors'].values()}
    return json.loads(steps.pop()) if len(steps) == 1 else None


def _fitted_growth(b, m, ind, key):
    """The growth values (%) the share model was fitted on, in this industry's units: its own rows, or, with none, the pooled rows' standardised growth put back on this industry's mean and sd."""
    own = [r[key] for r in m['history'] if r['industry'] == ind['id']]
    if own:
        return own
    z = lambda r: (r[key] - b['industries'][r['industry']]['growth_mean']) / b['industries'][r['industry']]['growth_sd']
    return [ind['growth_mean'] + ind['growth_sd'] * z(r) for r in m['history']]


def _v2_offered(b):
    """(offered industries, not offered): an industry without Mentat data is not displayed; it stays in the estimation samples."""
    offered, not_offered = [], []
    for ident, row in b['industries'].items():
        if row['calculator'] and row['mentat_key']:
            offered.append(_pooled_scored(b, row))
        else:
            reason = 'No Mentat data for this industry; it stays in the estimation samples but is not displayed.' if not row['mentat_key'] else 'Not offered by the model package.'
            not_offered.append(dict(id=ident, label=row['label'], reason=reason))
    return offered, not_offered


def _lda_history(lda):
    """Melange's own LDA levels as the lobbying card draws them (the same sector_spend rows the anchors read): annual rows {year, hired_firms, in_house, orgs, lobbyists}
    (hired_firms = income_total, in_house = expenses_total, never added here), the partial year with the quarter it runs through, and the source sha256. None when the
    industry has no Melange-LDA sector. orgs and lobbyists are None for a snapshot built before the counts were carried."""
    if lda['status'] != 'ready':
        return None
    fees, house, counts = lda['series']['income_total'], lda['series']['expenses_total'], lda.get('counts') or {}

    def row(year, hired, inhouse):
        c = counts.get(str(year)) or {}
        return dict(year=year, hired_firms=hired, in_house=inhouse, orgs=c.get('organizations'), lobbyists=c.get('lobbyists'))
    part, hpart = fees['partial'], house['partial']
    return dict(sector=lda['sector'], source_file=lda['source_file'], source_sha256=lda['source_sha256'], through=lda['through'],
                annual=[row(int(year), value, house['annual'][year]) for year, value in sorted(fees['annual'].items())],
                partial=None if part is None or hpart is None else dict(row(part['year'], part['total'], hpart['total']), through_quarter=part['through_quarter']))


@lru_cache(maxsize=8)
def _workbook_cycle_totals(path, mtime_ns, size):
    """{'2023-2024': total} from the 'Total Giving' rows (column B) of a Mentat workbook's 'Spending Overview' sheet (column A the cycle, column C its total, as cached in the file).
    Read straight from the zip: that one small sheet and the shared strings its first rows point at, never the whole workbook (the 60 MB ones take 10 s through openpyxl)."""
    main, rel = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}', '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
    with zipfile.ZipFile(path) as book:
        rid = next(s.get(rel + 'id') for s in ET.fromstring(book.read('xl/workbook.xml')).iter(main + 'sheet') if s.get('name') == 'Spending Overview')
        target = next(r.get('Target') for r in ET.fromstring(book.read('xl/_rels/workbook.xml.rels')) if r.get('Id') == rid).lstrip('/')
        cells, wanted = {}, set()            # 'A6' -> ('s', shared string index) | ('t', text) | ('n', number text)
        with book.open(target if target.startswith('xl/') else 'xl/' + target) as sheet:
            for _event, row in ET.iterparse(sheet):
                if row.tag != main + 'row':
                    continue
                if int(row.get('r')) > 200:
                    break
                for c in row.iter(main + 'c'):
                    ref, kind, v = c.get('r'), c.get('t'), c.find(main + 'v')
                    if ref[0] not in 'ABC' or ref[1:] != row.get('r'):
                        continue
                    if kind == 'inlineStr':
                        cells[ref] = ('t', ''.join(t.text or '' for t in c.iter(main + 't')))
                    elif v is not None and v.text is not None and kind in (None, 'n', 's', 'str'):
                        cells[ref] = ({'s': 's', 'str': 't'}.get(kind, 'n'), v.text)
                        if kind == 's':
                            wanted.add(int(v.text))
                row.clear()
        strings = {}
        if wanted:
            with book.open('xl/sharedStrings.xml') as stream:
                index = 0
                for _event, si in ET.iterparse(stream):
                    if si.tag != main + 'si':
                        continue
                    if index in wanted:
                        strings[index] = ''.join(t.text or '' for t in si.iter(main + 't'))
                    si.clear()
                    index += 1
                    if index > max(wanted):
                        break

    def text(ref):
        kind, raw = cells.get(ref, (None, None))
        return strings.get(int(raw)) if kind == 's' else raw if kind == 't' else None
    out = {}
    for n in range(1, 201):
        cycle, total = text('A%d' % n), cells.get('C%d' % n)
        if text('B%d' % n) == 'Total Giving' and re.fullmatch(r'\d{4}-\d{4}', cycle or '') and total and total[0] == 'n':
            out[cycle] = float(total[1])
    return out


def _own_cycle_ratio(totals, dataset):
    """The industry's own presidential-cycle step from its audited workbook totals ({'2023-2024': total, ...}): the latest presidential cycle (it ends in a year divisible by
    four) over the midterm cycle before it. None unless both totals exist and the midterm one is positive. Display only: the forecast takes the owner rule (D-121)."""
    ends = {int(label[-4:]): label for label in totals}
    presidential = [end for end in ends if end % 4 == 0]
    if not presidential or max(presidential) - 2 not in ends:
        return None
    high, low = ends[max(presidential)], ends[max(presidential) - 2]
    if not totals[low] > 0:
        return None
    return dict(dataset=dataset, presidential_cycle=high, presidential_total=totals[high], midterm_cycle=low, midterm_total=totals[low], ratio=totals[high] / totals[low])


def _industry_cycle_ratio(entry):
    """own_cycle_ratio of one anchored industry, read from the published workbook the anchors cite; None when that workbook or its rows cannot be read."""
    import sys
    tools = str(Path(__file__).resolve().parents[1] / 'tools')
    if tools not in sys.path:
        sys.path.insert(0, tools)
    from report_directory import report_directory
    try:
        path = (report_directory() / entry['mentat']['workbook']).resolve()
        stat = path.stat()
        return _own_cycle_ratio(_workbook_cycle_totals(str(path), stat.st_mtime_ns, stat.st_size), entry['mentat']['dataset'])
    except (OSError, ValueError, StopIteration, KeyError, IndexError, TypeError, AttributeError, EOFError, zlib.error, zipfile.BadZipFile, ET.ParseError):
        # An odd sheet row (a cell or row with no 'r', an empty one, a cut-off zip stream) is a note left out, never a catalog that fails: _anchors_status catches only AnchorProblem.
        return None


def _anchors_status(b, offered):
    """What the catalog says about the anchors, per offered industry, without failing the catalog when they are missing or stale."""
    try:
        snapshot = anchors()
    except AnchorProblem as problem:
        return dict(status=problem.status, reason=str(problem), built_at_utc=None, industries={})
    out = {}
    for row in offered:
        try:
            _snap, entry = _anchor_industry(row['id'])
            out[row['id']] = dict(status='ready', workbook=entry['mentat']['workbook'], collection_date=entry['coverage']['collection_date'],
                                  coverage_end=entry['coverage']['coverage_end'], current_cycle=entry['coverage']['current_cycle'], flags=entry['coverage']['flags'] + entry['flags'],
                                  measures=entry['status'], lda_history=_lda_history(entry['lda']))
            ratio = _industry_cycle_ratio(entry)          # display only; absent (not null) when the workbook's two totals cannot be read
            if ratio:
                out[row['id']]['own_cycle_ratio'] = ratio
        except AnchorProblem as problem:
            out[row['id']] = dict(status=problem.status, reason=str(problem))
    return dict(status='ready' if out and all(v['status'] == 'ready' for v in out.values()) else 'partial', reason=None,
                built_at_utc=snapshot['built_at_utc'], industries=out)


# --- owner waivers shown on the card ---------------------------------------------------------------------------------------------
# The refit's hard backtest gate (G6) lets the owner waive one cell by name (mentat/forecast/refit/registry/gate_waivers.json). A waiver the registry
# marks show_warning is disclosed on the forecast card. The registry says WHICH cell was waived, by whom, when and why, and records the error of the
# calculator the refit was compared with (`measured`); the error of the model in force comes from the active bundle itself, so the card never states a
# figure that is not the one being served, and a waiver whose degradation no longer stands is not shown. Display only: no gate, no projection and no
# number of the model reads any of this.
WAIVER_FILE = Path(__file__).resolve().parents[1] / 'forecast' / 'refit' / 'registry' / 'gate_waivers.json'
WAIVER_SLACK = 1e-9                  # the refit's own G6 tolerance (RF_G6_SLACK): a difference below it is no degradation
WAIVER_LABELS = {'control_House': 'House share', 'control_Senate': 'Senate share', 'pac': 'PAC giving', 'lobby': 'Lobbying'}


def waiver_path():
    override = os.environ.get('MENTAT_GIVING_WAIVERS', '').strip()
    return Path(override) if override else WAIVER_FILE


@lru_cache(maxsize=4)
def _load_waivers(path, stamp, size):
    """The registry's waiver entries (plain objects), or none when the file is unreadable or is not a registry: the catalog never fails on them."""
    try:
        with Path(path).open(encoding='utf-8-sig') as stream:
            registry = json.load(stream)
    except (OSError, ValueError, RecursionError):                      # a registry nested beyond the parser's recursion limit is unreadable like any other
        return ()
    entries = registry.get('waivers') if isinstance(registry, dict) else None
    return tuple(w for w in entries if isinstance(w, dict)) if isinstance(entries, list) else ()


def _waiver_text(value):
    """A text field of a registry entry as the response carries it: a string, or None. Anything else (a number such as NaN, which Python's json reads but a
    browser's JSON.parse rejects, or an object) never reaches the response."""
    return value if isinstance(value, str) else None


def _figure(value):
    """A registry figure as a finite float, or None (a bool, text, NaN, an infinity, or an integer too large for a float is no figure)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _share_error_in_force(b, term, industry):
    """The active bundle's own leave-one-election-out error (pp) of the anchor its calculator uses for one share model x industry; None when it records none."""
    entry = (b['share_models'].get(term) or {}).get('anchors', {}).get(industry)
    try:
        return _figure(entry['backtest'][entry['anchor']]['loeo_mae'])
    except (TypeError, KeyError):
        return None


def _v2_waivers(b, offered):
    """The waivers the card shows: registry entries for a G6 cell of an industry this console offers, with show_warning true, each beside the error of
    the model in force. [] when the registry cannot be read."""
    path = waiver_path()
    try:
        stat = path.stat()
    except OSError:
        return []
    labels = {row['id']: row['label'] for row in offered}
    shown = []
    for w in _load_waivers(str(path), stat.st_mtime_ns, stat.st_size):
        term, industry, cell = w.get('term'), w.get('industry'), w.get('cell')
        if (w.get('show_warning') is not True or w.get('gate', 'G6') != 'G6' or not isinstance(industry, str) or industry not in labels
                or not isinstance(term, str) or not isinstance(cell, str)):
            continue                 # (a list or an object as the industry is not hashable: its type is checked before the membership test)
        measured = w['measured'] if isinstance(w.get('measured'), dict) else {}
        comparator = _figure(measured.get('comparator_value'))
        in_force = _share_error_in_force(b, term, industry) if term in b['share_models'] else None
        if comparator is not None and in_force is not None and in_force <= comparator + WAIVER_SLACK:
            continue                 # the model in force is no longer worse than the calculator it was compared with: nothing is waived any more
        ruled = w.get('date') if isinstance(w.get('date'), str) else None
        text = WAIVER_LABELS.get(term, term) + ' gate waived by owner' + (' on ' + ruled if ruled else '')
        if comparator is not None and in_force is not None:
            text += ': backtest error %.3f -> %.3f pp' % (comparator, in_force)
        shown.append(dict(gate='G6', cell=cell, term=term, model_id=term if term in b['share_models'] else None, industry=industry, industry_label=labels[industry],
                          chamber=term.split('_', 1)[1] if term in ('control_House', 'control_Senate') else None,
                          metric=_waiver_text(measured.get('metric')), unit=_waiver_text(measured.get('unit')), comparator_label=_waiver_text(measured.get('comparator')),
                          comparator_value=comparator, published_value=in_force, owner=_waiver_text(w.get('owner')), date=ruled, reason=_waiver_text(w.get('reason')),
                          show_warning=True, source=_waiver_text(measured.get('source')), display_text=text))
    return shown


def _catalog_v2(b, today):
    cycle = today.year + today.year % 2
    offered, not_offered = _v2_offered(b)
    return dict(schema_version=2, model_version=b['model_version'], model_sha256=b['sha256'], built_on=_model_vintage(b).isoformat(), as_of=today.isoformat(),
                current_cycle=cycle, next_cycle=cycle + 2, currency=b['currency'], industries=offered, not_offered=not_offered,
                share_models=b['share_models'], pac_model=b['pac_model'], lobby_model=b['lobby_model'], evidence=b['evidence'],
                evidence_models={k: {f: v[f] for f in ('id', 'signal', 'chamber', 'specification', 'n', 'ordinary_p', 'training_cycles', 'calculator')} for k, v in b['evidence_models'].items()},
                industry_slopes=b['industry_slopes'], validation_metrics=b['validation_metrics'], backtests=b['backtests'], sources=b['sources'],
                saved_outlook=b['saved_outlook'], political_inputs=b['political_inputs'], anchor_contract=b['anchor_contract'],
                interval_labels=b['interval_labels'], inference=b['inference'], validation=b['validation'], anchors=_anchors_status(b, offered),
                # Owner waivers of the refit gates that the card shows (refit/registry/gate_waivers.json, show_warning true), each beside the model in force.
                waivers=_v2_waivers(b, offered),
                # The cycle-aware model (bundle `cycle_model`, owner rulings 2026-10-07): defaults of the one growth input per horizon, the layers and the Methods notes.
                **({'cycle_model_view': _cycle_view(b, cycle, offered)} if b.get('cycle_model') else {}),
                scope='Mentat levels for the industry\'s tracked companies (the published Mentat report scope); the models only supply the relationships. '
                      'Report company, donor-role and subindustry filters do not apply.',
                interval_label=b['interval_labels']['coefficient'] + '; ' + b['interval_labels']['prediction'])


# --- arithmetic ----------------------------------------------------------------------------------------------------------
def _quad(cov, vec):
    return sum(vec[i] * cov[i][j] * vec[j] for i in range(len(vec)) for j in range(len(vec)))


def _half(crit, variance):
    return crit * math.sqrt(max(0., variance))


def _effective_cut(cuts, as_of, coverage_end):
    """The latest observed cut on or before min(as_of, coverage end): remainder accepts coverage on or before the as-of date and starts the day after."""
    limit = min(as_of.isoformat(), coverage_end)
    best = None
    for cut in cuts:
        if cut['through'] <= limit:
            best = cut
    return best


def _remainder(projected, bounds, observed):
    """P_T - observed, and the same subtracted from each bound; a negative point is a conflict, never a zero forecast."""
    remaining = projected - observed
    if remaining < 0:
        return dict(remainder_status='baseline_exceeded', projected_remaining=None, excess_observed=-remaining,
                    remainder_reason='Observed giving already exceeds the projected cycle total. Revise the scenario; no zero or negative future-giving forecast is substituted.')
    return dict(remainder_status='ready', projected_remaining=remaining, remainder_reason=None,
                **{k: v - observed for k, v in bounds.items()})


def _window(ctx, cycle):
    """(status, reason, cut-day lookup) of the remainder window of a horizon: only the open Mentat cycle has observed amounts."""
    as_of, current, open_cycle = ctx['as_of'], ctx['current'], ctx['open_cycle']
    if cycle != current:
        return None, None
    if as_of >= date(cycle, 12, 31):
        return 'cycle_closed', 'No days remain after the cutoff in this cycle.'
    if cycle != open_cycle:
        return 'no_observed', 'The anchor snapshot observes the ' + _period(open_cycle) + ' cycle, not ' + _period(cycle) + '.'
    return 'window', None


def _cut_through(ctx):
    """The observed cut every measure of this industry shares on the as-of date (the first ready measure names it)."""
    for key in ('general_giving', 'candidate_receipts', 'pac_to_candidates'):
        block = ctx['snap'][key]
        if block['status'] == 'ready':
            cut = _effective_cut(block['observed'], ctx['as_of'], ctx['coverage_end'])
            if cut:
                return cut['through']
    return None


def _next_day(iso):
    return (date.fromisoformat(iso) + timedelta(days=1)).isoformat()


# --- the predictive conditioned on the giving already observed -------------------------------------------------------------------
# The full-cycle prediction interval is point x exp(+/- crit_df x scale) on the log scale: a scaled Student t with the anchored-step
# degrees of freedom. Giving already observed is a fact, so the predictive of the full cycle is that distribution conditioned on
# full >= observed (truncated at observed). Its quantiles never fall below observed and the remainder's never below zero; nothing about
# the slopes, the anchors or the residual variance changes. Pure stdlib (the console has no scipy): the t CDF through the regularized
# incomplete beta (continued fraction, modified Lentz) and the quantile by bisection.
def _betacf(a, b, x):
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1., a - 1.
    c, d = 1., 1. - qab * x / qap
    d = 1. / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1. + aa * d
        d = 1. / (d if abs(d) > tiny else tiny)
        c = 1. + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1. + aa * d
        d = 1. / (d if abs(d) > tiny else tiny)
        c = 1. + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.) < 1e-15:
            break
    return h


def _betainc(a, b, x, y):
    """Regularized incomplete beta I_x(a, b), with y = 1 - x passed exactly (no cancellation near either end)."""
    if x <= 0.:
        return 0.
    if y <= 0.:
        return 1.
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(y))
    return front * _betacf(a, b, x) / a if x < (a + 1.) / (a + b + 2.) else 1. - front * _betacf(b, a, y) / b


def _t_upper(z, df):
    """P(T_df > z)."""
    tail = .5 * _betainc(df / 2., .5, df / (df + z * z), z * z / (df + z * z))
    return tail if z >= 0 else 1. - tail


def _t_upper_inverse(u, df):
    """z with P(T_df > z) = u, 0 < u < 1 (bisection; the upper tail keeps tiny probabilities exact)."""
    lo, hi = -1e4, 1e4
    for _ in range(200):
        mid = .5 * (lo + hi)
        if _t_upper(mid, df) > u:
            lo = mid
        else:
            hi = mid
    return .5 * (lo + hi)


def _given_observed(point, half, crit, df, observed):
    """Quantiles (2.5%, 50%, 97.5%) of the full-cycle predictive conditioned on full >= observed, and of the remainder (full - observed),
    for the interval point x exp(+/- half) whose half-width is crit x scale. None when the inputs cannot define it."""
    if not (point > 0 and half > 0 and crit > 0 and df >= 1 and observed >= 0):
        return None
    scale = half / crit
    above = 1. if observed == 0 else _t_upper((math.log(observed) - math.log(point)) / scale, df)     # P(full >= observed)
    if not above > 1e-12:
        return None
    z = {k: _t_upper_inverse((1. - k) * above, df) for k in (.025, .5, .975)}
    if any(abs(v) > 9e3 for v in z.values()):
        return None
    q = {k: point * math.exp(scale * v) for k, v in z.items()}
    return dict(prob_full_below_observed=1. - above, cycle_given_observed_low=q[.025], cycle_given_observed_median=q[.5],
                cycle_given_observed_high=q[.975], remaining_given_observed_low=q[.025] - observed,
                remaining_given_observed_median=q[.5] - observed, remaining_given_observed_high=q[.975] - observed,
                given_observed_kind='95% prediction interval conditioned on the giving already observed (the full cycle cannot fall below it)')


# --- PAC ------------------------------------------------------------------------------------------------------------------
def _v2_pac(ctx, assumptions, cycle):
    b, ind = ctx['b'], ctx['industry']
    k = (cycle - ctx['anchor_cycle']) // 2
    out = dict(status='needs_inputs', anchor_cycle=ctx['anchor_cycle'], steps=k, chained=k > 1, measures=[], slope=None, ordinary_p=None,
               interval_kind='Coefficient interval for the slope; prediction interval adds the residual variance of ' + ('the anchored step' if k == 1 else str(k) + ' chained steps'))
    if not ind['capabilities']['pac']:
        return out | dict(status='withheld', reason=ind['withheld'].get('pac') or 'The PAC model is withheld for this industry.')
    row = b['pac_model']['slopes'].get(ind['id'])
    step = b['pac_model']['prediction']['industries'].get(ind['id'])
    ref = next((r for r in ind['history'] if r['cycle'] == ctx['anchor_cycle']), None)
    if row is None or step is None or ref is None:
        return out | dict(status='withheld', reason='The model package has no slope, residual variance or growth history for the anchor cycle of this industry.')
    slope = row['applied']
    out.update(slope=dict(rule=slope['rule'], label=SLOPE_LABELS[slope['rule']], beta=slope['beta'], se=slope['se'], df=slope['df']),
               ordinary_p=slope['ordinary_p'], baseline_growth_pct=ref['growth_pct'])
    growth = _number(assumptions.get('growth_pct'), 'Cycle revenue growth', -99.9, 500)
    if growth is None:
        return out | dict(reason='Enter expected two-year cycle revenue growth.')
    delta = (growth - ref['growth_pct']) / ind['growth_sd']
    point = slope['beta'] * delta
    coef_half = slope['critical_95'] * slope['se'] * abs(delta)
    pred_half = _half(step['critical_95'], delta ** 2 * slope['se'] ** 2 + k * step['sigma2'])
    out.update(status='ready', growth_pct=growth, change_pct=100 * math.expm1(point),
               coefficient_interval_pct=[100 * math.expm1(point - coef_half), 100 * math.expm1(point + coef_half)],
               prediction_interval_pct=[100 * math.expm1(point - pred_half), 100 * math.expm1(point + pred_half)],
               extrapolation=growth < ind['growth_min'] or growth > ind['growth_max'])
    wstatus, wreason = _window(ctx, cycle)
    for mid, label in PAC_MEASURES:
        block = ctx['snap'][mid]
        measure = dict(id=mid, label=label, status='ready', flags=[])
        if block['status'] != 'ready':
            out['measures'].append(measure | dict(status='unavailable', reason=block['reason']))
            continue
        row_a = block['cycles'].get(str(ctx['anchor_cycle']))
        level = (row_a or {}).get('to_house_senate') if mid == 'pac_to_candidates' else row_a
        if level is None:
            out['measures'].append(measure | dict(status='unavailable', reason='The snapshot has no ' + str(ctx['anchor_cycle']) + ' level for this measure.'))
            continue
        base = level * ctx['multiplier']
        values = dict(projected_cycle_real_2025=base * math.exp(point), cycle_coefficient_low=base * math.exp(point - coef_half),
                      cycle_coefficient_high=base * math.exp(point + coef_half), cycle_prediction_low=base * math.exp(point - pred_half),
                      cycle_prediction_high=base * math.exp(point + pred_half))
        measure.update(anchor_cycle_nominal=level, anchor_cycle_real_2025=base, **values)
        # The residual variance of the interval comes from OpenSecrets PAC aggregates. Check it against this measure's OWN last observed step
        # (Mentat cycle A-2 -> A, 2025 USD): a step far outside the stated interval means the interval is not supported for this measure.
        row_p = block['cycles'].get(str(ctx['anchor_cycle'] - 2))
        level_p = (row_p or {}).get('to_house_senate') if mid == 'pac_to_candidates' else row_p
        mult_p = ctx['snap_cpi'].get(str(ctx['anchor_cycle'] - 2))
        if level_p and level_p > 0 and level > 0 and mult_p:
            own = math.log(base / (level_p * mult_p))
            measure.update(own_last_step_log=own, own_last_step_sigma_ratio=own / math.sqrt(step['sigma2']))
            if abs(own) > step['critical_95'] * math.sqrt(step['sigma2']):
                measure['flags'].append('interval_not_supported_by_own_history')
        if mid == 'pac_to_candidates' and block['single_pac']:
            measure['flags'].append('single_pac_thin_base')
            measure['flag_text'] = 'One PAC (' + block['committees'][0]['company'] + '): a thin base, not the industry\'s PAC giving.'
        if wstatus and wstatus != 'window':
            measure.update(remainder_status=wstatus, remainder_reason=wreason)
        elif wstatus:
            cut = _effective_cut(block['observed'], ctx['as_of'], ctx['coverage_end'])
            if cut is None:
                measure.update(remainder_status='no_observed', remainder_reason='The as-of date precedes the first observed Mentat month-end.')
            else:
                observed = cut['amount'] if mid == 'general_giving' else cut['to_house_senate']
                observed_real = observed * ctx['open_multiplier']
                measure.update(observed=dict(through=cut['through'], amount_nominal=observed, amount_real_2025=observed_real, face_value=ctx['face_value']),
                               remainder_starts=_next_day(cut['through']),
                               **_remainder(values['projected_cycle_real_2025'],
                                            dict(remaining_coefficient_low=values['cycle_coefficient_low'], remaining_coefficient_high=values['cycle_coefficient_high'],
                                                 remaining_prediction_low=values['cycle_prediction_low'], remaining_prediction_high=values['cycle_prediction_high']), observed_real))
                # The unconditional interval above ignores that `observed` is already given; the conditional one is what a display must draw.
                given = _given_observed(values['projected_cycle_real_2025'], pred_half, step['critical_95'], step['df'], observed_real)
                if given:
                    measure.update(given)
                    if values['projected_cycle_real_2025'] < observed_real:
                        measure['flags'].append('observed_exceeds_unconditional_projection')
        out['measures'].append(measure)
    return out


# --- partisan share -------------------------------------------------------------------------------------------------------
def _v2_share_effect(m, ind_id, target, anchor):
    """(effect, variance, critical, forecast-only effect, forecast-only variance, missing inputs) of the change from the anchor cycle's inputs to the target's."""
    row = m['slopes'].get(ind_id)
    if m['application'] == 'offset' and row is not None and m['offset'] is not None:
        o = m['offset']
        terms, beta, cov = o['terms'], o['beta'], o['covariance']
        crit, focal, focal_var = row['applied']['critical_95'], row['applied']['beta'], row['applied']['se'] ** 2
    else:
        terms, beta, cov = m['terms'], m['beta'], m['covariance']
        crit, focal, focal_var = m['critical_95'], None, None
    missing, delta = [], []
    for term in terms:
        if term == 'Intercept' or term.startswith('industry:'):
            delta.append(0.)
        elif target.get(term) is None:
            missing.append(term)
            delta.append(0.)
        else:
            delta.append(target[term] - anchor[term])
    d_forecast = None if target.get('Forecast') is None else target['Forecast'] - anchor['Forecast']
    if focal is None:
        j = terms.index('Forecast')
        only = [d_forecast if i == j and d_forecast is not None else 0. for i in range(len(terms))]
        forecast_effect, forecast_var = sum(a * c for a, c in zip(only, beta)), _quad(cov, only)
        return sum(a * c for a, c in zip(delta, beta)), _quad(cov, delta), crit, forecast_effect, forecast_var, missing
    if d_forecast is None:
        missing.append('Forecast')
        d_forecast = 0.
    return (sum(a * c for a, c in zip(delta, beta)) + focal * d_forecast, _quad(cov, delta) + focal_var * d_forecast ** 2, crit,
            focal * d_forecast, focal_var * d_forecast ** 2, missing)


def _v2_share_terms(m, ind_id, target, anchor):
    """{term: pp contribution} of the modelled change, from the same terms and coefficients _v2_share_effect sums (intercept and industry
    terms cancel). Lets a display say WHICH input moved the share when the political input did not."""
    row = m['slopes'].get(ind_id)
    out = {}
    if m['application'] == 'offset' and row is not None and m['offset'] is not None:
        terms, beta = m['offset']['terms'], m['offset']['beta']
        if target.get('Forecast') is not None:
            out['Forecast'] = 100 * row['applied']['beta'] * (target['Forecast'] - anchor['Forecast'])
    else:
        terms, beta = m['terms'], m['beta']
    for term, coef in zip(terms, beta):
        if term == 'Intercept' or term.startswith('industry:') or target.get(term) is None:
            continue
        out[term] = out.get(term, 0.) + 100 * coef * (target[term] - anchor[term])
    return out


def _share_anchor(ctx, m, ind_id, cycle):
    """(anchor id, Mentat anchor cycle A, steps k, the bundle's anchor entry) of one share model x industry x horizon.

    The entry is the auto-pick of the refit (share_models.*.anchors[id]); an industry without one (no control capability) takes the default."""
    entry = m['anchors'].get(ind_id)
    anchor = entry['anchor'] if entry else 'previous_cycle'
    if anchor == 'same_phase':
        return anchor, cycle - SHARE_ANCHORS['same_phase']['cycle_offset'], 1, entry
    return anchor, ctx['anchor_cycle'], (cycle - ctx['anchor_cycle']) // 2, entry


def _share_anchor_inputs(b, m, ind, chamber, a):
    """(anchor-cycle inputs, the anchor_input block reported) for Mentat anchor cycle `a`, or None when the bundle has no control input or growth for it.

    Control input: political_inputs.anchor_cycles[a] when present (2024), else the share model's own history row of this industry for `a`.
    Growth: the history row when that is the source, with the industry's growth history as the fallback (and the only source for 2024)."""
    mu, sd = ind['growth_mean'], ind['growth_sd']
    political = b['political_inputs']['anchor_cycles'].get(str(a))
    grown = next((r for r in ind['history'] if r['cycle'] == a), None)
    if political is not None:
        ch = political[chamber]
        control, forecast = political['current_control'][chamber], ch['p_rep']
        block = dict(cycle=a, p_rep=forecast, market=ch['market'], days=ch['days'], expected_days=ch['expected_days'], first_day=ch['first_day'],
                     partial_window=ch['partial_window'], note=ch.get('note'), current_control=control, source='political_inputs')
        rows = [grown]
    else:
        hist = next((r for r in m['history'] if r['industry'] == ind['id'] and r['cycle'] == a), None)
        if hist is None:
            return None
        control, forecast = hist['current_control'], hist['forecast']
        block = dict(cycle=a, p_rep=forecast, market=None, days=None, expected_days=None, first_day=None, partial_window=False,
                     note='The ' + _period(a) + ' control input is the value the model was fitted on for that cycle (its training history row).',
                     current_control=control, source='model_history')
        rows = [hist, grown]
    g = {key: next((r[key] for r in rows if r is not None and r.get(key) is not None), None) for key in ('growth_pct', 'prior_growth_pct')}
    if control is None or forecast is None or g['growth_pct'] is None or g['prior_growth_pct'] is None:
        return None
    inputs = {'RMajNow': float(control), 'Forecast': forecast, 'G_prior': (g['prior_growth_pct'] - mu) / sd, 'G_current': (g['growth_pct'] - mu) / sd,
              'Pres': float(a % 4 == 0)}
    return inputs, block


def _v2_chamber(ctx, assumptions, cycle, chamber, prior, growth, pac):
    b, ind = ctx['b'], ctx['industry']
    m = b['share_models']['control_' + chamber]
    anchor_id, a, k, entry = _share_anchor(ctx, m, ind['id'], cycle)
    row = m['slopes'].get(ind['id'])
    rule = row['applied']['rule'] if row else m['slope_rule']
    out = dict(status='needs_inputs', chamber=chamber, model_id=m['id'], anchor_cycle=a, steps=k, slope_rule=rule, slope_label=SLOPE_LABELS[rule],
               ordinary_p=(row['applied']['ordinary_p'] if row and m['application'] == 'offset' else m['ordinary_p'].get('Forecast')),
               specification=m['specification'], n=m['n'], training_cycles=m['training_cycles'], bases=[],
               anchor=anchor_id, anchor_label='Mentat ' + _period(a) + ' (' + SHARE_ANCHORS[anchor_id]['label'] + ')',
               anchor_reason=entry['reason'] if entry else None, anchor_backtest=copy.deepcopy(entry['backtest']) if entry else None)
    if ind.get('share_scoring') == 'pooled':
        out.update(share_scoring='pooled', share_scoring_note=POOLED_SHARE_NOTE)
    if not ind['capabilities']['control']:
        return out | dict(status='withheld', reason=ind['withheld'].get('control') or 'The control model is withheld for this industry.')
    if a > ctx['open_cycle'] - 2:
        return out | dict(status='unavailable', reason='Mentat\'s ' + _period(a) + ' cycle is still open in the anchor snapshot, so it cannot anchor this share.')
    pooled = ind.get('share_scoring') == 'pooled'          # no share-model rows of its own: the pooled step and fitted range stand in (POOLED_SHARE_SCORED)
    found = _share_anchor_inputs(b, m, ind, chamber, a)
    step = m['prediction']['industries'].get(ind['id']) or (_pooled_step(m) if pooled else None)
    if found is None or step is None:
        return out | dict(status='withheld', reason='The model package has no anchor-cycle control input, growth history or residual variance for this industry.')
    at_anchor, out['anchor_input'] = found
    majority = _number(_mapping(assumptions.get('current_control'), 'Current control').get(chamber), chamber + ' current control', 0, 1)
    if majority is not None and majority not in (0., 1.):
        raise ValueError('Current control must be Democratic (0) or Republican (1).')
    score = _number(_mapping(assumptions.get('control_scores'), 'Political expectations').get(chamber), chamber + ' forecast', 0, 100)
    forecast = None if score is None else score / 100
    mu, sd = ind['growth_mean'], ind['growth_sd']
    target = {'RMajNow': majority, 'Forecast': forecast, 'G_prior': None if prior is None else (prior - mu) / sd,
              'G_current': None if growth is None else (growth - mu) / sd, 'Pres': float(cycle % 4 == 0)}
    effect, var, crit, f_effect, f_var, missing = _v2_share_effect(m, ind['id'], target, at_anchor)
    if missing:
        names = {'RMajNow': chamber + ' current-control assumption', 'Forecast': chamber + ' control score', 'G_current': 'cycle growth', 'G_prior': 'prior-cycle growth'}
        return out | dict(reason='Enter ' + ', '.join(names.get(t, t) for t in missing) + '.')
    favored = 1 if forecast > .5 else -1 if forecast < .5 else 0
    fh = crit * math.sqrt(max(0., f_var))
    directed, directed_low = f_effect * favored * 100, min((f_effect - fh) * favored, (f_effect + fh) * favored) * 100
    out.update(change_pp=effect * 100, political_change_pp=f_effect * 100, political_low_pp=(f_effect - fh) * 100, political_high_pp=(f_effect + fh) * 100,
               forecast=forecast, baseline_forecast=at_anchor['Forecast'], expected_advantaged_party='R' if favored == 1 else 'D' if favored == -1 else 'Neither',
               materiality=[dict(threshold_pp=t, toward_expected_party_pp=directed if favored else None, point_meets=directed >= t if favored else None,
                                 ordinary_interval_meets=directed_low >= t if favored else None) for t in (1, 3, 5)])
    out['change_by_term_pp'] = _v2_share_terms(m, ind['id'], target, at_anchor)
    fitted = [('Forecast', forecast, [r['forecast'] for r in m['history']]), ('G_prior', prior, _fitted_growth(b, m, ind, 'prior_growth_pct')),
              ('G_current', growth, _fitted_growth(b, m, ind, 'growth_pct'))]
    out['extrapolation'] = any(x is not None and (x < min(xs) - 1e-9 or x > max(xs) + 1e-9) for term, x, xs in fitted if term in m['terms'])
    # Name each input outside the fitted range (values as entered: control as a probability, growth in %), so the flag says which and how far.
    out['extrapolated_inputs'] = [dict(term=term, value=x, fitted_min=min(xs), fitted_max=max(xs), fitted_cycles=m['training_cycles'])
                                  for term, x, xs in fitted if term in m['terms'] and x is not None and (x < min(xs) - 1e-9 or x > max(xs) + 1e-9)]
    fitted_shares = [r['share'] for r in m['history']]
    out['fitted_share_range'] = [min(fitted_shares), max(fitted_shares)]
    pred_half = _half(step['critical_95'], var + k * step['sigma2'])
    coef_half = crit * math.sqrt(max(0., var))
    wstatus, wreason = _window(ctx, cycle)
    bases = {}
    for bid, label in SHARE_BASES:
        block = _share_block(ctx, bid)
        basis = dict(id=bid, label=label, status='needs_inputs', flags=[])
        if bid == 'pac_candidate' and block.get('panel'):
            basis['population'] = _population(ind['id'], block)          # D-117: the split divides the same cohort as the dollars
        bases[bid] = basis
        out['bases'].append(basis)
        if block['status'] != 'ready' or block.get('split_reason'):
            basis.update(status='unavailable', reason=block['reason'] or block.get('split_reason'))
            continue
        parts = block['cycles'].get(str(a), {}).get(chamber)
        total = None if parts is None else parts['D'] + parts['R']
        if not total or total <= 0:
            basis.update(status='unavailable', reason='No two-party ' + chamber + ' candidate giving in the ' + _period(a) + ' anchor cycle to take a share of.')
            continue
        share_a = parts['R'] / total
        raw = share_a + effect
        basis.update(anchor_share=share_a, anchor_total_nominal=total, anchor_D=parts['D'], anchor_R=parts['R'], thin_base=total < ctx['threshold'],
                     raw_projected_cycle_R_share=raw, cycle_coefficient_low=share_a + effect - coef_half, cycle_coefficient_high=share_a + effect + coef_half,
                     cycle_prediction_low=share_a + effect - pred_half, cycle_prediction_high=share_a + effect + pred_half)
        basis['interval_outside_share_range'] = basis['cycle_coefficient_low'] < 0 or basis['cycle_coefficient_high'] > 1
        basis['prediction_outside_share_range'] = basis['cycle_prediction_low'] < 0 or basis['cycle_prediction_high'] > 1
        if basis['thin_base']:
            basis['flags'].append('thin_base')
        # The pp change was estimated on whole-industry shares inside [min, max] of the fitted rows; a Mentat anchor share outside that range
        # (e.g. a 0-9% R base) is an extrapolation of the share itself, which the linear change can push below 0 or above 100%.
        basis['anchor_share_outside_fitted_range'] = not (min(fitted_shares) - 1e-9 <= share_a <= max(fitted_shares) + 1e-9)
        if basis['anchor_share_outside_fitted_range']:
            basis['flags'].append('anchor_share_outside_fitted_range')
        if not 0 <= raw <= 1:
            basis.update(status='outside_share_range', reason='These assumptions put the linear share estimate outside 0-100%. Revise the scenario; the result is not clipped.')
            continue
        basis.update(status='ready', projected_cycle_R_share=raw)
    if all(bases[bid]['status'] in ('unavailable', 'needs_inputs') for bid in bases):
        return out | dict(status='unavailable', reason='Neither candidate-giving basis has a two-party ' + chamber + ' anchor for the ' + _period(a) + ' cycle.')
    out['status'] = 'ready'
    # Remainder: projected two-party total of each basis = projected measure x previous-cycle candidate share of that measure.
    if wstatus and wstatus != 'window':
        for basis in out['bases']:
            basis.update(remainder_status=wstatus, remainder_reason=wreason)
    elif wstatus:
        totals = _v2_basis_totals(ctx, pac, chamber)
        remaining = _v2_basis_remaining(ctx, pac, chamber)
        for bid, basis in bases.items():
            if basis['status'] == 'ready':
                basis.update(_v2_share_remainder(ctx, chamber, basis['projected_cycle_R_share'], totals.get(bid), bid, growth is not None or ctx.get('cm') is not None))
                allocation = _v2_remaining_allocation(ctx, chamber, basis, remaining.get(bid), bid)
                if allocation:
                    basis['remaining_allocation'] = allocation
    return out


def _v2_basis_remaining(ctx, pac, chamber):
    """{basis id: remaining two-party candidate giving (2025 USD)} = the measure's remainder conditioned on the giving already observed
    (median) x the anchor-cycle share of that measure that went to the chamber's two-party candidates (the ratio _v2_basis_totals applies to
    the full cycle). Never negative, unlike projected total - observed."""
    measures = {m['id']: m for m in pac.get('measures', []) if m['status'] == 'ready'}
    a = str(ctx['anchor_cycle'])
    out = {}
    general, cand = measures.get('general_giving'), ctx['snap']['candidate_receipts']
    general_level = ctx['snap']['general_giving']['cycles'].get(a)
    cand_part = cand['cycles'].get(a, {}).get(chamber) if cand['status'] == 'ready' else None
    if general and general_level and cand_part and general.get('remaining_given_observed_median') is not None:
        out['employee_candidate'] = general['remaining_given_observed_median'] * (cand_part['D'] + cand_part['R']) / general_level
    pac_measure, block = measures.get('pac_to_candidates'), _share_block(ctx, 'pac_candidate')
    pac_row = block['cycles'].get(a) if block['status'] == 'ready' else None
    if pac_measure and pac_row and pac_row.get(chamber) and pac_row['to_house_senate'] and pac_measure.get('remaining_given_observed_median') is not None:
        part = pac_row[chamber]
        out['pac_candidate'] = pac_measure['remaining_given_observed_median'] * (part['D'] + part['R']) / pac_row['to_house_senate']
    return out


def _v2_remaining_allocation(ctx, chamber, basis, remaining_total, bid):
    """The projected R share applied to the REMAINING two-party money: observed D / R are facts and are not re-estimated, so the remaining
    split is the model's share itself and both remaining amounts are non-negative. The implied full-cycle share mixes observed and projected."""
    seen = _observed_dr(ctx, chamber, bid)
    if remaining_total is None or seen is None:
        return None
    _through, d, r = seen
    mult = ctx['open_multiplier']
    observed_d, observed_r = d * mult, r * mult
    share, low, high = basis['projected_cycle_R_share'], basis['cycle_prediction_low'], basis['cycle_prediction_high']
    total = observed_d + observed_r + remaining_total
    if total <= 0:
        return None
    return dict(rule='Projected R share applied to the remaining two-party money; observed giving is not re-estimated',
                remaining_total_real_2025=remaining_total, remaining_R_real_2025=share * remaining_total, remaining_D_real_2025=(1 - share) * remaining_total,
                remaining_R_share=share, implied_cycle_total_real_2025=total, implied_cycle_R_share=(observed_r + share * remaining_total) / total,
                implied_cycle_R_share_prediction_low=(observed_r + low * remaining_total) / total,
                implied_cycle_R_share_prediction_high=(observed_r + high * remaining_total) / total)


def _v2_basis_totals(ctx, pac, chamber):
    """{basis id: projected two-party candidate total (2025 USD)} from this horizon's PAC measures and the anchor-cycle candidate shares."""
    measures = {m['id']: m for m in pac.get('measures', []) if m['status'] == 'ready'}
    a = str(ctx['anchor_cycle'])
    out = {}
    general, cand = measures.get('general_giving'), ctx['snap']['candidate_receipts']
    general_level = ctx['snap']['general_giving']['cycles'].get(a)
    cand_part = cand['cycles'].get(a, {}).get(chamber) if cand['status'] == 'ready' else None
    if general and general_level and cand_part:
        out['employee_candidate'] = general['projected_cycle_real_2025'] * (cand_part['D'] + cand_part['R']) / general_level
    pac_measure, block = measures.get('pac_to_candidates'), _share_block(ctx, 'pac_candidate')
    pac_row = block['cycles'].get(a) if block['status'] == 'ready' else None
    if pac_measure and pac_row and pac_row.get(chamber) and pac_row['to_house_senate']:
        part = pac_row[chamber]
        out['pac_candidate'] = pac_measure['projected_cycle_real_2025'] * (part['D'] + part['R']) / pac_row['to_house_senate']
    return out


def _share_block(ctx, bid):
    """The candidate-giving block a share basis divides: the employees' receipts, or the company PACs' (D-117: the published panel's cohort for an industry on it, else the anchors' roster)."""
    if bid == 'employee_candidate':
        return ctx['snap']['candidate_receipts']
    return (ctx.get('panel') or {}).get('pac_to_candidates') or ctx['snap']['pac_to_candidates']


def _observed_dr(ctx, chamber, bid):
    """(cut, D, R) observed through the effective cut for one basis (the panel cohort's own cut for its company PACs, not general giving's)."""
    block = _share_block(ctx, bid)
    cut = _effective_cut(block['observed'], ctx['as_of'], block.get('coverage_end', ctx['coverage_end']))
    return None if cut is None else (cut['through'], cut[chamber]['D'], cut[chamber]['R'])


def _split(share, total, observed_d, observed_r):
    """Remaining D and R of a two-party total split by a projected share, after the observed amounts; no clipping."""
    remaining_r, remaining_d = share * total - observed_r, (1 - share) * total - observed_d
    if remaining_r < 0 or remaining_d < 0 or remaining_r + remaining_d <= 0:
        return dict(remainder_status='budget_inconsistent', projected_remaining_R_share=None, projected_remaining_R_real_2025=remaining_r,
                    projected_remaining_D_real_2025=remaining_d,
                    remainder_reason='The projected total and share are incompatible with the giving already observed (a remaining amount is negative). Revise the scenario; no clipping is applied.')
    return dict(remainder_status='ready', remainder_reason=None, projected_remaining_R_share=remaining_r / (remaining_r + remaining_d),
                projected_remaining_R_real_2025=remaining_r, projected_remaining_D_real_2025=remaining_d)


def _v2_share_remainder(ctx, chamber, share, total, bid, growth_given):
    if total is None:
        reason = ('Enter cycle revenue growth to project the two-party total this share divides.' if not growth_given else
                  'The projected two-party total needs a ready projection of the measure it divides, and the snapshot has none for this basis.')
        return dict(remainder_status='needs_inputs', remainder_reason=reason)
    seen = _observed_dr(ctx, chamber, bid)
    if seen is None:
        return dict(remainder_status='no_observed', remainder_reason='The as-of date precedes the first observed Mentat month-end.')
    through, d, r = seen
    m = ctx['open_multiplier']
    return dict(projected_total_real_2025=total, observed=dict(through=through, D=d, R=r, D_real_2025=d * m, R_real_2025=r * m, face_value=ctx['face_value']),
                remainder_starts=_next_day(through), **_split(share, total, d * m, r * m))


# --- lobbying ------------------------------------------------------------------------------------------------------------------
# Owner rulings 2026-10-08: the projection of a Melange LDA series is the in-year NOWCAST of the year the LDA has begun filing (the anchors carry it: observed + (1 - s) x the
# prior full-year total, `partial.nowcast`), carried forward unchanged to the years after it; a growth-forecast change that is entered moves it by the pooled log slope, as it
# moved the old projection. Its bands are EMPIRICAL, not model-based: the 80% band is the blind-test 80th percentile of the nowcast error of the quarter cut (pooled, log scale),
# the ~95% band the largest blind miss; a year after the nowcast year adds, in quadrature, the LobbyView persistence error of that many years (the industry's 80th percentile;
# the pooled 95th percentile for the ~95% band). The old year-effect slope interval never beat last-period naive (G9) and is no longer used.
LOBBY_METHOD = ('In-year nowcast: the amount filed so far this year (through the latest complete quarter) plus the share still to come of the prior full year, where the filed share is the '
                'geometric mean of the share filed by that quarter in up to the three prior years (Melange LDA, nominal dollars). Later years carry it forward unchanged unless a '
                'growth-forecast change is entered, which moves it by the pooled log slope.')


def _lobby_changes(changes, base_year, year):
    """The growth-forecast changes (pp) of the years after the base year, up to `year`; a blank input is no change."""
    return [_number(changes.get(str(y)), 'Annual forecast-growth change', -100, 100) or 0. for y in range(base_year + 1, year + 1)]


def _lobby_halves(ident, now_year, partial, year, errors, persistence):
    """(half-width 80%, half-width 95%, reason) of one projected year on the log scale; (None, None, reason) when the blind or the persistence errors are missing.
    The nowcast year takes the blind errors of its quarter cut; a later year adds the persistence error of (year - nowcast year) years in quadrature."""
    h80 = h95 = 0.
    if partial is not None:
        cut = partial['through_quarter'][1]
        row = ((errors or {}).get('by_cut') or {}).get(cut)
        if row is None:
            return None, None, 'The anchor snapshot has no blind-test errors for the lobbying nowcast at the ' + partial['through_quarter'] + ' cut. Rebuild the anchors.'
        h80, h95 = row['q80'], row['max']
    horizon = year - now_year
    if horizon > 0:
        per = ((persistence or {}).get('industries') or {}).get(ident, {}).get(str(horizon))
        pooled = ((persistence or {}).get('pooled') or {}).get(str(horizon))
        if per is None or pooled is None:
            return None, None, 'The model package has no lobbying persistence error for ' + str(horizon) + ' year(s) ahead of the nowcast year.'
        h80, h95 = math.hypot(h80, per['q80']), math.hypot(h95, pooled['q95'])
    return h80, h95, None


def _v2_lobbying(ctx, changes, cycle):
    b, ind = ctx['b'], ctx['industry']
    lda = ctx['snap']['lda']
    out = dict(status='ready', reason=None, rows=[])
    model = b['lobby_model']['log_refit']
    if not ind['capabilities']['lobbying']:
        return out | dict(status='withheld', reason=ind['withheld'].get('lobbying') or 'The lobbying model is withheld for this industry.')
    if lda['status'] != 'ready':
        return out | dict(status='unavailable', reason=lda['reason'])
    row = model['slopes'].get(ind['id'])
    if row is None:
        return out | dict(status='withheld', reason='The model package has no lobbying slope for this industry.')
    slope = row['applied']
    annual = {sid: {int(y): v for y, v in lda['series'][sid]['annual'].items()} for sid, _label in LOBBY_SERIES}
    base_year = max(annual['income_total'])
    errors, persistence = ctx.get('nowcast_errors'), model.get('persistence')
    out.update(slope=dict(rule=slope['rule'], label=SLOPE_LABELS[slope['rule']], beta=slope['beta'], se=slope['se'], df=slope['df']), ordinary_p=slope['ordinary_p'],
               sector=lda['sector'], sector_label=lda['label'], source_file=lda['source_file'], base_year=base_year, through=lda['through'], note=lda['note'], method=LOBBY_METHOD)
    first = ctx['as_of'].year if cycle == ctx['current'] else cycle - 1
    cut_n, later = None, False                           # the number of blind tests behind the bands of the cut in use; whether a year after the nowcast year is among the rows
    for year in range(first, cycle + 1):
        item = dict(year=year, status='ready', series=[])
        out['rows'].append(item)
        if year <= base_year:
            item.update(status='complete', reason='Melange LDA already has the full year ' + str(year) + '.')
            continue
        values = _lobby_changes(changes, base_year, year)
        total, k = sum(values), len(values)
        move = slope['beta'] * total                     # 0 with no growth change entered: the slope no longer moves the point
        item.update(base_year=base_year, steps=k, chained=k > 1, forecast_change_pp=values[-1], cumulative_change_pp=total, slope_change_pct=100 * math.expm1(move))
        for sid, label in LOBBY_SERIES:
            level, partial = annual[sid][base_year], lda['series'][sid]['partial']
            now = partial.get('nowcast') if partial else None
            if partial and now is None:
                item.update(status='unavailable', series=[], reason=('The ' + str(partial['year']) + ' nowcast needs the complete prior year and a prior-year share filed by the same quarter; the anchors have none.')
                            if 'nowcast' in partial else 'The anchor snapshot predates the lobbying nowcast. Rebuild the anchors (mentat/forecast/anchors/build_giving_anchors.py).')
                break
            now_year = partial['year'] if now else base_year
            h80, h95, why = _lobby_halves(ind['id'], now_year, partial if now else None, year, errors, persistence)
            if why:
                item.update(status='unavailable', series=[], reason=why)
                break
            if now:
                cut_n = errors['by_cut'][partial['through_quarter'][1]]['n']
            later = later or year > now_year
            point = (now['point'] if now else level) * math.exp(move)
            lo80, hi80, lo95, hi95 = point * math.exp(-h80), point * math.exp(h80), point * math.exp(-h95), point * math.exp(h95)
            s = dict(id=sid, label=label, base_level=level, projected=point, change_pct=100 * (point / level - 1), nowcast=now, nowcast_year=now_year, years_after_nowcast=year - now_year,
                     half_width_80=h80, half_width_95=h95, band80_low=lo80, band80_high=hi80, band95_low=lo95, band95_high=hi95)
            if partial and partial['year'] == year and cycle == ctx['current']:
                if ctx['as_of'] < date.fromisoformat(lda['through']):
                    s.update(remainder_status='no_observed', remainder_reason='The Melange LDA quarters run past the as-of date; use a later as-of date.')
                else:
                    # The nowcast and the observed quarters are both the LDA's own nominal dollars, so the remainder needs no deflation.
                    s.update(observed=dict(through=lda['through'], through_quarter=partial['through_quarter'], amount_nominal=partial['total'], face_value=True),
                             remainder_starts=_next_day(lda['through']),
                             **_remainder(point, dict(remaining_band80_low=lo80, remaining_band80_high=hi80, remaining_band95_low=lo95, remaining_band95_high=hi95), partial['total']))
            item['series'].append(s)
    out['bands'] = dict(blind_tests=cut_n, label_80='80% band: the 80th percentile of the blind nowcast errors' + (' (n=%d blind tests)' % cut_n if cut_n else ''),
                        label_95='~95% band: the largest blind miss' + (' (n=%d blind tests)' % cut_n if cut_n else '')
        + ('; for the later years, added in quadrature to the pooled 95th percentile of the LobbyView persistence error' if later else '')
        + '; with so few tests it is a worst-observed bound, not an estimated 95% interval')
    return out


# ----------------------------------------------------------------------------------------------------------------------
# Cycle model (bundle `cycle_model`): the cycle-aware 50/50 "Giving by cycle" projection (owner rulings of 2026-10-07; modelspec/SPEC.md and the coordinator calls)
#
# Full cycle   M = B exp(phi dP + d_h) (1 + x / 2)
#   B   the anchor cycle's Mentat level in 2025 dollars (levels come from the anchor snapshot; the bundle only supplies the relationships),
#   dP  the presidential-cycle difference Pres(c) - Pres(c - 2), phi its coefficient, d_h the damped drift of the h-th step ahead,
#   x   the industry budget change the IBISWorld REAL growth path implies for the cycle with the one-year lag (G_odd, G_even) given this measure's odd/even mix in
#       the anchor cycle. The ONE growth input of a horizon ("Industry revenue growth", average annual %, blank = the IBIS default) shifts every annual growth of the
#       lag window c-3 .. c-1 by the same amount; each measure then derives its own x from its own odd-year share.
# The open cycle's published layer conditions M on the giving already observed (P1); the next cycle chains from that layer. Company PACs are the exception (D-116, Paul
# 2026-10-08, "use the panel's nowcast for company PACs, drop the 50/50"): no budget change (x = 0) on either horizon and, in the open cycle, the published PAC panel's
# R8_REMAINING nowcast O + (1 - s) B in place of the model (R8; it replaced the D-099 BLEND); the step into the next (presidential) cycle takes the owner's
# factor in place of phi (D-118/D-119/D-121, PAC_UPLIFT_FACTOR x1.09; general giving takes GENERAL_UPLIFT_FACTOR x1.8635 on both steps). A bundle without `cycle_model` takes the earlier path unchanged (the pointer can be rolled back without a code rollback).
# ----------------------------------------------------------------------------------------------------------------------
from fractions import Fraction  # noqa: E402
from statistics import NormalDist  # noqa: E402

CYCLE_GRID = 400               # midpoint quantile grid of each factor of the chained next-cycle distribution (the reference build used 1000: medians agree to 0.01%)
PANEL_NOWCAST = frozenset({'pac_to_candidates'})      # D-116: the measures on the panel nowcast, which the IBIS budget change does not move

# D-118 / D-119 / D-121 (Paul, 2026-10-08, "use +9% and restart 8806"; then "Different for both PAC and General", with OpenSecrets data): a fixed presidential-cycle factor u replaces the
# fitted cycle effect phi (phi = log u) in the two measures; the drift d_h, the 50/50, P1 and the step's prediction interval stay the fitted ones.
#   Company PACs ("less cyclical"): u = 1.09 (+9%), on the step INTO a presidential cycle only (2027-28, dP = +1). The midterm step (2025-26, dP = -1; an unconditioned point the chart does
#     not draw) keeps the fitted phi, which avoids a false observed-exceeds flag. ratio_by_pair: FEC committee-to-candidate giving of all PACs in real 2025 dollars, a presidential cycle over its
#     preceding midterm (geometric mean 0.981), the data the rule was set against.
#   General giving ("roughly 86% higher"): u = the presidential average of OpenSecrets' total cost of elections over the midterm average, inflation-adjusted $bn, 12.56 (2012-2024) / 6.74
#     (2010-2022) = 1.8635, on BOTH steps, symmetrically: dP = -1 gives -log u (2025-26) and dP = +1 gives +log u (2027-28). The two averages are Paul's, as he gave them (the eight totals
#     average 12.56 and 6.745); the totals are the response's ratio_by_pair.
PAC_UPLIFT_FACTOR = 1.09
PAC_UPLIFT_SOURCE = 'owner rule D-118/D-121: +9% (PAC spending less cyclical; national FEC all-PAC ratio 0.98)'
PAC_UPLIFT_RATIOS = {'2016/2014': 0.9969291514830991, '2020/2018': 0.9639711510629424, '2024/2022': 0.9829388610358335}
GENERAL_COST_BN = {'2010': 5.24, '2012': 8.59, '2014': 5.09, '2016': 8.51, '2018': 7.15, '2020': 18.34, '2022': 9.50, '2024': 14.80}
GENERAL_AVERAGES_BN = dict(presidential=12.56, midterm=6.74)
GENERAL_UPLIFT_FACTOR = GENERAL_AVERAGES_BN['presidential'] / GENERAL_AVERAGES_BN['midterm']
GENERAL_UPLIFT_SOURCE = 'owner rule D-118/D-121: OpenSecrets total cost of elections, presidential avg 2012-2024 vs midterm avg 2010-2022'
UPLIFT_BOTH_STEPS = frozenset({'general_giving'})      # D-121: the factor is applied on the midterm step too (symmetrically); the company PACs take it on the presidential step only


def _presidential_uplift(mid):
    """The owner's presidential-cycle rule of a measure as the response states it ({factor, source, ratio_by_pair}), or None for a measure without one."""
    if mid == 'pac_to_candidates':
        return dict(factor=PAC_UPLIFT_FACTOR, source=PAC_UPLIFT_SOURCE, ratio_by_pair=dict(PAC_UPLIFT_RATIOS))
    if mid == 'general_giving':
        return dict(factor=GENERAL_UPLIFT_FACTOR, source=GENERAL_UPLIFT_SOURCE, ratio_by_pair=dict(GENERAL_COST_BN))
    return None


def _open_layer(cm, mid):
    """The open-cycle layer of a measure: R8 for the company PACs (D-116, whatever the bundle's `open_cycle_layer` says), else the bundle's."""
    return 'R8' if mid in PANEL_NOWCAST else cm['measures'][mid]['open_cycle_layer']


def _pres(cycle):
    return 1. if cycle % 4 == 0 else 0.


def _price_level(infl, year):
    return 1. if year <= infl['base_year'] else (1. + infl['annual_rate']) ** (year - infl['base_year'])


def _cycle_factor(infl, cycle):
    """Nominal / real 2025 dollars of a cycle: the mean price level of its two years (years after the base year at the expected rate)."""
    return (_price_level(infl, cycle - 1) + _price_level(infl, cycle)) / 2.


def _real_multiplier(snapshot, infl, cycle):
    """Real 2025 dollars per nominal dollar of a cycle: the snapshot's CPI multiplier when the cycle has one, else the expected-inflation factor."""
    m = snapshot['cpi']['multipliers'].get(str(cycle))
    return m if m is not None else 1. / _cycle_factor(infl, cycle)


# D-092 (Paul): projections are shown in nominal dollars by default. The models work in 2025 dollars; the nominal twin of a measure is each 2025-dollar amount x the inverse of the multiplier
# the model deflates that cycle with (_real_multiplier: the snapshot's CPI when the cycle has one, else the expected inflation, 2.5726436%/yr at the cycle mid-point: 2025-26 x1.01286,
# 2027-28 x1.06565 against 2025). The 2023-24 level and the observed amount are Mentat's own nominal values and are never reconverted. The page does no inflation arithmetic.
NOMINAL_FIELDS = (('projected_cycle_real_2025', 'projected_cycle'), ('anchor_cycle_real_2025', 'anchor_cycle'),
                  ('cycle_coefficient_low', 'cycle_coefficient_low'), ('cycle_coefficient_high', 'cycle_coefficient_high'),
                  ('cycle_prediction_low', 'cycle_prediction_low'), ('cycle_prediction_high', 'cycle_prediction_high'),
                  ('cycle_given_observed_low', 'cycle_given_observed_low'), ('cycle_given_observed_median', 'cycle_given_observed_median'), ('cycle_given_observed_high', 'cycle_given_observed_high'),
                  ('remaining_given_observed_low', 'remaining_given_observed_low'), ('remaining_given_observed_median', 'remaining_given_observed_median'),
                  ('remaining_given_observed_high', 'remaining_given_observed_high'), ('projected_remaining', 'projected_remaining'),
                  ('remaining_coefficient_low', 'remaining_coefficient_low'), ('remaining_coefficient_high', 'remaining_coefficient_high'),
                  ('remaining_prediction_low', 'remaining_prediction_low'), ('remaining_prediction_high', 'remaining_prediction_high'), ('excess_observed', 'excess_observed'))


def _nominal_factor(ctx, cycle):
    """Nominal dollars per 2025 dollar of a cycle: the inverse of the multiplier the model deflates it with."""
    m = ctx['snap_cpi'].get(str(cycle))
    return 1. / m if m else _cycle_factor(ctx['cm']['inflation'], cycle)


def _nominal(measure, factor, inflation):
    """The explicit nominal-dollar twin of a ready cycle-model measure: every 2025-dollar amount it carries x `factor`; the anchor cycle's level and the observed amount are the anchors' own nominal values."""
    out = {nominal: measure[real] * factor for real, nominal in NOMINAL_FIELDS if measure.get(real) is not None}
    out.update(factor=factor, anchor_cycle=measure['anchor_cycle_nominal'],
               source='x%.5f against 2025 dollars: expected inflation of %.7f%% a year at the cycle mid-point (%s). The anchor-cycle level and the observed amount are the nominal values Mentat holds.'
               % (factor, 100. * inflation['annual_rate'], inflation['source']))
    if measure.get('observed'):
        out['observed'] = measure['observed']['amount_nominal']
    return out


def _ibis_path(cm, ident, cycle):
    """[(year, g)] of the lag window cycle-3 .. cycle-1 (IBISWorld REAL annual revenue growth, fractions), or None when the bundle's path does not cover it."""
    entry = cm['budget_rule']['growth'].get(ident)
    if entry is None:
        return None
    try:
        return [(y, float(entry['annual'][str(y)])) for y in (cycle - 3, cycle - 2, cycle - 1)]
    except KeyError:
        return None


def _path_average_pct(path):
    """Geometric mean of the window's annual growth, in percent."""
    product = 1.
    for _year, g in path:
        product *= 1. + g
    return 100. * (product ** (1. / len(path)) - 1.)


def _growth_source(entry):
    return 'IBISWorld ' + entry['report_id'] + ' ' + entry['edition'] + ' edition (' + entry['price_basis'] + ')'


def _growth_input(cm, ident, cycle, value):
    """(annual path used, info) of the growth input of one horizon. `value` is the user's average annual growth in percent (None = the IBIS default).

    The shift is delta = value - default average, added to every annual growth of the lag window. A value equal to the default as the catalog shows it (3 decimals)
    is the default."""
    path = _ibis_path(cm, ident, cycle)
    entry = cm['budget_rule']['growth'].get(ident)
    if path is None:
        return None, dict(status='unavailable', reason='The IBISWorld growth path in the model package does not cover ' + str(cycle - 3) + '-' + str(cycle - 1) +
                          ', the years that feed the ' + _period(cycle) + ' cycle (one-year lag).')
    default = _path_average_pct(path)
    info = dict(status='ready', default_pct=round(default, 3), used_pct=default, shift_pp=0., source='default', window=[cycle - 3, cycle - 1], report_id=entry['report_id'],
                edition=entry['edition'], source_text=_growth_source(entry))
    if value is not None and abs(value - round(default, 3)) > 1e-9:
        shift = (value - default) / 100.
        path = [(y, g + shift) for y, g in path]
        if any(1. + g <= 0. for _y, g in path):
            raise ValueError('Industry revenue growth is outside its supported range.')
        info.update(used_pct=value, shift_pp=100. * shift, source='user')
    return path, info


def _budget_change(s_odd, path):
    """(x, G_odd, G_even): the industry budget change of the cycle with the one-year lag. The odd (first) year of the cycle follows the revenue of years c-3 and c-2, the
    even year that of c-2 and c-1; the measure's own odd-year share s_odd of the anchor cycle weights the two."""
    (_y1, g1), (_y2, g2), (_y3, g3) = path
    odd, even = (1. + g1) * (1. + g2), (1. + g2) * (1. + g3)
    return s_odd * odd + (1. - s_odd) * even - 1., odd, even


def _measure_level(block, mid, cycle):
    row = block['cycles'].get(str(cycle))
    return row.get('to_house_senate') if mid == 'pac_to_candidates' and isinstance(row, dict) else row


def _measure_odd(block, mid, cycle):
    row = (block.get('odd_year') or {}).get(str(cycle))
    return row.get('to_house_senate') if isinstance(row, dict) else row


def _default_budget(block, mid, anchor_cycle, path):
    """The default industry budget change of one measure (at the IBIS default growth), or the reason it is unavailable."""
    level, odd = _measure_level(block, mid, anchor_cycle), _measure_odd(block, mid, anchor_cycle)
    if block['status'] != 'ready' or level is None or level <= 0:
        return dict(status='unavailable', reason=block.get('reason') or 'The snapshot has no ' + str(anchor_cycle) + ' level for this measure.')
    if odd is None:
        return dict(status='unavailable', reason='Rebuild the anchors: the budget rule needs the ' + str(anchor_cycle) + ' cycle\'s odd-year split.')
    s_odd = odd / level
    x, g_odd, g_even = _budget_change(s_odd, path)
    return dict(status='ready', budget_growth_pct=100. * x, s_odd=s_odd, G_odd=g_odd, G_even=g_even)


def _cycle_view(b, cycle, offered):
    """What the catalog tells the display about the cycle model: the defaults of the one growth input per industry and horizon (value, window, source), the budget
    change they imply for each measure, the layers, the inflation convention and the Methods notes."""
    cm = b['cycle_model']
    growth, company = {}, {}
    for row in offered:
        ident, entry = row['id'], cm['budget_rule']['growth'].get(row['id'])
        try:
            _snapshot, anchored = _anchor_industry(ident)
            anchor_cycle = anchored['coverage']['current_cycle'] - 2
        except AnchorProblem:
            anchored, anchor_cycle = None, None
        pac_model = row['capabilities']['pac']          # an industry whose PAC model is withheld (offered for lobbying only) has no company PACs to name
        blocks = {} if anchored is None or not pac_model else _panel_blocks(ident, anchor_cycle + 2)          # D-117: the credit unions' company PACs are the published panel's cohort
        if anchored is not None:
            company[ident] = _company_pacs(ident, anchored, blocks) if pac_model else None
        per = {}
        for c in (cycle, cycle + 2):
            path = _ibis_path(cm, ident, c)
            if entry is None or path is None:
                per[str(c)] = dict(status='unavailable', reason='The IBISWorld growth path in the model package does not cover ' + str(c - 3) + '-' + str(c - 1) + '.')
                continue
            item = dict(status='ready', growth_default=round(_path_average_pct(path), 3), growth_window=[c - 3, c - 1], growth_source=_growth_source(entry),
                        report_id=entry['report_id'], edition=entry['edition'], price_basis=entry['price_basis'],
                        annual={str(y): g for y, g in path}, budget_defaults={})
            for mid, _label in PAC_MEASURES:
                item['budget_defaults'][mid] = (dict(status='unavailable', reason='The Mentat anchors are not available.') if anchored is None
                                                else _default_budget(blocks.get(mid) or anchored[mid], mid, anchor_cycle, path))
                if mid in PANEL_NOWCAST and item['budget_defaults'][mid]['status'] == 'ready':        # D-116: the growth does not move this measure
                    item['budget_defaults'][mid].update(budget_growth_pct=0., growth_applies=False)
            per[str(c)] = item
        growth[ident] = per
    return dict(version=cm['version'], inflation=cm['inflation'], layers={mid: _open_layer(cm, mid) for mid, _l in PAC_MEASURES},
                growth_applies={mid: mid not in PANEL_NOWCAST for mid, _l in PAC_MEASURES}, presidential_uplift={mid: _presidential_uplift(mid) for mid, _l in PAC_MEASURES},
                growth=growth, company_pacs=company, g_prior_defaults={k: v for k, v in cm['g_prior'].items() if k != 'source'}, notes=list(cm['notes']),
                measure_notes={mid: list(cm['measures'][mid]['notes']) + ([R8_NOTE] if mid in PANEL_NOWCAST else []) for mid, _l in PAC_MEASURES})


# --- t quantiles (stdlib) for the chained distribution ------------------------------------------------------------------------
def _t_pdf(z, df):
    return math.exp(math.lgamma((df + 1.) / 2.) - math.lgamma(df / 2.) - .5 * math.log(df * math.pi) - (df + 1.) / 2. * math.log1p(z * z / df))


def _t_quantile_upper(u, df):
    """z with P(T_df > z) = u (0 < u < 1): safeguarded Newton from a normal start, the bisection bracket of _t_upper_inverse as the fallback."""
    lo, hi = -1e4, 1e4
    z = NormalDist().inv_cdf(1. - u)
    for _ in range(100):
        f = _t_upper(z, df) - u
        if f > 0:
            lo = z
        else:
            hi = z
        if abs(f) <= 1e-14 * max(u, 1e-300):
            return z
        step = f / max(_t_pdf(z, df), 1e-300)
        new = z + step
        if not lo < new < hi:
            new = .5 * (lo + hi)
        if abs(new - z) <= 1e-13 * max(1., abs(z)):
            return new
        z = new
    return _t_upper_inverse(u, df)


@lru_cache(maxsize=64)
def _t_crit(df):
    """qt(.975, df)."""
    return _t_upper_inverse(.025, df)


@lru_cache(maxsize=64)
def _t_grid(df, n):
    """The n midpoint quantiles qt((l - .5) / n, df), l = 1 .. n (n even: the lower half is computed, the upper half mirrors it)."""
    lower = [-_t_quantile_upper((l - .5) / n, df) for l in range(1, n // 2 + 1)]
    return tuple(lower + [-v for v in reversed(lower)])


def _quantile(ordered, q):
    """Linear-interpolation quantile of a sorted list (numpy's default)."""
    pos = q * (len(ordered) - 1)
    low = int(math.floor(pos))
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] * (1. - (pos - low)) + ordered[high] * (pos - low)


def _layer_dist(center, scale, df, observed):
    """The log-t distribution of an open-cycle layer: centre, scale, df and above = P(full cycle >= observed); None when the observed giving leaves no probability."""
    above = 1. if observed <= 0 else _t_upper((math.log(observed) - center) / scale, df)
    return dict(center=center, scale=scale, df=df, above=above) if above > 1e-12 else None


def _chain(layer, mu2, s2, df2, n=CYCLE_GRID):
    """(2.5%, 50%, 97.5%) of exp(L + mu2 + s2 t): L the layer's log distribution conditioned on full >= observed (a midpoint quantile grid, qt(1 - (1 - u) above)),
    t a Student t with df2 degrees of freedom (its own grid)."""
    above = layer['above']
    base = [layer['center'] + layer['scale'] * _t_quantile_upper((1. - (k - .5) / n) * above, layer['df']) for k in range(1, n + 1)]
    grid = _t_grid(df2, n)
    ordered = sorted(v + mu2 + s2 * t for v in base for t in grid)
    return tuple(math.exp(_quantile(ordered, q)) for q in (.025, .5, .975))


# --- Company PACs on the published PAC panel's nowcast (D-116) -------------------------------------------------------------------
R8_KIND = ("Range of the three prior cycles' pace ({}), not a 95% interval: the panel nowcast O + (1 - s) B with each cycle's own completion share, lowest to highest")
R8_CHAIN_KIND = ('95% prediction interval of the phase-and-drift step chained on the panel nowcast (R8); it excludes the error of the nowcast itself')
R8_NOTE = ("Open cycle: the published PAC panel's nowcast (R8) O + (1 - s) B: O the giving observed to the cut, B the prior cycle's total of the same roster, s the geometric mean of the "
           "share of each of the three prior cycles' totals given by the same date. The range is the same formula with each cycle's own share, lowest to highest; it is not a 95% interval. "
           "Industry revenue growth does not affect company PACs on either horizon (D-116); the next cycle chains from the nowcast.")


def _r8_pace(cm, ident, block, mid, open_cycle, anchor_cycle, cut_through):
    """The chart roster's inputs of the nowcast, from the bundle's pace series: ((pace month p, the three prior cycles, O at p, [(O_p,j, F_j)]), None) or (None, why it is unavailable).
    p is the latest of March / June / September on or before the effective cut (the panel's m15 / m18 / m21 cuts)."""
    series = cm['pace']['series'].get(ident)
    if series is None:
        return None, 'The model package has no pace series for this industry.'
    level, ref = _measure_level(block, mid, anchor_cycle), series['cycles'].get(str(anchor_cycle))
    if level is None or ref is None or abs(ref['F'] - level) > .005:
        return None, 'The pace series was built for a different roster (its ' + str(anchor_cycle) + ' full cycle differs from the anchor level).'
    months = {int(q): str(open_cycle) + '-' + md for q, md in cm['pace']['months'].items()}
    usable = [q for q, cut in months.items() if cut <= cut_through]
    if not usable:
        return None, 'The observed giving ends before the first pace month (' + months[min(months)] + ').'
    p = max(usable)
    seen = [c for c in block['observed'] if c['through'] <= months[p]]
    observed_p = seen[-1]['to_house_senate'] if seen else None
    if observed_p is None or observed_p <= 0:
        return None, 'No giving is observed by the pace month (' + months[p] + ').'
    window = [open_cycle - 6, open_cycle - 4, open_cycle - 2]
    rows = [series['cycles'].get(str(c)) for c in window]
    if any(r is None or r['F'] <= 0 or r['O%d' % p] <= 0 for r in rows):
        return None, 'The pace series lacks giving at the pace month in one of the three prior cycles (' + ', '.join(str(c) for c in window) + ').'
    return (p, window, observed_p, [(r['O%d' % p], r['F']) for r in rows]), None


def _r8(cm, ident, block, mid, open_cycle, anchor_cycle, cut_through, mult, observed_real):
    """(nowcast dict, None) or (None, why it is unavailable): the panel's R8_REMAINING, mirrored from pac_giving_forecast.calculate (lines 189-217; that function takes a frame
    pinned to the panel's own rosters, so it cannot be fed this chart's roster). nowcast = O + (1 - s) B in NOMINAL dollars as in the panel: O the giving observed through the pace
    month p, B the anchor cycle's total of the same roster, s the geometric mean over the three prior cycles of O_p,j / F_j (exact fractions, the equal-shares and share-capped-at-one
    rules of the panel). The nominal total is converted ONCE to 2025 USD at the open cycle's deflator `mult`. The range is O + (1 - s_j) B for each cycle j alone (min to max); the panel
    publishes no interval.
    The inputs come from the bundle's pace series for the chart's roster (`_r8_pace`) or, for a block that carries `panel` (D-117: the credit unions' 16-PAC cohort), straight from the
    panel's current frame: its own cut, its own observed amount and its own history at the same date, in integer cents. Everything after the inputs is this one code path."""
    panel = block.get('panel')
    found, why = ((None, panel['window'], panel['observed_nominal'], panel['pairs']), None) if panel else _r8_pace(cm, ident, block, mid, open_cycle, anchor_cycle, cut_through)
    if found is None:
        return None, why
    p, window, observed_p, pairs = found
    level = _measure_level(block, mid, anchor_cycle)
    shares = [Fraction(o) / Fraction(f) for o, f in pairs]
    mean_log = math.fsum(math.log(o) - math.log(f) for o, f in pairs) / 3
    product_share = shares[0] * shares[1] * shares[2]
    if product_share >= 1:
        s = 1.
    elif shares[0] == shares[1] == shares[2]:
        s = float(shares[0])
    else:
        s = math.exp(mean_log)
    if not math.isfinite(s) or not 0 < s <= 1:
        return None, 'The completion share of the three prior cycles is not usable.'
    floor = observed_real / mult          # the nominal total cannot fall below the giving already observed (the cut may lie after the pace month)
    raw, raw_point = [observed_p + (1. - float(share)) * level for share in shares], observed_p + (1. - s) * level
    nominal, point = [max(v, floor) for v in raw], max(raw_point, floor)
    low, high = min(nominal), max(nominal)
    kind = R8_KIND.format(', '.join(str(c) for c in window))
    layer = dict(cycle_given_observed_low=low * mult, cycle_given_observed_median=point * mult, cycle_given_observed_high=high * mult,
                 remaining_given_observed_low=low * mult - observed_real, remaining_given_observed_median=point * mult - observed_real,
                 remaining_given_observed_high=high * mult - observed_real,
                 given_observed_kind=kind, interval_kind=kind)
    info = dict(pace_month=p, cycles=window, shares=[float(x) for x in shares], completion_share=s, share_capped_at_one=product_share > 1,
                observed_nominal=observed_p, anchor_total_nominal=level, remaining_nominal=(1. - s) * level, nowcast_nominal=raw_point,
                range_nominal=[low, high], deflator=mult, floored_at_observed=min(raw + [raw_point]) < floor, **({'panel_cut': panel['cut']} if panel else {}))
    return dict(layer=layer, dist=dict(center=math.log(point * mult), scale=0., df=1, above=1.), info=info), None


# --- Credit-union company PACs on the published PAC panel's 16-PAC cohort (D-117) ---------------------------------------------------
# Paul, 2026-10-08: "use the 16 credit union PACs". The company-PAC measure of the credit unions is the published PAC panel's cohort (pac-giving-current.json -> the active bundle), not
# the anchors' single committee (SchoolsFirst): every piece comes from the panel's CURRENT frame for the open cycle (B the cohort's total of the anchor cycle, O the giving observed at
# the panel's own cut, the three prior cycles' totals and their observed amounts on the same date), so the nowcast is the panel's own published one (`_r8` is the same code path as
# the banks'). Banks keep the chart's 19 committees. The panel's cut (2026-09-26) is not the general-giving cut: the measure carries its own `observed.through`, and the two dates are
# never put in one figure. The panel bundle holds the cohort's totals only; the D/R split of the same 16 PACs (the 2023-24 anchor by chamber, and the observed D/R to the panel's cut) is the
# partisan-dollar lane's own canonical classification, pinned in forecast/anchors/panel_cohort_split.json (build_panel_cohort_split.py), so the company-PAC candidate basis of the
# party split divides the same population as the dollars: the anchor share, the observed D/R, the R8 remainder and the cut are all the cohort's (Paul, 2026-10-08, "fix the credit union
# party split"). The employees' basis is untouched.
# Telecom (2026-10-08, coordinator call consistent with D-117): the published panel's 8 telecom PACs, the same code path as the credit unions' 16 (the anchors have no roster PAC for Telecom).
PANEL_COHORT = {'credit_unions': 'credit_unions', 'telecom': 'telecom'}      # chart industry -> PAC panel sector
SPLIT_FILE = Path(__file__).resolve().parents[1] / 'forecast' / 'anchors' / 'panel_cohort_split.json'          # the credit unions'; the other cohorts have a file of their own
SPLIT_FILES = {'telecom': SPLIT_FILE.with_name('panel_cohort_split_telecom.json')}
SPLIT_SCHEMA = 'panel-cohort-party-split-v1'
PANEL_LABELS = {'credit_unions': 'Credit unions: {n} PACs (published panel cohort)', 'telecom': 'Telecom: {n} PACs (published panel cohort)'}
PANEL_NAMES = {'credit_unions': 'credit-union', 'telecom': 'Telecom'}
PANEL_MEASURE_LABEL = 'Panel cohort PACs: own giving to House and Senate candidates'
ROSTER_LABELS = {'commercial_banks': 'Banks: {n} bank companies'}
PANEL_NOTICE = ("Company PACs: {label}, observed to {through} (the panel's own date), and so is their party split. General giving and the employees' party split are observed to {general}. "
                "The two dates are never mixed in one figure.")


def _panel_block(ident, open_cycle):
    """(block, None) for the `pac_to_candidates` block built from the published PAC panel's current frame of this industry's sector, or (None, why it is unavailable).
    Shaped like the anchors' block where the cycle model reads it, plus `panel`: the cut, the integer-cent history `_r8` takes and the identity of the bundle and cohort."""
    sector = PANEL_COHORT[ident]
    try:
        import pac_giving_forecast as panel
        published = panel.catalog()
    except (ImportError, OSError, ValueError):
        return None, 'The published PAC panel could not be read, so the ' + PANEL_NAMES.get(ident, ident) + ' company PACs cannot be shown.'
    item = next((s for s in published['sectors'] if s['sector'] == sector), None)
    now = item and item['current']
    origin = item and next((f for f in item['forecasts'] if f['target'] == open_cycle and f['product'] == 'ORIGIN' and f['status'] == 'ready'), None)
    if not now or now['target'] != open_cycle or now['product'] == 'ORIGIN' or now['status'] != 'ready' or not origin:
        return None, 'The published PAC panel has no current ' + _period(open_cycle) + ' nowcast for this industry.'
    trace = now['trace']
    history = trace['history']
    cycles = {str(h['cycle']): {'to_house_senate': h['total_cents'] / 100.} for h in history}
    odd = origin['trace']['annual'].get(open_cycle - 3)          # the anchor cycle's odd-year calendar piece (the budget rule's s_odd)
    cut = now['cut']
    observed = dict(through=cut, to_house_senate=now['observed_cents'] / 100.)
    split, why = _cohort_split(item, now, open_cycle - 2)          # the D/R of the same PACs: the party split's company-PAC basis divides this block, not the anchors' roster
    if split:
        cycles[str(open_cycle - 2)].update(split['anchor'])
        observed.update(split['observed'])
    return dict(status='ready', reason=None, single_pac=False, committee_count=item['cohort']['count'], committees=[], cycles=cycles, split_reason=why,
                odd_year=None if odd is None else {str(open_cycle - 2): {'to_house_senate': odd / 100.}},
                observed=[observed], coverage_end=cut,
                panel=dict(cut=cut, window=[h['cycle'] for h in history], observed_nominal=now['observed_cents'] / 100., pairs=[(h['observed_cents'], h['total_cents']) for h in history],
                           published_nowcast_nominal=now['prediction_cents'] / 100., bundle_sha256=published['bundle_sha256'], cohort=item['cohort'])), None


def _cohort_split(item, now, anchor):
    """({anchor: {House: {D, R}, Senate: {D, R}}, observed: {House: {D, R}, Senate: {D, R}}} in nominal dollars, None) or (None, why it is unavailable): the pinned D/R split of the panel's
    cohort (build_panel_cohort_split.py). It is taken only when it divides the very population the panel's dollars are: the same cohort and cut, the anchor cycle's total and the observed
    amount equal to the cent, and every bucket (D, R, other / unknown recipients, the lane's unresolved-office pool) adding up to them."""
    why = ('The D/R split of the panel cohort (forecast/anchors/panel_cohort_split' + ('' if item['sector'] == 'credit_unions' else '_' + item['sector']) + '.json) is missing or does not match the published PAC panel (cohort, cut or totals), so the company-PAC '
           'party split cannot be shown. Rebuild it with forecast/anchors/build_panel_cohort_split.py.')
    try:
        data = json.loads(SPLIT_FILES.get(item['sector'], SPLIT_FILE).read_text(encoding='utf-8'))
        total = next(h['total_cents'] for h in now['trace']['history'] if h['cycle'] == anchor)
        cycle, seen = data['cycles'][str(anchor)], data['observed']

        def add(b):
            return sum(b[c][k] for c in CHAMBERS for k in ('D', 'R', 'Other', 'Unknown')) + b['unresolved_office_pool']

        if (data['schema'], data['cohort_ids_sha256'], seen['through']) != (SPLIT_SCHEMA, item['cohort']['ids_sha256'], now['cut']) or (add(cycle), add(seen)) != (total, now['observed_cents']):
            return None, why
        dollars = lambda b: {c: dict(D=b[c]['D'] / 100., R=b[c]['R'] / 100.) for c in CHAMBERS}
        return dict(anchor=dollars(cycle), observed=dollars(seen)), None
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return None, why


def _panel_blocks(ident, open_cycle):
    """{measure id: block} to read in place of the anchors' own: the company PACs of an industry that is on the published panel's cohort; {} for the others."""
    if ident not in PANEL_COHORT:
        return {}
    block, why = _panel_block(ident, open_cycle)
    return {'pac_to_candidates': block or dict(status='unavailable', reason=why, single_pac=False, cycles={}, observed=[])}


def _population(ident, block):
    """What a company-PAC total is a total of, named for the page: the chart roster's committees or the published panel's cohort."""
    n, panel = block['committee_count'], block.get('panel')
    if panel:
        return dict(kind='panel_cohort', count=n, label=PANEL_LABELS[ident].format(n=n), observed_to=panel['cut'], bundle_sha256=panel['bundle_sha256'], cohort_ids_sha256=panel['cohort']['ids_sha256'])
    return dict(kind='chart_roster', count=n, label=ROSTER_LABELS.get(ident, '{n} roster PACs').format(n=n))


def _company_pacs(ident, anchored, blocks):
    """The catalog's statement of what an industry's company-PAC account is, with its own 'observed to' date; None when the industry has no company PACs."""
    block = blocks.get('pac_to_candidates') or anchored['pac_to_candidates']
    if block['status'] != 'ready':
        return dict(kind='panel_cohort', status='unavailable', reason=block['reason']) if ident in PANEL_COHORT else None
    return dict(_population(ident, block), status='ready', observed_to=block.get('coverage_end') or anchored['coverage']['coverage_end'])


def _cycle_pac(ctx, assumptions, cycle, chains):
    """The PAC block of one horizon under the cycle model. `chains` carries {measure id: open-cycle layer distribution} from the open cycle to the next one."""
    cm, ind, snap, panel = ctx['cm'], ctx['industry'], ctx['snap'], ctx.get('panel', {})
    k = (cycle - ctx['anchor_cycle']) // 2
    out = dict(status='needs_inputs', anchor_cycle=ctx['anchor_cycle'], steps=k, chained=k > 1, measures=[], slope=None, ordinary_p=None, model_kind='cycle_model',
               interval_kind='Prediction interval of the phase-and-drift step (coefficient of the presidential-cycle term and the anchored-step residual); it excludes the error of the IBISWorld growth path')
    if not ind['capabilities']['pac']:
        return out | dict(status='withheld', reason=ind['withheld'].get('pac') or 'The PAC model is withheld for this industry.')
    if ctx['current'] != ctx['open_cycle'] or k not in (1, 2):
        return out | dict(status='unavailable', reason='The cycle model projects the open cycle of the anchor snapshot (' + _period(ctx['open_cycle']) +
                          ') and the cycle after it; this as-of date is outside them.')
    value = _number(assumptions.get('growth_pct'), 'Industry revenue growth', -99.9, 500)
    path, info = _growth_input(cm, ind['id'], cycle, value)
    if path is None:
        return out | dict(status='unavailable', reason=info['reason'])
    out.update(growth_input=info, growth_pct=info['used_pct'])
    wstatus, wreason = _window(ctx, cycle)
    anchor = ctx['anchor_cycle']
    nominal_factor = _nominal_factor(ctx, cycle)
    for mid, label in PAC_MEASURES:
        block = panel.get(mid) or snap[mid]          # D-117: the credit unions' company PACs are the published panel's cohort
        measure = dict(id=mid, label=label, status='ready', flags=[])
        if mid == 'pac_to_candidates' and block.get('committee_count'):
            measure['population'] = _population(ind['id'], block)
            if block.get('panel'):
                measure['label'] = PANEL_MEASURE_LABEL          # not "roster companies": the cohort is the panel's
        params, step = cm['measures'][mid], cm['measures'][mid]['step'].get(ind['id'])
        level, odd = _measure_level(block, mid, anchor), _measure_odd(block, mid, anchor)
        if block['status'] != 'ready':
            out['measures'].append(measure | dict(status='unavailable', reason=block['reason']))
            continue
        if level is None or level <= 0:
            out['measures'].append(measure | dict(status='unavailable', reason='The snapshot has no ' + str(anchor) + ' level for this measure.'))
            continue
        if step is None:
            out['measures'].append(measure | dict(status='unavailable', reason='The model package has no residual variance for this industry and measure.'))
            continue
        if odd is None:
            out['measures'].append(measure | dict(status='unavailable', reason='Rebuild the anchors: the budget rule needs the ' + str(anchor) + ' cycle\'s odd-year split.'))
            continue
        base, s_odd = level * ctx['multiplier'], odd / level
        x, g_odd, g_even = _budget_change(s_odd, path)
        growth_applies = mid not in PANEL_NOWCAST
        if not growth_applies:          # D-116: no 50/50 for the company PACs, the budget factor is 1 on both horizons
            x = 0.
        d_p, phi, se, drift = _pres(cycle) - _pres(cycle - 2), params['phi'], params['phi_se'], params['drift']['d'][str(k)]
        factor = 1. + x / 2.
        uplift = _presidential_uplift(mid)          # D-118/D-119/D-121: log u in place of the fitted phi; company PACs on the presidential step only, general giving on both (symmetric)
        uplifted = uplift is not None and (d_p > 0 or mid in UPLIFT_BOTH_STEPS)
        phi_fitted, phi = phi, (math.log(uplift['factor']) if uplifted else phi)
        model = dict(B_real_2025=base, dP=d_p, phi=phi, drift=drift, h=k, s_odd=s_odd, G_odd=g_odd, G_even=g_even, growth_by_year={str(y): g for y, g in path},
                     budget_change=x, budget_growth_pct=100. * x, budget_growth_source=info['source'], budget_factor=factor, growth_applies=growth_applies)
        if uplifted:
            model.update(phi_fitted=phi_fitted, presidential_uplift=uplift)
        measure.update(anchor_cycle_nominal=level, anchor_cycle_real_2025=base, model=model)
        crit = step['critical_95']
        if k == 1:
            point = base * math.exp(phi * d_p + drift) * factor
            half = crit * math.sqrt(d_p ** 2 * se ** 2 + step['sigma2'])
            coef_half = _t_crit(params['phi_df']) * abs(d_p) * se
            values = dict(projected_cycle_real_2025=point, cycle_coefficient_low=point * math.exp(-coef_half), cycle_coefficient_high=point * math.exp(coef_half),
                          cycle_prediction_low=point * math.exp(-half), cycle_prediction_high=point * math.exp(half))
            measure.update(values)
            measure['layer'] = None
            dist, layer_name = None, 'model'
            if mid == 'pac_to_candidates' and block['single_pac']:
                measure['flags'].append('single_pac_thin_base')
                measure['flag_text'] = 'One PAC (' + block['committees'][0]['company'] + '): a thin base, not the industry\'s PAC giving.'
            if wstatus and wstatus != 'window':
                measure.update(remainder_status=wstatus, remainder_reason=wreason)
            elif wstatus:
                cut = _effective_cut(block['observed'], ctx['as_of'], block.get('coverage_end', ctx['coverage_end']))          # the panel's cut is its own (09-26), not general giving's
                if cut is None:
                    measure.update(remainder_status='no_observed', remainder_reason='The as-of date precedes the first observed Mentat month-end.')
                else:
                    observed = cut['amount'] if mid == 'general_giving' else cut['to_house_senate']
                    observed_real = observed * ctx['open_multiplier']
                    measure.update(observed=dict(through=cut['through'], amount_nominal=observed, amount_real_2025=observed_real, face_value=ctx['face_value']),
                                   remainder_starts=_next_day(cut['through']),
                                   **_remainder(point, dict(remaining_coefficient_low=values['cycle_coefficient_low'], remaining_coefficient_high=values['cycle_coefficient_high'],
                                                            remaining_prediction_low=values['cycle_prediction_low'], remaining_prediction_high=values['cycle_prediction_high']),
                                                observed_real))
                    scale = half / crit
                    nowcast = None
                    if _open_layer(cm, mid) == 'R8':
                        nowcast, why = _r8(cm, ind['id'], block, mid, ctx['open_cycle'], anchor, cut['through'], ctx['open_multiplier'], observed_real)
                        if nowcast is None:
                            measure['flags'].append('nowcast_unavailable_pace')
                            measure['nowcast_reason'] = why
                    if nowcast:
                        measure.update(nowcast['layer'], layer='R8', nowcast=nowcast['info'])
                        if nowcast['info']['floored_at_observed']:
                            measure['flags'].append('nowcast_floored_at_observed')
                        dist, layer_name = nowcast['dist'], 'R8'
                    else:
                        given = _given_observed(point, half, crit, step['df'], observed_real)
                        if given:
                            measure.update(given, layer='P1')
                            dist, layer_name = _layer_dist(math.log(point), scale, step['df'], observed_real), 'P1'
                    if measure.get('cycle_given_observed_median') is not None and point < observed_real:
                        measure['flags'].append('observed_exceeds_unconditional_projection')
            chains[mid] = (dist, layer_name) if dist else (_layer_dist(math.log(point), half / crit, step['df'], 0.), 'model')
        else:
            layer, layer_name = chains.get(mid, (None, None))
            if layer is None:
                out['measures'].append(measure | dict(status='unavailable', reason='The next cycle chains from the open cycle\'s projection, which is not available for this measure.'))
                continue
            mu2 = phi * d_p + drift + math.log(factor)
            low, median, high = _chain(layer, mu2, math.sqrt(se ** 2 + step['sigma2']), step['df'])
            measure.update(projected_cycle_real_2025=median, cycle_coefficient_low=None, cycle_coefficient_high=None, cycle_prediction_low=low, cycle_prediction_high=high,
                           chained_from=dict(layer=layer_name, cycle=cycle - 2))
            if layer_name == 'R8':
                measure['interval_kind'] = R8_CHAIN_KIND
        measure['nominal'] = _nominal(measure, nominal_factor, cm['inflation'])
        out['measures'].append(measure)
    ready = [m for m in out['measures'] if m['status'] == 'ready']
    if ready:
        out['status'] = 'ready'
    else:
        out.update(status='unavailable', reason=(out['measures'][0].get('reason') if out['measures'] else None) or 'No measure is available for this industry.')
    return out


def _cycle_response_info(cm):
    return dict(version=cm['version'], inflation=cm['inflation'], layers={mid: _open_layer(cm, mid) for mid, _l in PAC_MEASURES}, notes=list(cm['notes']),
                growth_applies={mid: mid not in PANEL_NOWCAST for mid, _l in PAC_MEASURES})


FACE_VALUE_FLAG = re.compile(r'^No (\d{4}) CPI exists yet: .* compared at face value\.$')


def _cycle_flags(flags, infl):
    """The open cycle has no CPI yet; under the cycle model its observed amounts are deflated at the expected inflation, not compared at face value."""
    rate = '%.7f' % (100. * infl['annual_rate'])
    out = []
    for text in flags:
        match = FACE_VALUE_FLAG.match(text)
        out.append(('No %s CPI exists yet: %d-%s observed amounts are deflated at the expected %s%%/yr (cycle mid-point).' % (match.group(1), int(match.group(1)) - 1, match.group(1), rate))
                   if match else text)
    return out


# --- request -> response ---------------------------------------------------------------------------------------------------
def _project_v2(request, b):
    sector = request.get('industry')
    offered = {r['id']: r for r in _v2_offered(b)[0]}
    if not isinstance(sector, str) or sector not in offered:
        raise ValueError('Choose a supported research industry. Company and subindustry models are not interchangeable.')
    industry = offered[sector]
    as_of = _date(request.get('as_of') or date.today().isoformat(), 'As-of date')
    if as_of > date.today():
        raise ValueError('The as-of date cannot be in the future.')
    if as_of < _model_vintage(b):
        raise ValueError('The model package was not available at that historical cutoff.')
    if request.get('signal', 'control') != 'control':
        raise ValueError(SEATS_TEXT)
    varying = request.get('industry_slopes', False)
    if not isinstance(varying, bool):
        raise ValueError('Industry slopes must be true or false.')
    if varying:
        raise ValueError('Industry slopes follow the rule the model package records; they are not selectable.')
    note = _text(request.get('assumption_source'), 'Assumption source') or 'User scenario assumptions; not an automatically updated live forecast'
    known = _date(request.get('assumption_date') or as_of.isoformat(), 'Assumption date')
    if known > as_of:
        raise ValueError('Assumptions cannot be dated after the forecast cutoff.')
    current = as_of.year + as_of.year % 2
    horizons = _mapping(request.get('horizons'), 'Horizon assumptions')
    if set(horizons) - {str(current), str(current + 2)}:
        raise ValueError('Only the current cycle remainder and next complete cycle are supported.')
    if b.get('cycle_model') and request.get('growth_basis') != GROWTH_BASIS and any(       # a stale page's cycle revenue growth must not be read as the IBIS-window average
            _mapping(h, 'Horizon assumptions').get('growth_pct') not in (None, '') for h in horizons.values()):
        raise ValueError(STALE_PAGE_TEXT)
    _finite(request, 'Forecast request')
    head = dict(schema_version=2, product='Mentat-anchored industry scenarios', model_version=b['model_version'], model_sha256=b['sha256'], industry=sector,
                industry_label=industry['label'], industry_code=industry['code'], scope=industry['scope'], currency=b['currency'], as_of=as_of.isoformat(),
                assumption_source=note, assumption_date=known.isoformat(), inputs=request, inference=b['inference'], interval_labels=b['interval_labels'],
                interval_label=b['interval_labels']['coefficient'] + '; ' + b['interval_labels']['prediction'])
    try:
        snapshot, entry = _anchor_industry(sector)
    except AnchorProblem as problem:
        return head | dict(status=problem.status, reason=str(problem), horizons=[], notices=[str(problem)])
    open_cycle = entry['coverage']['current_cycle']
    multiplier = snapshot['cpi']['multipliers'][str(open_cycle - 2)]
    open_multiplier = snapshot['cpi']['multipliers'].get(str(open_cycle))
    cm = b.get('cycle_model')
    panel = _panel_blocks(sector, open_cycle) if cm and industry['capabilities']['pac'] else {}          # D-117: measure blocks read in place of the anchors' own (the panel cohorts' company PACs)
    ctx = dict(b=b, industry=industry, snap=entry, as_of=as_of, current=current, open_cycle=open_cycle, anchor_cycle=open_cycle - 2, panel=panel,
               coverage_end=entry['coverage']['coverage_end'], threshold=snapshot['thin_base_threshold_usd'], multiplier=multiplier,
               open_multiplier=open_multiplier or 1., face_value=open_multiplier is None, snap_cpi=snapshot['cpi']['multipliers'], cm=cm, nowcast_errors=snapshot.get('lda_nowcast_errors'))
    if cm:       # the open cycle has no CPI yet: its observed amounts are deflated at the expected inflation instead of compared at face value
        ctx.update(open_multiplier=_real_multiplier(snapshot, cm['inflation'], open_cycle), face_value=False)
    chains = {}
    changes_all = {}
    for cycle in (current, current + 2):
        changes_all.update(_mapping(_mapping(horizons.get(str(cycle)), str(cycle) + ' assumptions').get('annual_forecast_changes'), 'Annual growth-forecast changes'))
    output = []
    for cycle in (current, current + 2):
        a = _mapping(horizons.get(str(cycle)), str(cycle) + ' assumptions')
        prior = a.get('prior_growth_pct')
        if prior is None or prior == '':
            history = next((r for r in industry['history'] if r['cycle'] == cycle - 2), None)
            if history:
                prior = history['growth_pct']
            elif cm:       # the share models' prior-cycle growth of a cycle with no history row: the IBIS cycle growth, no longer the removed slider
                prior = (cm['g_prior'].get(industry['id']) or {}).get(str(cycle))
            else:
                prior = _mapping(horizons.get(str(cycle - 2)), 'Previous horizon').get('growth_pct')
        prior = _number(prior, 'Prior cycle revenue growth', -99.9, 500)
        growth = None if cm else _number(a.get('growth_pct'), 'Cycle revenue growth', -99.9, 500)       # with a cycle model growth_pct is the Industry revenue growth input of the PAC block
        pac = _cycle_pac(ctx, a, cycle, chains) if cm else _v2_pac(ctx, a, cycle)
        wstatus, _reason = _window(ctx, cycle)
        through = _cut_through(ctx) if wstatus == 'window' else None
        result = dict(cycle=cycle, period=_period(cycle), horizon='current_remainder' if cycle == current else 'next_complete',
                      starts=(_next_day(through) if through else (((as_of + timedelta(days=1)).isoformat() if as_of < date(cycle, 12, 31) else None)
                                                                   if cycle == current else f'{cycle - 1}-01-01')),
                      ends=f'{cycle}-12-31', prior_growth_pct=prior, coverage_through=through, pac=pac, shares={})
        for chamber in CHAMBERS:
            result['shares'][chamber] = _v2_chamber(ctx, a, cycle, chamber, prior, growth, pac)
        result['lobbying'] = _v2_lobbying(ctx, changes_all, cycle)
        output.append(result)
    pb = panel.get('pac_to_candidates')
    panel_notice = [PANEL_NOTICE.format(label=_population(sector, pb)['label'], through=pb['coverage_end'], general=entry['coverage']['coverage_end'])] if pb and pb['status'] == 'ready' else []
    return head | dict(status='ready', anchors=dict(built_at_utc=snapshot['built_at_utc'], workbook=entry['mentat']['workbook'],
                                                         workbook_sha256=entry['mentat']['workbook_sha256'], index_token=entry['mentat']['index_token'],
                                                         dataset=entry['mentat']['dataset'], collection_date=entry['coverage']['collection_date'],
                                                         coverage_end=entry['coverage']['coverage_end'], rule_coverage_end=entry['coverage']['rule_coverage_end'],
                                                         coverage_reason=entry['coverage']['reason'], anchor_cycle=ctx['anchor_cycle'], current_cycle=open_cycle,
                                                         flags=(_cycle_flags(entry['coverage']['flags'] + entry['flags'], cm['inflation']) if cm else entry['coverage']['flags'] + entry['flags']),
                                                         thin_base_threshold_usd=ctx['threshold'],
                                                         cpi_multiplier=multiplier, face_value_current_cycle=ctx['face_value'], measures=entry['status'],
                                                         **({'cpi_multiplier_current_cycle': ctx['open_multiplier']} if cm else {})),
                       **({'cycle_model': _cycle_response_info(cm)} if cm else {}),
                       horizons=output,
                       notices=['Levels come from Mentat\'s own data for the tracked companies; OpenSecrets whole-industry data only estimated the relationships.',
                                'PAC, candidate-share and lobbying amounts are separate accounts and must not be added. The two lobbying series are never summed.',
                                'Observed amounts run to the Mentat coverage end (declared by rule) and the remainder starts the day after; rows dated later stay in the remainder.',
                                'Historical associations and scenario inputs are not validated future-giving predictions; the prediction interval adds the residual variance of the anchored step.']
                       + panel_notice + list(industry.get('caveats', [])))
