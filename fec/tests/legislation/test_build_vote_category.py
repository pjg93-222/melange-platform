from pathlib import Path
import sys
import csv
import json
import pytest
import pandas as pd
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legislation.build_vote_category import build, measure, passage, retained_rules, sha


@pytest.mark.parametrize('display,expected', [('H.R.32', 'hr'), ('HR32', 'hr'),
    ('H.J.Res.32', 'hjres'), ('HJRES32', 'hjres'), ('S.32', 's'), ('S.J.Res.32', 'sjres')])
def test_measure_type_is_conserved(display, expected):
    assert measure(display, 119) == f'119:{expected}:32'
    assert measure(display, 118) == f'118:{expected}:32'


def test_rejects_non_measure_or_trailing_text():
    for value in ('HR0', 'HR32 and HR33', 'H.Amdt.32', '', 'nomination 32'):
        assert measure(value, 119) is None


def test_retains_final_passage_only_policy():
    assert passage('On Passage of the Bill')
    assert passage('On Motion to Suspend the Rules and Pass, as Amended')
    assert not passage('On Motion to Recommit')
    assert not passage('On the Amendment')
    assert not passage('On Cloture')


def test_keeps_existing_category_and_sign_constants():
    rules = retained_rules(Path(__file__).resolve().parents[2] / 'legislation/vote_by_category.py')
    assert len(rules['CATLAB']) == 17
    assert rules['ANTI'] == {4, 14}
    assert rules['CATLAB'][13] == 'CFPB'


def test_complete_rebuild_conserves_types_exclusions_and_nonvotes(tmp_path):
    root, source, output = tmp_path / 'root', tmp_path / 'source', tmp_path / 'out'
    (root / 'fec/legislation').mkdir(parents=True)
    (root / 'data/legislation').mkdir(parents=True)
    source.mkdir()
    original = Path(__file__).resolve().parents[2] / 'legislation/vote_by_category.py'
    (root / 'fec/legislation/vote_by_category.py').write_bytes(original.read_bytes())
    (root / 'data/legislation/cfpb_bill_directions.csv').write_text('Bill,Direction,Category\n', encoding='utf-8')
    (root / 'legislation_exclusion_ledger.ndjson').write_text('', encoding='utf-8')
    (root / 'legislation_hold_ledger.ndjson').write_text(''.join(json.dumps({
        'canonical_key': f'{c}:s:9', 'decision_scope': 'candidate'}) + '\n' for c in (117,118,119)), encoding='utf-8')
    # Rollcall dates must sit inside the builder's 30-day freshness window of the archive's mtime
    # (the archive is written below, now), so they follow the clock, not a fixed date that ages out.
    fresh = datetime.now(timezone.utc).date().isoformat()
    exclusions = tmp_path / 'excluded.csv'
    exclusions.write_text('congress,bill\n', encoding='utf-8')
    names = tmp_path / 'names.json'
    names.write_text('{"A000001":"Member A","B000002":"Member B"}', encoding='utf-8')
    archive = root / 'congress_bills_archive_test.xlsx'
    pd.DataFrame([{'Congress': c, 'Name': bill, 'Section': category}
        for c in (117,118,119) for bill, category in (
            ('H.R.7','Examination & Supervision'), ('H.R.7','Payments'),
            ('H.J.Res.7','Interchange'), ('S.9','Payments'))]).to_excel(archive, sheet_name='All Bills', index=False)
    def write(name, fields, records):
        with (source / name).open('w', encoding='utf-8', newline='') as handle:
            writer=csv.writer(handle); writer.writerow(fields.split(',')); writer.writerows(records)
    for c in (117,118,119):
        write(f'HS{c}_members.csv','congress,chamber,icpsr,bioguide_id,bioname',[
            [c,'House','1','A000001','A'],[c,'House','2','B000002','B'],[c,'President','999','', 'President']])
        write(f'HS{c}_rollcalls.csv','congress,chamber,rollnumber,bill_number,vote_question,date,session,clerk_rollnumber',[
            [c,'House','1','HR7','On Passage',fresh,'2','1'],
            [c,'House','2','HJRES7','On Passage',fresh,'2','2'],
            [c,'Senate','3','S9','On Passage',fresh,'2','3']])
        write(f'HS{c}_votes.csv','congress,chamber,rollnumber,icpsr,cast_code',[
            [c,'House','1','1',1],[c,'House','1','2',9],[c,'House','1','999',1],
            [c,'House','2','1',6],[c,'Senate','3','1',1]])
    (source/'receipt.json').write_text(json.dumps({'status':'PASS','files':[
        {'path':str(path),'sha256':sha(path)} for path in source.glob('*.csv')]}),encoding='utf-8')
    receipt = build(root, source, archive, exclusions, names, output)
    result=json.loads((output/'vote_category_lean.json').read_text(encoding='utf-8'))
    assert result['votes']=={'A000001': {name:{'lean':1.0,'n':3}
        for name in ('Examination & Supervision','Payments','Interchange')}}
    assert receipt['counts']['accepted_rollcalls']==6
    assert receipt['counts']['member_votes']==9
    assert receipt['rejections']['non_legislator_positions']==3
    assert receipt['rejections']['non_yea_nay_member_votes']==3
    with (source/'HS119_votes.csv').open('a',encoding='utf-8') as handle:
        handle.write('119,House,1,1,6\n')
    with pytest.raises(ValueError,match='Source snapshot bytes changed'):
        build(root,source,archive,exclusions,names,output)
