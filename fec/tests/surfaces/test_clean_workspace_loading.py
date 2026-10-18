"""Workspace loading (r5): every frame document starts with mlk-boot.js and names a mount marker its overlay really sets;
every workspace script the build injects may be re-requested in place (data-mlk); the shell loads its scripts in order
behind the loader; a tree without the review folder still builds (audit r4 code F3); a presentation rebuild keeps a
current alias receipt current and never binds a generation the publication step did not (code F4)."""
from pathlib import Path
import importlib.util
import re
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SURFACES = ROOT / 'fec/surfaces'
CLEAN = SURFACES / 'approved/clean'
OUT = SURFACES / 'staffer_live/approved'
sys.path.insert(0, str(ROOT / 'fec'))


def builder():
    spec = importlib.util.spec_from_file_location('build_clean_workspace_under_test', ROOT / 'fec' / 'surfaces' / 'build_clean_workspace.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FRAMES = {'original-desk.html': 'desk', 'original-industry.html': 'industry', 'original-champions.html': 'champions',
          'original-interactions.html': 'interactions', 'original-research-bills.html': 'research-bills',
          'original-cu-fintech.html': 'cu-fintech', 'people.html': 'people', 'network.html': 'network'}
OVERLAY_SOURCE = {'desk': 'desk-shell.js', 'industry': 'industry-ui.js', 'champions': 'champions-ui.js',
                  'interactions': 'interactions-ui.js', 'people': 'people-ui.js', 'network': 'network.js'}


def test_every_frame_has_a_mount_marker_its_overlay_sets():
    B = builder()
    assert set(B.MOUNTED) == set(FRAMES.values())
    assert (CLEAN / B.BOOT).is_file()
    for frame, marker in B.MOUNTED.items():
        if not marker:
            assert not B.OVERLAYS.get(frame), f'{frame} has overlay scripts but no mount marker'
            continue
        element, cls = marker.split('.')
        source = (CLEAN / OVERLAY_SOURCE[frame]).read_text(encoding='utf-8')
        # the overlay adds exactly this class, on this element, while it mounts
        assert re.search(r"classList\.add\([^)]*'" + re.escape(cls) + "'", source), (frame, marker)
        holder = {'html': r'documentElement|\broot\b', 'body': r'\bbody\b', 'nav': r'\bnav\b'}[element]
        line = next(l for l in source.splitlines() if re.search(r"classList\.add\([^)]*'" + re.escape(cls) + "'", l))
        assert re.search(holder, line), (frame, line)


def test_boot_tags_are_first_in_head_and_carry_the_marker():
    B = builder()
    html = '<!doctype html><html><head><meta charset=utf-8><style>x{}</style></head><body><script>var a="<head>"</script></body></html>'
    out = B.with_boot(html, 'champions')
    assert out.index('mlk-boot.js') < out.index('<meta') and out.count('mlk-boot.js') == 1
    assert '<script src="/staffer-live/approved/mlk-boot.js" data-frame="champions" data-ready="html.chm-ready"></script>' in out
    assert out.endswith('<script>var a="<head>"</script></body></html>')       # only the first <head>
    based = B.with_boot('<head><base href="/"><title>t</title></head>', 'research-bills', head='<head><base href="/">')
    assert based.startswith('<head><base href="/"><script src="/staffer-live/approved/mlk-boot.js" data-frame="research-bills" data-ready="">')
    with pytest.raises(ValueError):
        B.with_boot('<html><body></body></html>', 'desk')
    assert B.js_scripts('ui-kit', 'desk-shell') == ('<script src="/staffer-live/approved/ui-kit.js" data-mlk></script>'
                                                   '<script src="/staffer-live/approved/desk-shell.js" data-mlk></script>')


@pytest.mark.skipif(not (OUT / 'original-desk.html').is_file(), reason='the workspace has not been built in this tree')
def test_built_frames_load_every_workspace_script_behind_the_loader():
    B = builder()
    for name, frame in FRAMES.items():
        html = (OUT / name).read_text(encoding='utf-8')
        head = html.index('<head>')
        first = re.compile(r'<(script|link)\b[^>]*>').search(html, head)
        assert first and 'mlk-boot.js' in first.group(0), (name, first and first.group(0))
        assert f'data-frame="{frame}"' in first.group(0) and f'data-ready="{B.MOUNTED[frame]}"' in first.group(0), name
        assert html.count('mlk-boot.js') == 1, name
        tags = re.findall(r'<script\b[^>]*\bsrc="/staffer-live/approved/[^"]+"[^>]*>', html)
        assert tags, name
        for tag in tags:
            assert 'mlk-boot.js' in tag or ' data-mlk' in tag, (name, tag)
            assert ' defer' not in tag and ' async' not in tag, (name, tag)
    assert (OUT / B.BOOT).read_bytes() == (CLEAN / B.BOOT).read_bytes()


@pytest.mark.skipif(not (OUT / 'original-desk.html').is_file(), reason='the workspace has not been built in this tree')
def test_every_built_document_with_workspace_styles_gets_its_fonts_from_the_loader():
    # fix2 (r5 audit D-1): the overlay stylesheets no longer @import the font kit; mlk-boot.js links it. So every document
    # that links a workspace stylesheet must carry the loader, or it would silently lose the kit.
    docs = [p for p in OUT.glob('*.html')] + [ROOT / 'Moneyball_Redesign.html']
    styled = 0
    for doc in docs:
        if not doc.is_file():
            continue
        html = doc.read_text(encoding='utf-8')
        if re.search(r'<link\s[^>]*href="(?:/staffer-live/)?approved/[^"]+\.css"', html):
            styled += 1
            assert html.count('/staffer-live/approved/mlk-boot.js') == 1, doc.name
    assert styled >= 9
    for sheet in CLEAN.glob('*.css'):
        assert '@import' not in sheet.read_text(encoding='utf-8'), sheet.name


def test_the_shell_loads_its_scripts_in_order_behind_the_loader():
    raw = (CLEAN / 'index.html').read_bytes()
    assert b'<!-- melange-clean-workspace-v2 -->' in raw[:128]                 # the server's v2 routing marker
    html = raw.decode('utf-8')
    head, body = html.split('</head>', 1)
    assert re.search(r'<head><meta charset="utf-8"><script src="/staffer-live/approved/mlk-boot.js" data-frame="shell"></script>', head)
    assert 'defer' not in html and html.count('<script') == 5
    order = re.findall(r'<script src="/staffer-live/approved/([a-z-]+)\.js" data-mlk></script>', body)
    assert order == ['ui-kit', 'app', 'research', 'workspace']
    assert body.rstrip().endswith('<script src="/staffer-live/approved/workspace.js" data-mlk></script></body></html>')
    assert body.index('id="announcer"') < body.index('ui-kit.js')                # every shell node exists before app.js runs


def test_the_rejected_alias_folder_is_created_once(tmp_path):
    B = builder()
    target, backup = tmp_path / 'Moneyball_Redesign.html', tmp_path / 'outputs/rebuild-review-2026-09-21/rejected-Moneyball_Redesign.html'
    B.keep_rejected_alias(target, backup)
    assert not backup.parent.exists()                                          # nothing to keep yet
    target.write_text('first alias')
    B.keep_rejected_alias(target, backup)
    assert backup.read_text() == 'first alias'
    target.write_text('second alias')
    B.keep_rejected_alias(target, backup)
    assert backup.read_text() == 'first alias'


def generation(root):
    from surfaces import sync_live_dashboard_alias as alias
    (root / 'data/engine').mkdir(parents=True)
    (root / 'Moneyball.html').write_text('<html>generated</html>')
    for name in alias.CLEAN_INPUTS:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text('input ' + name)
    built = root / alias.CLEAN_BUILDER
    built.parent.mkdir(parents=True)
    built.write_text('\n'.join(['from pathlib import Path', 'ROOT=Path(__file__).resolve().parents[2]', 'def main():',
        "    (ROOT/'Moneyball_Redesign.html').write_bytes(b'<!doctype html>\\n<!-- melange-clean-workspace-v2 -->\\nshell one')", '']))
    (root / 'Moneyball_Redesign.html').write_bytes(b'<!doctype html>\n<!-- melange-clean-workspace-v2 -->\nold')
    return alias


def test_a_presentation_rebuild_rebinds_a_current_receipt(tmp_path):
    alias = generation(tmp_path)
    published = alias.sync(tmp_path)                                          # the guarded publication step
    assert alias.clean_workspace_current(tmp_path)
    before = alias.current_receipt(tmp_path)
    assert before == published
    shell = tmp_path / 'Moneyball_Redesign.html'
    shell.write_bytes(b'<!doctype html>\n<!-- melange-clean-workspace-v2 -->\nshell two')   # a UI rebuild
    assert not alias.clean_workspace_current(tmp_path)
    receipt = alias.rebind_after_rebuild(tmp_path, before)
    assert alias.clean_workspace_current(tmp_path)
    assert receipt['sha256'] == alias.digest(shell) and receipt['previous_alias_sha256'] == published['sha256']
    for key in ('mode', 'status', 'source_sha256', 'workspace_inputs_sha256', 'prepublication_source_guard', 'schema'):
        assert receipt[key] == published[key]
    assert 'presentation rebuild' in receipt['rebound_by']


def test_a_rebuild_never_binds_what_publication_did_not(tmp_path):
    alias = generation(tmp_path)
    receipt_path = tmp_path / alias.RECEIPT
    assert alias.current_receipt(tmp_path) is None                            # no receipt at all
    assert alias.rebind_after_rebuild(tmp_path, None) == {'status': 'SKIP_NOT_CURRENT'}
    assert not receipt_path.exists()
    alias.sync(tmp_path)
    before = alias.current_receipt(tmp_path)
    (tmp_path / 'lobby-bills.html').write_text('a newer generation')           # the generation moved under the rebuild
    old = receipt_path.read_bytes()
    assert alias.rebind_after_rebuild(tmp_path, before) == {'status': 'SKIP_GENERATION_CHANGED'}
    assert receipt_path.read_bytes() == old and not alias.clean_workspace_current(tmp_path)
    receipt_path.write_text('{"schema": "live-dashboard-alias-v1", "status": "PASS", "sha256": "84f0"}')   # pre-v2 format
    assert alias.current_receipt(tmp_path) is None


def test_the_builder_rebinds_only_around_its_own_run(tmp_path, monkeypatch):
    B = builder()
    alias = generation(tmp_path)
    monkeypatch.setattr(B, 'ROOT', tmp_path)
    rebind = B.alias_receipt_rebinder()                                        # no current receipt yet
    rebind()
    assert not (tmp_path / alias.RECEIPT).exists()
    alias.sync(tmp_path)
    rebind = B.alias_receipt_rebinder()                                        # captured before the build
    (tmp_path / 'Moneyball_Redesign.html').write_bytes(b'<!doctype html>\n<!-- melange-clean-workspace-v2 -->\nrebuilt')
    rebind()
    assert alias.clean_workspace_current(tmp_path)
