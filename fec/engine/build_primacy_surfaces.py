#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build primacy engine inputs and synchronize the public surfaces.

The allocation engines now consume the discrete primacy tier as an anchor,
then add the shared leave-one-topic-out orientation adjustment. The pipeline
therefore runs the input half before every engine and the surface half after
the engine artifacts exist. The official reporting rank remains guarded
byte-for-byte throughout.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys

FEC_BOOTSTRAP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if FEC_BOOTSTRAP not in sys.path:
    sys.path.insert(0, FEC_BOOTSTRAP)

from paths import ROOT


FEC = FEC_BOOTSTRAP
RANKS = os.path.join(ROOT, 'ranks_python.json')
ENGINE_INPUT_STEPS = (
    ('legislation/legislation_culling_status.py',),
    ('legislation/build_registry_anchor_backfill.py',),
    ('legislation/build_bank_anchor_backfill.py',),
    ('surfaces/make_member_desk.py', '--data', '--analytic-inputs-only'),
    ('engine/primacy_factors.py', ROOT),
    # D-031, moved here 2026-09-16 (was a standalone run_pipeline.py step). The
    # per-area grade's rankable set is
    #   congestion.json AND ranks_python.json['pcts'] AND primacy_factors.json,
    # so running it BEFORE the factor made both its member set and its recorded
    # provenance hash lag a full cycle. The 2026-09-17 run is the receipt:
    # per_area_grade.json (written 02:15:42) recorded a primacy_factors.json
    # hash that this step overwrote at 04:42:04, and the board test's provenance
    # check went red with no shipped code having changed.
    #
    # Nothing between the desk pass above and here reads the grade's VALUES:
    # primacy_factors.main consumes member_desk_bundle.json for members[].bills
    # only (primacy_factors.py:485-501), never tier/tierrank, so the board that
    # pass computes is inert for the factor. What the pass DOES need is for the
    # artifact to EXIST -- make_member_desk.build_bundle calls
    # legislative_press_tiers unconditionally (make_member_desk.py:2520), and
    # under PRIMACY_BOARD=per_area_v1 tier_util fails loud without it. On a tree
    # that has never built one, main() prepends GRADE_STEP to the step tuple it is
    # about to run (:94-99) so the grade runs once up front -- there is no separate
    # seeding helper.  In steady state the file is there and the grade runs once a cycle,
    # here, where every one of its eleven inputs is final.
    #
    # PRIMACY_BOARD dial semantics are untouched: the grade is built either way,
    # and under PRIMACY_BOARD=factor it is simply read by nobody.
    ('engine/per_area_grade.py', '--root', ROOT),
)
GRADE_STEP = ('engine/per_area_grade.py', '--root', ROOT)
GRADE_ARTIFACT = os.path.join(ROOT, 'data', 'engine', 'per_area_grade.json')
SURFACE_STEPS = (
    ('stance/build_rank_honors.py', '--publish'),
    ('stance/honors_workbook.py', '--prepare-model-update'),
    ('stance/sync_stance_g.py',),
    ('stance/honors_workbook.py',),
    ('surfaces/build_dashboard_bundle.py',),
    ('surfaces/make_dashboard_html.py',),
    ('surfaces/make_member_desk.py',),
)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def with_affirmative_factor_seed(steps, root=ROOT):
    """Migrate missing factor evidence before the analytic desk's tier reader.

    The seed runs the real factor producer against the existing desk source;
    the ordinary desk -> factor -> grade sequence still rebuilds this cycle's
    inputs afterward. Invalid present evidence refuses instead of being reset.
    """
    import tier_util
    steps = tuple(steps)
    if (tier_util.PRIMACY_BOARD not in ('factor', 'per_area_v1')
            or tier_util.PRIMACY_T1_MIN_AFFIRMATIVE == 0
            or not any(step[0] == 'engine/primacy_factors.py' for step in steps)):
        return steps
    if tier_util.affirmative_schema_seed_required(root):
        return (('engine/primacy_factors.py', root),) + steps
    return steps


def main(argv=None):
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--engine-inputs-only', action='store_true')
    group.add_argument('--surfaces-only', action='store_true')
    args = parser.parse_args(argv)
    if args.engine_inputs_only:
        steps = ENGINE_INPUT_STEPS
        label = 'engine inputs'
    elif args.surfaces_only:
        steps = SURFACE_STEPS
        label = 'surfaces'
    else:
        steps = ENGINE_INPUT_STEPS + SURFACE_STEPS
        label = 'engine inputs + surfaces'
    if GRADE_STEP in steps and not os.path.exists(GRADE_ARTIFACT):
        # Cold start only: ENGINE_INPUT_STEPS[3] seats the board before the
        # grade's own slot, so a tree that has never built the artifact would
        # halt there. Seeding costs one extra producer run on that tree alone.
        steps = (GRADE_STEP,) + tuple(steps)
        label = '%s (+ per-area grade cold-start seed)' % label
    migrated_steps = with_affirmative_factor_seed(steps)
    if migrated_steps != tuple(steps):
        label = '%s (+ affirmative evidence schema seed)' % label
    steps = migrated_steps
    before = sha256(RANKS)
    for step in steps:
        script, *arguments = step
        completed = subprocess.run(
            [sys.executable, os.path.join(FEC, script), *arguments],
            cwd=ROOT,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )
        tail = [
            line
            for line in (completed.stdout or '').splitlines()
            if line.strip()
        ][-1:]
        print(
            '%-30s %s  %s'
            % (
                script,
                'OK' if completed.returncode == 0 else 'FAIL',
                tail[0][:120] if tail else '',
            )
        )
        if completed.returncode:
            if completed.stdout:
                print(completed.stdout[-1200:])
            if completed.stderr:
                print(completed.stderr[-1200:], file=sys.stderr)
            return completed.returncode
    after = sha256(RANKS)
    if after != before:
        raise RuntimeError(
            'primacy build changed ranks_python.json; reporting-rank '
            'invariant violated'
        )
    print(
        'primacy %s: complete | official rank authority byte-identical'
        % label
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
