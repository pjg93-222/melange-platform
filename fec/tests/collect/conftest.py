"""Synthetic CREC roster shared by the collect tests: a roster, HSall and press-CSV that mirror the audit 2026-10-20 cases.
The live roster, keys and database are never read."""
import csv
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

FEC = Path(__file__).resolve().parents[2]

# (member, bioguide, chamber, HSall bioname, state, [(congress, chamber)], press-CSV state or None)
ROSTER = [
    ('Christopher Smith', 'S000522', 'House', 'SMITH, Christopher H.', 'NJ', [(117, 'House'), (118, 'House'), (119, 'House')], None),
    ('Adam Smith', 'S000510', 'House', 'SMITH, Adam', 'WA', [(118, 'House'), (119, 'House')], 'WA'),
    ('Jason Smith', 'S001195', 'House', 'SMITH, Jason', 'MO', [(118, 'House'), (119, 'House')], 'MO'),
    ('Tina Smith', 'S001203', 'Senate', 'SMITH, Tina', 'MN', [(118, 'Senate'), (119, 'Senate')], 'MN'),
    ('Austin Scott', 'S001189', 'House', 'SCOTT, Austin', 'GA', [(118, 'House'), (119, 'House')], None),
    ('Robert Scott', 'S000185', 'House', 'SCOTT, Robert C. (Bobby)', 'VA', [(118, 'House'), (119, 'House')], 'VA'),
    ('Ben Luján', 'L000570', 'Senate', 'LUJAN, Ben Ray', 'NM', [(116, 'House'), (117, 'House'), (118, 'Senate'), (119, 'Senate')], 'NM'),
    ('Jesús García', 'G000586', 'House', 'GARCIA, Jesus G. "Chuy"', 'IL', [(118, 'House'), (119, 'House')], None),
    ('Robert Garcia', 'G000598', 'House', 'GARCIA, Robert', 'CA', [(118, 'House'), (119, 'House')], None),
    ('Sylvia Garcia', 'G000587', 'House', 'GARCIA, Sylvia R.', 'TX', [(118, 'House'), (119, 'House')], 'TX'),
    ('Nydia Velázquez', 'V000081', 'House', 'VELAZQUEZ, Nydia M.', 'NY', [(118, 'House'), (119, 'House')], None),
    ('Timothy Kennedy', 'K000402', 'House', 'KENNEDY, Timothy M.', 'NY', [(118, 'House'), (119, 'House')], None),
    ('John Kennedy', 'K000393', 'Senate', 'KENNEDY, John Neely', 'LA', [(118, 'Senate'), (119, 'Senate')], 'LA'),
    ('Mike Kennedy', 'K000377', 'House', 'KENNEDY, Mike', 'UT', [(119, 'House')], 'UT'),
    ('Daniel Goldman', 'G000599', 'House', 'GOLDMAN, Daniel S.', 'NY', [(118, 'House'), (119, 'House')], None),
    ('Clay Higgins', 'H001077', 'House', 'HIGGINS, Clay', 'LA', [(118, 'House'), (119, 'House')], 'LA'),
    ('Derrick Van Orden', 'V000135', 'House', 'VAN ORDEN, Derrick', 'WI', [(118, 'House'), (119, 'House')], 'WI'),
    ('Adelita Grijalva', 'G000606', 'House', 'GRIJALVA, Adelita S.', 'AZ', [(119, 'House')], None),
    ('Pablo Hernández Rivera', 'H001103', 'House', 'HERNÁNDEZ, Pablo José', 'PR', [(119, 'House')], None),
    ('Danny Davis', 'D000096', 'House', 'DAVIS, Danny K.', 'IL', [(118, 'House'), (119, 'House')], None),
    ('Donald Davis', 'D000230', 'House', 'DAVIS, Donald G.', 'NC', [(118, 'House'), (119, 'House')], None),
    ('Brendan Boyle', 'B001296', 'House', 'BOYLE, Brendan F.', 'PA', [(118, 'House'), (119, 'House')], None),
    ('John Rose', 'R000612', 'House', 'ROSE, John W.', 'TN', [(118, 'House'), (119, 'House')], None),
    ('Al Green', 'G000553', 'House', 'GREEN, Al', 'TX', [(118, 'House'), (119, 'House')], None),
    ('Susie Lee', 'L000590', 'House', 'LEE, Susie', 'NV', [(118, 'House'), (119, 'House')], None),
    ('Mazie Hirono', 'H001042', 'Senate', 'HIRONO, Mazie K.', 'HI', [(118, 'Senate'), (119, 'Senate')], None),
]
# People HSall knows that are NOT on the roster (historical or departed namesakes): (bioguide, bioname, state, congress, chamber, died).
OFF_ROSTER = [
    ('G000600', 'GOLDMAN, Craig A.', 'TX', 119, 'House', ''),
    ('S001190', 'SCOTT, David', 'GA', 118, 'House', ''), ('S001190', 'SCOTT, David', 'GA', 119, 'House', ''),
    ('D000598', 'DAVIS, Susan A.', 'CA', 118, 'House', ''),
    ('R000601', 'ROE, David P.', 'TN', 116, 'House', ''),
    ('H001078', 'HIGGINS, Brian', 'NY', 118, 'House', ''),
    ('G000590', 'GREEN, Mark E.', 'TN', 118, 'House', ''),
    ('L000551', 'LEE, Barbara', 'CA', 118, 'House', ''),
    ('J000032', 'JACKSON LEE, Sheila', 'TX', 118, 'House', ''),
    ('G000551', 'GRIJALVA, Raúl M.', 'AZ', 119, 'House', '2025.0'),   # died March 2025; Adelita (his daughter) holds the seat from Nov 2025
]


@pytest.fixture(scope='module')
def cr(tmp_path_factory):
    """fetch_congressional_record loaded against the synthetic roster."""
    root = tmp_path_factory.mktemp('crec_roster')
    (root / 'data' / 'engine').mkdir(parents=True)
    (root / 'votes').mkdir()
    (root / 'draft').mkdir()
    (root / 'data' / 'engine' / 'congestion.json').write_text(
        json.dumps([{'member': m, 'bioguide': bio, 'chamber': ch} for m, bio, ch, *_ in ROSTER], ensure_ascii=False), encoding='utf-8')   # raw UTF-8, as the roster refresh now writes it: a reader without encoding= mojibakes 'Velázquez' on this box
    with open(root / 'votes' / 'HSall_members.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['congress', 'chamber', 'bioguide_id', 'icpsr', 'bioname', 'state_abbrev', 'died'])
        for _m, bio, _ch, bioname, st, service, _csv in ROSTER:
            for congress, chamber in service: w.writerow([congress, chamber, bio, '', bioname, st, ''])
        for bio, bioname, st, congress, chamber, died in OFF_ROSTER: w.writerow([congress, chamber, bio, '', bioname, st, died])
    with open(root / 'draft' / 'cu_press_stance_tone.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(['member', 'state'])
        for m, _bio, _ch, _bn, _st, _svc, csv_state in ROSTER:
            if csv_state: w.writerow([m, csv_state])
    # paths.py reads these at import; point it (and only it) at the synthetic tree, then restore.
    saved = {k: os.environ.get(k) for k in ('MONEYBALL_ROOT', 'MONEYBALL_DRAFT')}
    os.environ['MONEYBALL_ROOT'] = str(root); os.environ['MONEYBALL_DRAFT'] = str(root / 'draft')
    prior_paths = sys.modules.pop('paths', None)
    try:
        spec = importlib.util.spec_from_file_location('crec_attrib_under_test', FEC / 'collect' / 'fetch_congressional_record.py')
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    finally:
        sys.modules.pop('paths', None)
        if prior_paths is not None: sys.modules['paths'] = prior_paths
        for k, v in saved.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    return mod




TOOL_FUNCS = ['contains', 'covers', 'surname', 'reconcile', 'Collect', 'hist_names', 'hist_persons', 'refusal_reason', 'label_index',
              'decide', 'suspect_rows', 'quarantine_table', 'apply_decisions', 'undo_batch', '_norm', '_chambers',
              'quarantine_cmd', 'undo_cmd', 'work_list', 'cached',
              # refine pass (read-only)
              'STATE_NAMES', 'ROSTER_CELL', 'RECOGNIZED', 'LABEL_BEFORE', 'ADDRESSES_CHAIR', 'hearing_roster', 'roster_hits', 'roster_persons', 'context_state', 'adjacent_label',
              'addresses_chair', 'turn_index', 'refine_rows', 'person_of_member', '_flat',
              'EVIDENCE', 'refine_table', 'apply_refine', 'undo_refine', 'refine_cmd', '_ensure_cols', 'guard_roster', 'apply_reconciled', 'undo_applied', 'counts', 'roster_members']


@pytest.fixture(scope='module')
def tool(cr):
    """backfill_attribution's functions executed against the synthetic roster (its own stdlib imports, so a forgotten import fails here)."""
    import ast
    path = FEC / 'collect' / 'backfill_attribution.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    keep = [n for n in tree.body if (isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in TOOL_FUNCS)
            or (isinstance(n, ast.Assign) and any(getattr(t, 'id', '') in TOOL_FUNCS for t in n.targets))]
    imports = [n for n in tree.body if isinstance(n, ast.Import)]
    scope = {'CR': cr, 'HEAD': 120, 'ROOT': cr.B, 'now': lambda: '2026-10-20T23:00:00', '__name__': 'tool_under_test'}
    exec(compile(ast.Module(body=imports, type_ignores=[]), str(path), 'exec'), scope)
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(path), 'exec'), scope)
    return scope
