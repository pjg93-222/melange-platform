"""Replay the native allocation algorithms using frozen baseline or results tiers.

Runs in a fresh subprocess with the original workspace as a read-only source.
Only moneyball_core's tier state and atomic output destinations are substituted;
the native money, network, engagement, receptivity and allocation code executes
unchanged. An audit hook rejects every attempted write outside the worker's
output directory. This is a current-network sensitivity experiment for each
Congress's results, not a historical allocation reconstruction.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys


def _sha(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _worker(args):
    source = Path(args.source_root).resolve()
    output = Path(args.output).resolve()
    shadow = Path(args.shadow_root).resolve()
    board_path = Path(args.board).resolve()
    if shadow not in output.parents or shadow not in board_path.parents:
        raise ValueError('Worker inputs and outputs require the explicit shadow root')
    board = json.loads(board_path.read_text(encoding='utf-8'))
    if board.get('schema') != 'effectiveness-board-priority-comparison-v1' or board.get('status') != 'shadow_candidate':
        raise ValueError('Expected a versioned shadow board comparison')
    if source == output or source in output.parents:
        raise ValueError('Allocation output must be outside the read-only source root')
    output.mkdir(parents=True, exist_ok=True)
    sys.dont_write_bytecode = True
    reads = set()
    hashing = False
    initial_hashes = {}

    def within(path, root):
        return path == root or root in path.parents

    def audit(event, params):
        nonlocal hashing
        if event == 'open':
            filename, mode, flags = params
            if not isinstance(filename, (str,bytes,os.PathLike)):
                return
            resolved = Path(os.fsdecode(filename)).resolve()
            writing = any(flag in str(mode or '') for flag in ('w','a','x','+')) or bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
            if writing and not within(resolved,output):
                raise PermissionError('Allocation replay blocked write outside shadow output: '+str(resolved))
            if not writing and within(resolved,source) and resolved.is_file() and not hashing:
                reads.add(str(resolved))
                if str(resolved) not in initial_hashes:
                    hashing = True
                    try: initial_hashes[str(resolved)] = _sha(resolved)
                    finally: hashing = False
        elif event in ('os.rename','os.replace'):
            for filename in params[:2]:
                if not within(Path(filename).resolve(),output):
                    raise PermissionError('Allocation replay blocked move outside shadow output')
        elif event in ('os.remove','os.rmdir','os.mkdir'):
            if not within(Path(params[0]).resolve(),output):
                raise PermissionError('Allocation replay blocked filesystem mutation outside shadow output')
        elif event in ('subprocess.Popen','os.system'):
            raise PermissionError('Nested process execution is not allowed in allocation replay')

    sys.addaudithook(audit)
    # Prefer the exact source generation's algorithms and calibration modules.
    # They were inspected to use local readers; output calls are intercepted.
    os.environ['MONEYBALL_ROOT'] = str(source)
    os.environ['NET_CONFIG'] = 'cu'
    os.environ['PRIMACY_BOARD'] = 'factor'
    os.environ['SEED'] = '7'
    os.environ['MC'] = '20'
    os.environ.pop('EFFECTIVENESS_COMPARISON_PATH',None)
    sys.path.insert(0,str(source/'fec'))
    import moneyball_core as mc
    original_state = mc.primacy_engine_state
    original_dump = mc.dump_atomic
    calls = []

    def tier_state(roster=None):
        names = list(roster if roster is not None else mc.members())
        if args.variant == 'baseline':
            result = original_state(names)
            # Verify native baseline agrees with the frozen comparison authority.
            for name in names:
                if result[0][name] != board['members'][name]['legacy_tier']:
                    raise ValueError('Baseline tier source drift for '+name)
            return result
        missing = set(names) - set(board['members'])
        if missing:
            raise ValueError('Candidate board misses allocation members: '+str(sorted(missing)))
        return tuple({name:board['members'][name][field] for name in names}
                     for field in ('results_tier','results_board_rank','results_priority'))

    names = {'flip':'per_topic_flip.py', 'coalition':'per_topic_coalition.py'}
    expected = {'flip':'per_topic_alloc.json','coalition':'per_topic_coalition.json'}

    def dump(document,path,**kwargs):
        if Path(path).name != expected[args.engine]:
            raise PermissionError('Unrecognized allocation output: '+str(path))
        target = output/expected[args.engine]
        original_dump(document,str(target),**kwargs)
        calls.append(str(target))

    mc.primacy_engine_state = tier_state
    mc.dump_atomic = dump
    script = source/'fec'/'engine'/names[args.engine]
    sys.argv = [str(script)]
    runpy.run_path(str(script),run_name='__main__')
    if len(calls) != 1:
        raise ValueError('Native engine did not emit exactly one allocation artifact')
    hashing = True
    final = {path:_sha(path) for path in sorted(reads)}
    if initial_hashes != final:
        raise ValueError('Source changed during allocation replay; outputs are not certified')
    receipt = {'schema':'effectiveness-allocation-replay-v1','status':'complete',
               'variant':args.variant,'engine':args.engine,'congress':board.get('congress'),
               'board_source_sha256':board['source_sha256'],'board_file_sha256':_sha(args.board),
               'source_root':str(source),'source_hashes':final,'source_unchanged':True,
               'output_sha256':{path:_sha(path) for path in calls},
               'replay_implementation_sha256':_sha(__file__),
               'scope':'Native current-network allocation with frozen current nonlegislative inputs; only the tier/priority authority changes. Historical results do not recreate historical networks or budgets.',
               'environment':{key:os.environ.get(key) for key in ('NET_CONFIG','PRIMACY_BOARD','SEED','MC','HALFF','RECEPT_LAYER','IMPUTE_IND0','BRIDGE','PERS_NONE','COMPET_MULT','COMPET_SRC')},
               'candidate_coverage':board['summary']}
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2,sort_keys=True),encoding='utf-8')


def summarize(baseline_path, candidate_path):
    baseline=json.loads(Path(baseline_path).read_text(encoding='utf-8'))
    candidate=json.loads(Path(candidate_path).read_text(encoding='utf-8'))
    topics={}
    old_total=Counter();new_total=Counter()
    for topic in sorted(set(baseline)|set(candidate)):
        old=baseline.get(topic,{});new=candidate.get(topic,{})
        changes={name:{'baseline':old.get(name,0),'candidate':new.get(name,0),
                       'delta':new.get(name,0)-old.get(name,0)}
                 for name in sorted(set(old)|set(new)) if old.get(name,0)!=new.get(name,0)}
        topics[topic]={'baseline_spent':sum(old.values()),'candidate_spent':sum(new.values()),
                       'changed_members':len(changes),'changes':changes}
        old_total.update(old);new_total.update(new)
    return {'schema':'effectiveness-allocation-dollar-comparison-v1','status':'shadow_candidate',
            'scope':'Native per-topic diagnostic allocation replay. Each topic has its own budget; totals across topics are not one deployable joint budget.',
            'topics':topics, 'summary':{'topics_changed':sum(bool(row['changed_members']) for row in topics.values()),
                'member_topic_absolute_dollar_change':sum(abs(row['delta']) for topic in topics.values() for row in topic['changes'].values()),
                'baseline_topic_budget_sum':sum(old_total.values()),'candidate_topic_budget_sum':sum(new_total.values()),
                'aggregate_member_changes':{name:new_total[name]-old_total[name] for name in sorted(set(new_total)|set(old_total)) if new_total[name]!=old_total[name]}}}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',required=True)
    parser.add_argument('--board',required=True)
    parser.add_argument('--shadow-root',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--engine',choices=('flip','coalition'),required=True)
    parser.add_argument('--variant',choices=('baseline','candidate'),required=True)
    parser.add_argument('--worker',action='store_true')
    args=parser.parse_args(argv)
    if args.worker:
        _worker(args)
        return 0
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from engine.effectiveness_integration import require_shadow_path
    output=require_shadow_path(Path(args.output)/'receipt.json',args.shadow_root).parent
    if Path(args.source_root).resolve() == output or Path(args.source_root).resolve() in output.parents:
        raise ValueError('Allocation outputs cannot be inside source root')
    require_shadow_path(args.board,args.shadow_root)
    output.mkdir(parents=True,exist_ok=True)
    command=[sys.executable,str(Path(__file__).resolve()),*(list(argv) if argv is not None else sys.argv[1:]),'--worker']
    with open(output/'stdout.log','w',encoding='utf-8') as stdout, open(output/'stderr.log','w',encoding='utf-8') as stderr:
        result=subprocess.run(command,stdout=stdout,stderr=stderr)
    if result.returncode:
        print((output/'stderr.log').read_text(encoding='utf-8')[-3000:],file=sys.stderr)
    return result.returncode


if __name__=='__main__':
    raise SystemExit(main())
