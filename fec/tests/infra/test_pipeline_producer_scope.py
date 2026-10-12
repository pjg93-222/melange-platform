"""Only scheduled chain producers share the global basename namespace."""
import ast
import os
from pathlib import Path
import pytest


def resolve(root):
    source=Path(__file__).resolve().parents[2]/'run_pipeline.py'
    tree=ast.parse(source.read_text(encoding='utf-8'))
    begin=next(i for i,n in enumerate(tree.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_chain_producer_names' for t in n.targets))
    end=next(i for i,n in enumerate(tree.body[begin:],begin) if isinstance(n,ast.For) and isinstance(n.target,ast.Tuple) and any(isinstance(t,ast.Name) and t.id=='_chain_script' for t in n.target.elts))
    env={'os':os,'FE':str(root),'CHAIN':[('scheduled.py',[])]}
    exec(compile(ast.Module(body=tree.body[begin:end+1],type_ignores=[]),str(source),'exec'),env)
    return env['_producer_paths']


def test_duplicate_unrelated_helpers_do_not_block_chain(tmp_path):
    for directory in ('surfaces','research/a','research/b'):(tmp_path/directory).mkdir(parents=True)
    for directory in ('research/a','research/b'):(tmp_path/directory/'write_report.py').write_text('')
    (tmp_path/'surfaces/scheduled.py').write_text('')
    assert resolve(tmp_path)=={'scheduled.py':str(tmp_path/'surfaces/scheduled.py')}


def test_duplicate_chain_producer_still_fails(tmp_path):
    for directory in ('a','b'):
        (tmp_path/directory).mkdir();(tmp_path/directory/'scheduled.py').write_text('')
    with pytest.raises(RuntimeError,match='duplicate producer basename scheduled.py'):resolve(tmp_path)


def test_missing_chain_producer_still_fails(tmp_path):
    with pytest.raises(RuntimeError,match='chain producer script not found'):resolve(tmp_path)


def test_evidence_producers_precede_their_surface_consumers():
    source=Path(__file__).resolve().parents[2]/'run_pipeline.py'
    tree=ast.parse(source.read_text(encoding='utf-8'))
    chain=next(ast.literal_eval(node.value) for node in tree.body
               if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='CHAIN' for t in node.targets))
    names=[name for name,args in chain]
    assert names.index('build_vote_category.py') < names.index('per_category_stance.py')
    assert names.index('b5_profile_basis.py') < names.index('build_member_narrative_ledger.py')
    assert names.index('build_member_longform_turn_features.py') < names.index('build_member_narrative_ledger.py')
    # member_narratives_v2.json's chain producer is regenerate_member_profiles.py --activate (the ratified
    # profiles); regenerate_descriptive_profiles.py is the superseded 09-04 edition and is not a chain step.
    assert names.index('build_member_narrative_ledger.py') < names.index('regenerate_member_profiles.py')
    assert names.index('regenerate_member_profiles.py') < names.index('build_dashboard_bundle.py')
