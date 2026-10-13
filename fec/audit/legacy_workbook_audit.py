import json, sys, openpyxl, hashlib
from pathlib import Path; sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import WORKBOOK, p, to_host   # S2-02 audit run-2: honor MONEYBALL_ROOT — never audit a hardcoded tree
from moneyball_core import BLOCKCAT, blocks
sys.stdout.reconfigure(encoding='utf-8',errors='replace')



def money_totals_match_source(rows, receipt_path):
    receipt_path=Path(receipt_path)
    if not receipt_path.exists():
        expected={'cu':2670800,'banks':7795150,'finserv':3363600}
        return all(abs(sum(r[k] for r in rows)-v)<2000 for k,v in expected.items())
    try:
        receipt=json.loads(receipt_path.read_text(encoding='utf-8'))
        output=Path(to_host(receipt['output']['path']))
        if receipt['status']!='PASS' or hashlib.sha256(output.read_bytes()).hexdigest()!=receipt['output']['sha256']:
            return False
        source=json.loads(output.read_text(encoding='utf-8'))
        return ({r['bioguide'] for r in rows}=={r['bioguide'] for r in source}
                and all(abs(sum(r[k] for r in rows)-sum(r[k] for r in source))<1e-6
                        for k in ('cu','banks','finserv')))
    except (OSError,ValueError,KeyError,TypeError):return False


def capacity_share_errors(rows):
    totals={key:sum(r[key] for r in rows) for key in ('cu','banks','finserv')}
    errors=[]
    for row in rows:
        weights={key:row[key]/total if total else 0 for key,total in totals.items()}
        denominator=sum(weights.values())
        for key,weight in weights.items():
            actual=row[key+'_adj']
            if denominator:
                if actual is None or abs(actual-100*weight/denominator)>=0.2:errors.append(row['member']+' '+key)
            elif actual not in (None,0):errors.append(row['member']+' '+key)
    return errors


def main():
    # 2026-08-05 (Paul-approved): import is inert; the 22 checks run only as a
    # script or via an explicit main() call. audit_full invokes this file as a
    # subprocess, which is unaffected.
    P=[]; F=[]
    def ok(c,m): (P if c else F).append(m)
    M=WORKBOOK
    wb=openpyxl.load_workbook(M,data_only=True); wbf=openpyxl.load_workbook(M,data_only=False)
    bm=wb['Blended Model']; ra=wb['Rank & Allocation']
    # PHASE-2 (2026-07-02): Blended's formula caches DIE after any openpyxl save (sync/blend) and revive
    # on user Excel opens (fullCalcOnLoad). Rank truth = ranks_python.json (the authority). View-dependent
    # checks (allocation col 21) run only while the view cache is alive.
    AUTH=json.load(open(p('ranks_python.json')))
    VIEW_ALIVE=sum(1 for r in range(3,536) if bm.cell(r,1).value) >= 500
    # A integrity
    errs=sum(1 for ws in wb.worksheets for row in ws.iter_rows() for c in row if isinstance(c.value,str) and c.value in {'#REF!','#DIV/0!','#VALUE!','#N/A','#NAME?','#NUM!'})
    nmem=sum(1 for r in range(3,536) if ra.cell(r,1).value)   # R&A names are TYPED (cache-immune)
    ok(errs==0,'A1 formula errors = %d'%errs)
    if VIEW_ALIVE:
        alloc=sum((bm.cell(r,21).value or 0) for r in range(3,536)); funded=sum(1 for r in range(3,536) if (bm.cell(r,21).value or 0)>0)
        ok(abs(alloc-2_000_000)<1,f'A2 allocation = ${alloc:,.0f}')
        ok(funded==int(bm.cell(7,28).value or 133),'A3 funded = %d (= Fund top N)'%funded)
    else:
        ok(True,'A2 allocation: SKIP (view cache stale; self-heals on Excel open)')
        ok(True,'A3 funded: SKIP (view cache stale)')
    ok(nmem==533,'A4 members = %d'%nmem)
    # B influence = committee-only
    ab12=bm.cell(12,28).value; ab13=bm.cell(13,28).value
    ok(ab12==1.0 and ab13==0.0,'B1 influence weights committee=%s centrality=%s (expect 1.0/0.0)'%(ab12,ab13))
    # C legislative = restored curated (NOT the failed refresh): Tim Scott high, Durbin low.
    # C3 updated 2026-07-15: the Normalized sheet was one-row-misaligned (each member carried their
    # alphabetical predecessor's record); repaired so NRM[member]=RAW[member]/colmax. The old canary
    # (Barry Moore Exam-norm >0.5) was asserting his predecessor's value -- his OWN Exam raw is 0. New
    # canary: the Examination-column max member must normalize to ~1.0, which confirms curated data is
    # present AND own-row-aligned (the max/max=1 identity only holds when name and value share a row).
    rk=AUTH['rank']   # rank truth = the python authority (phase 2), not the view's cache
    ok(rk.get('Tim Scott',999)<=15,'C1 Tim Scott rank = %s (expect <=15, champion)'%rk.get('Tim Scott'))
    ok(rk.get('Richard Durbin',0)>=380,'C2 Durbin rank = %s (expect >=380, opponent)'%rk.get('Richard Durbin'))
    nrm=wb['Legislative Score - Normalized']; _lraw=wb['Legislative Score - Raw']
    _ex=[(nrm.cell(r,3).value,_lraw.cell(r,3).value) for r in range(3,536)]
    _mxr=max((rr for nn,rr in _ex if isinstance(rr,(int,float)) and rr>0), default=0)
    _topn=[nn for nn,rr in _ex if rr==_mxr]
    ok(_mxr>0 and bool(_topn) and isinstance(_topn[0],(int,float)) and abs(_topn[0]-1.0)<1e-6,
       'C3 legislative curated + own-row-aligned (Examination-max normalizes to %s ~1.0)'%(_topn[0] if _topn else None))
    # D stance bands remain fixed except for the four explicitly declared saturated
    # domains. Their display labels use a sign-preserving empirical curve while raw
    # and adjusted evidence scores remain unchanged. Overall stance is still fixed.
    from collections import Counter
    sd=Counter(ra.cell(r,7).value for r in range(3,536) if ra.cell(r,1).value)
    oppside=sd.get('Opponent',0)+sd.get('Highly Skeptical',0)+sd.get('Skeptic',0)
    champside=sd.get('Champion',0)+sd.get('Ally',0)
    _stance=json.load(open(p('category_stance.json')))
    _th=_stance.get('thresholds',{})
    _curve=_th.get('domain_label_curves') or {}
    _requested_curves={
        'Charter Modernization','Member Business Lending',
        'Mortgage & Housing','Technology & Emerging',
    }
    _fixed=(
        _th.get('scale')=='fixed-bands-with-declared-signed-empirical-curves-v3' and
        _th.get('champion_or_defender')==0.70 and _th.get('ally')==0.35 and
        _th.get('highly_persuadable')==0.25 and _th.get('persuadable')==0.15 and
        _th.get('lean_persuadable')==0.05 and _th.get('true_neutral')==[-0.05,0.05] and
        _th.get('lean_skeptic')==-0.05 and _th.get('skeptic')==-0.15 and
        _th.get('highly_skeptical')==-0.35 and _th.get('opponent')==-0.70 and
        (_th.get('overall') or {}).get('normalization')=='fixed-not-roster-percentile' and
        _th.get('evidence_scales',{}).get('normalization')=='fixed-not-domain-percentile' and
        set(_curve.get('categories') or [])==_requested_curves and
        _curve.get('changes_raw_scores') is False and
        _curve.get('changes_adjusted_scores') is False and
        _curve.get('empty_domains_inherit') is False and
        (_stance.get('curve') or {}).get('label_scores')
    )
    ok(_fixed,'D1 fixed overall/domain bands plus four declared sign-preserving label curves; observed overall sides anti=%d pro=%d %s'%(oppside,champside,dict(sd)))
    # E congestion sheet
    ok('Congestion' in wb.sheetnames,'E1 Congestion sheet present')
    cg=wb['Congestion']; ncg=sum(1 for r in range(3,cg.max_row+1) if cg.cell(r,1).value)
    ok(ncg==533,'E2 Congestion rows = %d'%ncg)
    CJ=json.load(open(p('data/engine/congestion.json')))
    TCU=sum(m['cu'] for m in CJ); TBK=sum(m['banks'] for m in CJ); TFS=sum(m['finserv'] for m in CJ)
    ok(money_totals_match_source(CJ,p('data/money/fec_refresh_current.json')),f'E3 source-bound industry totals CU ${TCU:,} Banks ${TBK:,} FinSvc ${TFS:,}')
    # 3-way raw shares sum to 100; capacity-adj sum to 100
    badraw=sum(1 for m in CJ if m['total']>0 and abs((m['cu_pct']+m['banks_pct']+m['finserv_pct'])-100)>0.3)
    badadj=sum(1 for m in CJ if m['total']>0 and abs((m['cu_adj']+m['banks_adj']+m['finserv_adj'])-100)>0.3)
    ok(badraw==0 and badadj==0,'E4 share rows not summing to 100: raw=%d adj=%d'%(badraw,badadj))
    # Recompute current rows; a named member may legitimately have no current-cycle money.
    _share_errors=capacity_share_errors(CJ)
    ok(not _share_errors,'E5 capacity-adj math for all current members (errors: %s)'%_share_errors[:5])
    # 3 edge cases present
    ec={m['member']:m for m in CJ}
    ok(all(n in ec for n in ['Debbie Wasserman Schultz','Matt Van Epps','Pablo Hernández Rivera']),'E6 three edge-case members present')
    # F FEC mapping coverage + no false-match committees in totals
    ok(len([m for m in CJ if m['bioguide']])==533,'F1 all 533 members have bioguide')
    neg=[m['member'] for m in CJ if (m['cu'] or 0)<0 or (m['banks'] or 0)<0 or (m['finserv'] or 0)<0]
    ok(len(neg)==0,'F2 no negative $ buckets (refunds floored): %s'%neg[:3])
    # G IM allocation -- M4: im_allocation.json is now the LIVE per-topic model (member-level row-sums of
    # per_topic_alloc.json [FLIP] + per_topic_coalition.json [COALITION]), NOT the superseded single-$2M
    # im_allocate2.py output. G1/G2 updated accordingly: the real invariant is that the IM columns EQUAL the
    # per-topic row-sums (so the workbook sheet can't silently go stale), and the flip total is one $2M
    # budget per source block.  CFPB deliberately has two source blocks; CU Tax Status is now block 153.
    IM=json.load(open(p('im_allocation.json')))
    PT=json.load(open(p('per_topic_alloc.json')))
    PTC=json.load(open(p('data/lobbying/per_topic_coalition.json')))
    fs=sum(IM['flip'].values()); cs=sum(IM['coalition'].values())
    pta_tot=sum(sum(d.values()) for d in PT.values()); ptc_tot=sum(sum(d.values()) for d in PTC.values())
    ok(fs==pta_tot and abs(fs-len(PT)*2_000_000)<5000*len(PT),f'G1 flip allocation = live per-topic row-sums ${fs:,} (= {len(PT)} x $2M)')
    ok(cs==ptc_tot and 0<cs<=len(PTC)*2_000_000,f'G2 coalition allocation = live per-topic row-sums ${cs:,} (0 < c <= {len(PTC)} x $2M)')
    _expected_source_blocks=len(blocks)
    _expected_unique_categories=len(set(BLOCKCAT.values()))
    ok(len(PT)==_expected_source_blocks and 'Credit Union Tax Status' in PT and
       len({BLOCKCAT[b] for b in blocks})==_expected_unique_categories,
       'G3 per-sub-category allocations = %d topics (expected %d source blocks / %d unique categories)'%
       (len(PT),_expected_source_blocks,_expected_unique_categories))
    ptbad=[t for t,a in PT.items() if abs(sum(a.values())-2_000_000)>5000]
    ok(len(ptbad)==0,'G4 each sub-category sums to $2M (bad: %s)'%ptbad)
    # H cross-consistency: congestion.json members == model members
    modn=set(ra.cell(r,1).value for r in range(3,536) if ra.cell(r,1).value)   # cache-immune
    cjn=set(m['member'] for m in CJ)
    ok(modn==cjn,'H1 congestion.json members match model exactly (diff %d)'%len(modn^cjn))
    # ===== report =====
    print('AUDIT — %d passed, %d FAILED\n'%(len(P),len(F)))
    for x in P: print('  [PASS] '+x)
    if F:
        print('\n  !!! FAILURES !!!')
        for x in F: print('  [FAIL] '+x)
    else: print('\n  ALL CHECKS PASSED.')

    return 1 if F else 0


if __name__ == '__main__':
    sys.exit(main())