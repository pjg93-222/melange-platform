"""Rebuild the local presentation from source without rerunning any analytical models.

Uses the existing generated preview (or Moneyball.html on first build), replaces
only explicitly delimited presentation adapters, and bundles the reviewed source
CSS/JS into the server's existing assets. Published data/artifacts stay untouched.
"""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SRC = HERE / 'approved'


def bundle(target, source):
    marker = '/* MELANGE APPROVED PRESENTATION */'
    current = target.read_text(encoding='utf-8').split(marker)[0].rstrip()
    target.write_text(current + '\n\n' + marker + '\n' + source.read_text(encoding='utf-8').rstrip() + '\n', encoding='utf-8')


def install_bridge(html, kind, anchor):
    start, end = f'/* APPROVED {kind} BRIDGE */', f'/* END APPROVED {kind} BRIDGE */'
    html = re.sub(re.escape(start) + r'.*?' + re.escape(end) + r'\s*', '', html, flags=re.S)
    if html.count(anchor) != 1:
        raise ValueError(f'Expected one {kind} hook, found {html.count(anchor)}')
    source = (SRC / f'{kind}-bridge.js').read_text(encoding='utf-8')
    return html.replace(anchor, f'{start}\n{source}\n{end}\n{anchor}', 1)


def main():
    target = ROOT / 'Moneyball_Redesign.html'
    original = target if target.exists() else ROOT / 'Moneyball.html'
    html = original.read_text(encoding='utf-8')
    html = install_bridge(html, 'desk', 'window.__deskOpen=function(n)')
    html = install_bridge(html, 'network', 'window.__netResize=resize;')
    # Keep the original organic positions; frame their useful bounds, with real padding.
    template = (HERE / 'templates/make_dashboard_html_00.tpl').read_text(encoding='utf-8')
    new = re.search(r'function fit\(\)\{[^\n]*\}', template).group(0)
    html = re.sub(r'function fit\(\)\{[^\n]*\}', lambda _: new, html, count=1)
    halo = "ctx.globalAlpha=0.16;ctx.fillStyle='#EECD5C';ctx.beginPath();ctx.arc(sx,sy,r+11,0,7);ctx.fill();ctx.globalAlpha=1;"
    html = html.replace(halo, '/* Selection uses an outline, without a decorative halo. */')
    target.write_text(html, encoding='utf-8')
    bundle(HERE / 'staffer_live/melange-dashboard.js', SRC / 'workspace.js')
    # A compact, read-only action index for the separate Champions page. Counts
    # and opened ledgers are computed from the same retained canonical actions.
    data = json.loads((ROOT / 'member_desk_bundle.json').read_text(encoding='utf-8'))
    index = {'asOf': data.get('rank_honors', {}).get('as_of'), 'members': [], 'bills': {}}
    for m in data['members']:
        fields = m.get('honors', {}).get('fields', {})
        index['members'].append({'name': m['name'], 'chamber': m['chamber'], 'district': m.get('district'),
            'fields': {k: v.get('field_actions', []) for k, v in fields.items()},
            'rank': m.get('honors', {}).get('overall', {}).get('rank')})
    for key, bill in data.get('bill_live', {}).get('bills', {}).items():
        index['bills'][key] = {k: bill.get(k) for k in ('title','url','introduced_date','latest_action','summary')}
    index_json = json.dumps(index, ensure_ascii=True, separators=(',', ':')).replace('<', '\\u003c')
    js_target = HERE / 'staffer_live/melange-dashboard.js'
    with js_target.open('a', encoding='utf-8') as fh:
        fh.write('\nwindow.MelangeEvidenceIndex=' + index_json + ';\n')
        fh.write((SRC / 'research.js').read_text(encoding='utf-8'))
        fh.write((SRC / 'workspace-flows.js').read_text(encoding='utf-8'))
    bundle(HERE / 'staffer_live/melange-dashboard.css', SRC / 'workspace.css')
    out = ROOT / 'outputs/approved-implementation-2026-09-21'
    out.mkdir(exist_ok=True)
    receipt = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [target, *SRC.glob('*')] if p.is_file()}
    (out / 'build-receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print('Built local preview and shared presentation assets; analytical records unchanged.')


if __name__ == '__main__':
    main()
