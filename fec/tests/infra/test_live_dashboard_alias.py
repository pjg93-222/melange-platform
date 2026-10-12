from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from surfaces import sync_live_dashboard_alias as alias


def test_alias_preserves_prior_generation_and_is_idempotent(tmp_path):
    (tmp_path/'data/engine').mkdir(parents=True)
    source=tmp_path/'Moneyball.html';target=tmp_path/'Moneyball_Redesign.html'
    source.write_text('<html>new</html>');target.write_text('<html>prior</html>')
    assert alias.sync(tmp_path)['status']=='PASS'
    assert target.read_bytes()==source.read_bytes()
    prior=tmp_path/'backups/Moneyball_Redesign.previous-generation.html'
    assert prior.read_text()=='<html>prior</html>'
    alias.sync(tmp_path)
    assert prior.read_text()=='<html>prior</html>'


def test_concurrent_alias_edit_is_retained(tmp_path,monkeypatch):
    (tmp_path/'data/engine').mkdir(parents=True)
    (tmp_path/'Moneyball.html').write_text('new')
    target=tmp_path/'Moneyball_Redesign.html';target.write_text('prior')
    original=alias.shutil.copyfile
    def copy(source,destination,*args,**kwargs):
        result=original(source,destination,*args,**kwargs)
        if str(destination).endswith('.candidate'):target.write_text('concurrent edit')
        return result
    monkeypatch.setattr(alias.shutil,'copyfile',copy)
    with pytest.raises(ValueError,match='changed'):
        alias.sync(tmp_path)
    assert target.read_text()=='concurrent edit'


def clean_generation(root):
    (root/'data/engine').mkdir(parents=True)
    (root/'Moneyball.html').write_text('<html>generated</html>')
    for name in alias.CLEAN_INPUTS:
        (root/name).parent.mkdir(parents=True,exist_ok=True);(root/name).write_text('input '+name)
    builder=root/alias.CLEAN_BUILDER;builder.parent.mkdir(parents=True)
    builder.write_text('\n'.join(['from pathlib import Path','ROOT=Path(__file__).resolve().parents[2]','def main():',
        "    count=ROOT/'builds.txt';count.write_text(str(int(count.read_text())+1) if count.exists() else '1')",
        "    (ROOT/'Moneyball_Redesign.html').write_bytes(b'<!doctype html>\\n<!-- melange-clean-workspace-v2 -->\\nrebuilt')",'']))
    target=root/'Moneyball_Redesign.html';target.write_bytes(b'<!doctype html>\n<!-- melange-clean-workspace-v2 -->\nold')
    return target


def test_clean_workspace_shell_is_rebuilt_not_overwritten(tmp_path):
    target=clean_generation(tmp_path)
    receipt=alias.sync(tmp_path)
    assert receipt['status']=='PASS' and receipt['mode']==alias.CLEAN_MODE
    assert target.read_bytes().endswith(b'rebuilt') and (tmp_path/'builds.txt').read_text()=='1'
    assert (tmp_path/'Moneyball.html').read_text()=='<html>generated</html>'
    assert not (tmp_path/'backups').exists()
    assert receipt['sha256']==alias.digest(target) and receipt['source_sha256']==alias.digest(tmp_path/'Moneyball.html')
    assert alias.clean_workspace_current(tmp_path)
    (tmp_path/'lobby-bills.html').write_text('a newer generation')
    assert not alias.clean_workspace_current(tmp_path)
    alias.sync(tmp_path)
    assert (tmp_path/'builds.txt').read_text()=='2' and alias.clean_workspace_current(tmp_path)


def test_clean_workspace_without_its_builder_is_refused(tmp_path):
    target=clean_generation(tmp_path);(tmp_path/alias.CLEAN_BUILDER).unlink()
    with pytest.raises(ValueError,match='no builder'):
        alias.sync(tmp_path)
    assert target.read_bytes().endswith(b'old')
