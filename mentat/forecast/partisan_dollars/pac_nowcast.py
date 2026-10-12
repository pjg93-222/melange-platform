"""Offline PAC-only House/Senate D/R dollar nowcasts on preserved DUE ledgers.

No application import, publication, downloads, or model fitting at import time.
Latest amendments, retrospective fixed cohorts and candidate masters prevent an
original-vintage accuracy claim. Issue horizons are reported separately.
"""
from __future__ import annotations
import argparse
import csv
from datetime import date
import hashlib
import json
import math
from pathlib import Path
from statistics import median

SECTORS = ('commercial_banks', 'credit_unions', 'property_casualty', 'credit_arms', 'telecom')
CHAMBERS = ('House', 'Senate')
PARTIES = ('D', 'R')
YEARS = tuple(range(2012, 2025, 2))
TARGETS = (2018, 2020, 2022, 2024)
PRODUCTS = ('m12', 'm15', 'm18', 'm21')
FAMILIES = ('prior_party', 'party_r12_remaining', 'party_pace', 'historical_remainder',
            'r12_pace_equal_blend', 'chamber_r12_remaining')
PROTOCOL = dict(version='pac-partisan-dollars-offline-v1', sectors=SECTORS, targets=TARGETS,
                products=PRODUCTS, families=FAMILIES, inner_minimum=2,
                selector='mean of ten cohort-party WAPEs within chamber and issue horizon',
                initial_policy='party_r12_remaining', domain_fallback='known prior-party dollars; never missing as zero',
                completion='min(1, geometric mean of exactly three historical observed/final ratios)',
                availability='DUE or following January 31; latest amended edition, not original-vintage data',
                scope='fixed five selected PAC cohorts, signed nominal candidate contributions, classified D/R separately',
                publication=False)


class DataError(ValueError):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def schedule(year, product):
    if product == 'm12':
        return f'{year}-01-31', f'{year-1}-12-31'
    month, day, cut = {'m15': (4, 20, '03-31'), 'm18': (7, 20, '06-30'), 'm21': (10, 20, '09-30')}[product]
    return f'{year}-{month:02d}-{day:02d}', f'{year}-{cut}'


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


class Panel:
    def __init__(self, rows, presence):
        self.rows = {}
        self.presence = {}
        for r in presence:
            key = r['sector'], int(r['cycle']), r['chamber']
            if key in self.presence:
                raise DataError('duplicate presence key')
            if str(r.get('ledger_reconciled','')).lower() != 'true' or str(r.get('party_classification_admissible','')).lower() != 'true' or int(r.get('party_blocked_rows',-1))!=0:
                raise DataError('unreconciled or unclassified input cell: ' + '/'.join(map(str,key)))
            self.presence[key] = r['first_due_any_raw'] or None
        expected = {(s,y,c) for s in SECTORS for y in YEARS for c in CHAMBERS}
        if set(self.presence) != expected:
            raise DataError('presence must cover every pinned cohort/cycle/chamber')
        seen=set()
        for original in rows:
            if int(original['hs']) != 1:
                continue
            r = dict(original, cycle=int(original['cycle']), cents=int(original['cents']))
            key=(r['sector'],r['cycle'],int(r['source_record_order']))
            if key in seen:raise DataError('duplicate counted source row')
            seen.add(key)
            if r['sector'] not in SECTORS or r['cycle'] not in YEARS or r['chamber'] not in CHAMBERS or r['party'] not in (*PARTIES, 'Other', 'Unknown'):
                raise DataError('unsupported ledger identity')
            if r['party']=='Unknown':
                raise DataError('unclassified House/Senate recipient cannot enter an accuracy score')
            for field in ('date', 'due_available'):
                if date.fromisoformat(r[field]).isoformat() != r[field]:
                    raise DataError('noncanonical ledger date')
            if not f"{r['cycle']-1}-01-01" <= r['date'] <= f"{r['cycle']}-12-31":
                raise DataError('transaction outside its cycle')
            self.rows.setdefault((r['sector'],r['cycle'],r['chamber'],r['party']), []).append(r)

    def amount(self, sector, year, chamber, party, issue=None, cut=None):
        known = self.presence[sector, year, chamber]
        if known is None or (issue is not None and known > issue):
            raise DataError(f'missing sector presence at issue: {sector}/{year}/{chamber}/{issue}')
        return sum(r['cents'] for r in self.rows.get((sector,year,chamber,party), [])
                   if (issue is None or r['due_available'] <= issue) and (cut is None or r['date'] <= cut))

    def frame(self, sector, chamber, year, product):
        issue, cut = schedule(year, product)
        history = []
        for old in (year-6,year-4,year-2):
            old_issue, old_cut = schedule(old, product)
            history.append(dict(cycle=old,
                                final={p:self.amount(sector,old,chamber,p,issue=issue) for p in PARTIES},
                                observed={p:self.amount(sector,old,chamber,p,issue=old_issue,cut=old_cut) for p in PARTIES}))
        return dict(sector=sector, chamber=chamber, target=year, product=product,
                    issue=issue, cut=cut, history=history,
                    observed={p:self.amount(sector,year,chamber,p,issue=issue,cut=cut) for p in PARTIES})


def completion(final, observed):
    if len(final) != 3 or len(observed) != 3 or any(not math.isfinite(x) or x <= 0 for x in final) or any(not math.isfinite(x) or x < 0 for x in observed):
        raise DataError('three positive historical totals and nonnegative observations required')
    ratios = [o/f for f,o in zip(final,observed)]
    raw = 0.0 if any(x == 0 for x in ratios) else math.exp(sum(math.log(x) for x in ratios)/3)
    return min(1.0,raw), ratios, raw > 1


def predict(frame, family):
    """Only the supplied feature frame enters prediction; no target truth accepted."""
    if family not in FAMILIES or set(frame) != {'sector','chamber','target','product','issue','cut','history','observed'}:
        raise DataError('invalid model or frame contract')
    expected = [frame['target']-6,frame['target']-4,frame['target']-2]
    if [h['cycle'] for h in frame['history']] != expected or schedule(frame['target'],frame['product']) != (frame['issue'],frame['cut']):
        raise DataError('history or horizon mismatch')
    for block in [frame['observed']] + [h[k] for h in frame['history'] for k in ('final','observed')]:
        if set(block) != set(PARTIES) or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in block.values()):
            raise DataError('known finite D/R amounts required')
    result = []
    for party in PARTIES:
        fs = [h['final'][party] for h in frame['history']]
        os = [h['observed'][party] for h in frame['history']]
        b, observed = fs[-1], frame['observed'][party]
        reason, detail, point = None, {}, float(b)
        try:
            if family == 'prior_party':
                pass
            elif family == 'historical_remainder':
                point = observed + median(f-o for f,o in zip(fs,os))
                if point < 0:
                    raise DataError('negative historical-remainder prediction')
            else:
                if family == 'chamber_r12_remaining' and (any(v<0 for v in frame['observed'].values()) or any(v<0 for v in frame['history'][-1]['final'].values())):
                    raise DataError('joint chamber formula requires nonnegative current observations and prior-party amounts')
                if observed < 0:
                    raise DataError('negative current cumulative observation')
                if family == 'chamber_r12_remaining':
                    totals = [sum(h['final'].values()) for h in frame['history']]
                    partials = [sum(h['observed'].values()) for h in frame['history']]
                    s, ratios, cap = completion(totals,partials)
                else:
                    s, ratios, cap = completion(fs,os)
                detail = dict(completion_share=s, historical_ratios=ratios, completion_capped=cap)
                r12 = observed + (1-s)*b
                if family in ('party_r12_remaining','chamber_r12_remaining'):
                    point = r12
                else:
                    if s == 0:
                        raise DataError('zero historical completion makes pace division undefined')
                    pace = observed/s
                    point = pace if family == 'party_pace' else (pace+r12)/2
                if point < 0 or not math.isfinite(point):
                    raise DataError('negative or nonfinite formula prediction')
        except DataError as error:
            point, reason = float(b), str(error)
        result.append(dict(sector=frame['sector'],chamber=frame['chamber'],target=frame['target'],
                           product=frame['product'],issue=frame['issue'],cut=frame['cut'],family=family,
                           party=party,prediction_cents=point,observed_cents=observed,
                           prior_cents=b,training_years=expected,fallback_reason=reason,trace=detail))
    if family=='chamber_r12_remaining':
        fallback=[r['fallback_reason'] is not None for r in result]
        if any(fallback) and not all(fallback):
            raise DataError('joint formula may not mix fallback and formula parties')
        total=(sum(h['prior_cents'] for h in result) if all(fallback) else
               sum(frame['observed'].values())+(1-result[0]['trace']['completion_share'])*sum(h['prior_cents'] for h in result))
        if not math.isclose(sum(r['prediction_cents'] for r in result),total,rel_tol=1e-12,abs_tol=1e-8):
            raise DataError('joint D+R total failed to reconcile')
        for r in result:r['trace']['chamber_total_prediction_cents']=total
    return result


def score(rows,panel,truth_issue=None):
    out=[]
    for row in rows:
        actual=panel.amount(row['sector'],row['target'],row['chamber'],row['party'],issue=truth_issue)
        error=row['prediction_cents']-actual
        ape=None if actual==0 else abs(error)/abs(actual)
        out.append(dict(row,actual_cents=actual,error_cents=error,absolute_error_cents=abs(error),
                        ape=ape,within10=None if ape is None else ape<=0.1+1e-12,
                        within20=None if ape is None else ape<=0.2+1e-12))
    return out


def metrics(rows):
    denominator=sum(abs(r['actual_cents']) for r in rows)
    absolute=sum(r['absolute_error_cents'] for r in rows)
    positive=[r for r in rows if r['actual_cents']>0]
    nonzero=[r for r in rows if r['ape'] is not None]
    strata={}
    for r in rows:
        strata.setdefault((r['sector'],r['party']),[]).append(r)
    wapes=[sum(r['absolute_error_cents'] for r in rs)/sum(abs(r['actual_cents']) for r in rs)
           if sum(abs(r['actual_cents']) for r in rs) else None for rs in strata.values()]
    return dict(n=len(rows),positive_actual_n=len(positive),zero_actual_n=sum(r['actual_cents']==0 for r in rows),
                negative_actual_n=sum(r['actual_cents']<0 for r in rows),
                within10_count=sum(r['within10'] is True for r in positive),
                within20_count=sum(r['within20'] is True for r in positive),
                within10_rate=None if not positive else sum(r['within10'] is True for r in positive)/len(positive),
                within20_rate=None if not positive else sum(r['within20'] is True for r in positive)/len(positive),
                signed_nonzero_n=len(nonzero),
                signed_nonzero_within10_count=sum(r['within10'] is True for r in nonzero),
                signed_nonzero_within20_count=sum(r['within20'] is True for r in nonzero),
                all_positive_within20=bool(positive) and all(r['within20'] for r in positive),
                dollar_mae=absolute/len(rows)/100 if rows else None,
                signed_dollar_bias=sum(r['error_cents'] for r in rows)/len(rows)/100 if rows else None,
                wape=None if not denominator else absolute/denominator,
                macro_wape=None if not wapes or any(v is None for v in wapes) else sum(wapes)/len(wapes),
                max_ape=max((r['ape'] for r in nonzero),default=None),
                negative_prediction_count=sum(r['prediction_cents']<0 for r in rows),
                fallback_count=sum(r['fallback_reason'] is not None for r in rows),
                exact_zero_count=sum(r['actual_cents']==0 and r['prediction_cents']==0 for r in rows),
                zero_actual_false_positive_count=sum(r['actual_cents']==0 and r['prediction_cents']!=0 for r in rows),
                zero_actual_false_positive_dollars=sum(abs(r['prediction_cents']) for r in rows if r['actual_cents']==0)/100)


def choose(prior_scores):
    if set(prior_scores)!=set(FAMILIES):
        raise DataError('selection requires all six frozen families')
    common=None
    for rows in prior_scores.values():
        keys={(r['sector'],r['party'],r['target']) for r in rows}
        if common is None:common=keys
        elif keys!=common:raise DataError('family selection origin grids differ')
        if not rows:continue
        origins={r['target'] for r in rows}
        expected={(s,p,y) for s in SECTORS for p in PARTIES for y in origins}
        if keys!=expected or len(keys)!=len(rows) or len({r['chamber'] for r in rows})!=1 or len({r['product'] for r in rows})!=1:
            raise DataError('selection requires exactly ten cohort-party strata and a common origin grid')
    valid={f:metrics(rows)['macro_wape'] for f,rows in prior_scores.items() if rows}
    valid={f:v for f,v in valid.items() if v is not None and math.isfinite(v)}
    if not valid:
        return 'party_r12_remaining'
    best=min(valid.values())
    return next(f for f in FAMILIES if f in valid and valid[f]<=best+1e-12)


def evaluate(panel):
    rows, failures, selections, final=[],[],[],{}
    # Refuse missing admission/availability before scoring: every method uses
    # exactly the full grid, never a reduced collection of convenient origins.
    frames={(s,c,y,p):panel.frame(s,c,y,p) for s in SECTORS for c in CHAMBERS for y in TARGETS for p in PRODUCTS}
    for chamber in CHAMBERS:
        final[chamber]={}
        for product in PRODUCTS:
            past={f:[] for f in FAMILIES}
            complete_years=[]
            for year in TARGETS:
                issue=schedule(year,product)[0]
                earlier_scores={f:score(v,panel,truth_issue=issue) for f,v in past.items()}
                eligible=len(complete_years)>=2 and any(v and metrics(v)['macro_wape'] is not None for v in earlier_scores.values())
                selected=choose(earlier_scores) if len(complete_years)>=2 else 'party_r12_remaining'
                selections.append(dict(chamber=chamber,product=product,target=year,selected_family=selected,
                                       earlier_origins=list(complete_years),initial_policy=len(complete_years)<2,
                                       selection_eligible=eligible,
                                       default_reason='insufficient_inner_origins' if len(complete_years)<2 else ('undefined_macro_wape' if not eligible else None),
                                       selection_truth_issue=issue,
                                       selection_macro_wape={f:metrics(v)['macro_wape'] for f,v in earlier_scores.items() if v}))
                current={f:[] for f in FAMILIES}
                for sector in SECTORS:
                    try:
                        frame=frames[sector,chamber,year,product]
                        for family in FAMILIES:
                            scored=score(predict(frame,family),panel)
                            current[family].extend(scored)
                    except DataError as error:
                        failures.append(dict(chamber=chamber,product=product,target=year,sector=sector,reason=str(error)))
                if any(len(v)!=10 for v in current.values()):
                    raise DataError('incomplete common forecast grid; run refused: ' + json.dumps(failures))
                for family,scored in current.items():
                    rows.extend(dict(r,stage='fixed_candidate') for r in scored)
                    if family==selected:
                        rows.extend(dict(r,stage='nested_policy',selected_family=family,family='nested_policy') for r in scored)
                    past[family].extend(scored)
                complete_years.append(year)
            final_issue=min(schedule(2026,product)[0],'2026-10-05')
            final_scores={f:score(v,panel,truth_issue=final_issue) for f,v in past.items()}
            final[chamber][product]=dict(family=choose(final_scores),origins=complete_years,selection_truth_issue=final_issue,
                                        selection_eligible=len(complete_years)>=2 and any(v and metrics(v)['macro_wape'] is not None for v in final_scores.values()),
                                        status='offline_research_only',live_activation=False)
    groups={}
    for row in rows:
        for sector,party in ((row['sector'],row['party']),(row['sector'],'ALL'),('ALL',row['party']),('ALL','ALL')):
            key=row['stage'],row['chamber'],row['product'],row['family'],sector,party
            groups.setdefault(key,[]).append(row)
    summary=[dict(zip(('stage','chamber','product','family','sector','party'),k),**metrics(v)) for k,v in sorted(groups.items())]
    return dict(protocol=PROTOCOL,predictions=rows,metrics=summary,selections=selections,final_policy=final,
                failures=failures,failure_count=len(failures),live_activation=False)


def csv_bytes(rows):
    import io
    s=io.StringIO(newline='')
    fields=list(dict.fromkeys(k for row in rows for k in row))
    writer=csv.DictWriter(s,fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({k:json.dumps(v,sort_keys=True) if isinstance(v,(dict,list,tuple)) else v for k,v in row.items()})
    return s.getvalue().encode('utf-8-sig')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger',type=Path,required=True)
    parser.add_argument('--presence',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--protocol',type=Path,required=True)
    parser.add_argument('--protocol-sha256',required=True)
    args=parser.parse_args()
    if args.output.exists():
        raise DataError('output exists; overwrite refused')
    if hashlib.sha256(args.protocol.read_bytes()).hexdigest()!=args.protocol_sha256:
        raise DataError('protocol hash mismatch')
    result=evaluate(Panel(read_csv(args.ledger),read_csv(args.presence)))
    result['input_sha256']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.ledger,args.presence,args.protocol,Path(__file__))}
    args.output.mkdir(parents=True,exist_ok=False)
    for name,content in {'evaluation.json':canonical(result),'predictions.csv':csv_bytes(result['predictions']),
                         'metrics.csv':csv_bytes(result['metrics']),'selections.csv':csv_bytes(result['selections'])}.items():
        with (args.output/name).open('xb') as out:
            out.write(content)
    print(f"{len(result['predictions'])} PAC forecasts; {result['failure_count']} coverage failures; no publication")
    return int(bool(result['failure_count']))


if __name__=='__main__':
    raise SystemExit(main())
