import json,os,sqlite3,subprocess,sys
from pathlib import Path
import pytest
SCRIPT=Path(__file__).resolve().parents[2]/'members/member_reference_layer.py'

@pytest.mark.parametrize('text,expected',[
    ('I am proud to partner with Senator Sample on this legislation.',1),
    ('I oppose Senator Sample because this amendment would harm our work.',1),
    ("Senator Sample's amendment is reckless and I oppose it.",1),
    ('I am proud to partner with Senator Bob Unlisted on this legislation.',0),
])
def test_titled_surname_before_connector(tmp_path,text,expected):
    (tmp_path/'data/engine').mkdir(parents=True)
    (tmp_path/'data/engine/congestion.json').write_text(json.dumps([
        {'member':'Alice Example','bioguide':'TEST01','chamber':'House'},
        {'member':'Bob Sample','bioguide':'TEST02','chamber':'Senate'},
        {'member':'Dan Sample','bioguide':'TEST03','chamber':'House'}]),encoding='utf-8')
    with sqlite3.connect(tmp_path/'social_media.db') as db:
        db.execute('CREATE TABLE speeches(member,date,source,text)')
        db.execute('CREATE TABLE members(bioguide,twitter)')
        db.execute('INSERT INTO speeches VALUES (?,?,?,?)',('Alice Example','2026-09-08','TEST',text))
    result=subprocess.run([sys.executable,str(SCRIPT),'--probe'],env={**os.environ,'MONEYBALL_ROOT':str(tmp_path),
        'MONEYBALL_DRAFT':str(tmp_path/'missing-draft')},capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0,result.stderr
    assert f'substantive-candidate references: {expected} ' in result.stdout,result.stdout
