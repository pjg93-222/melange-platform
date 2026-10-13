"""A synthetic Mentat layout for the compute-job tests: the anchors World of test_giving_anchors, arranged the way a job finds
things (LOCALAPPDATA, MENTAT_REPORT_DIRECTORY, inputs/active_fec_sources.json), so a copy of it at another root stands in for a
container's mounts. Synthetic data only."""
import json
from pathlib import Path
import shutil

from test_giving_anchors import World


def layout_args(base):
    """-> (env, extra): the environment and the `compute_job.py run anchors --` arguments that build the anchors from the layout at `base`."""
    base = Path(base)
    env = {'LOCALAPPDATA': str(base / 'localappdata'), 'MENTAT_REPORT_DIRECTORY': str(base / 'reports'), 'ANCHORS_GLASSBOX_DIR': str(base / 'glassbox'),
           'GLASSBOX_EVENTS_DIR': str(base / 'glassbox'), 'MENTAT_COMPUTE_RECEIPT_DIR': str(base / 'receipts')}
    extra = ['--project-root', str(base / 'mentat'), '--registry', str(base / 'registry.json'), '--controls', str(base / '00_controls.xlsx'),
             '--output-dir', str(base / 'out')]
    return env, extra


def make_layout(base):
    base = Path(base)
    world = World(base)
    derived = base / 'localappdata' / 'Melange' / 'lda_rebuild' / 'derived'
    (derived / 'sector_spend').mkdir(parents=True)
    shutil.copy(base / 'sector_spend.csv', derived / 'sector_spend' / 'sector_spend.csv')
    shutil.copy(base / 'fullscope_receipts.json', derived / 'fullscope_receipts.json')
    (world.project / 'inputs').mkdir()
    (world.project / 'inputs' / 'active_fec_sources.json').write_text(json.dumps({'path': str(world.fec)}), encoding='utf-8')
    (base / 'registry.json').write_text(json.dumps(world.registry), encoding='utf-8')
    return layout_args(base)


def move_layout(source, target):
    """Copy a layout to another root and repoint its recorded FEC source, as a container's mounts would."""
    shutil.copytree(source, target)
    (Path(target) / 'mentat' / 'inputs' / 'active_fec_sources.json').write_text(json.dumps({'path': str(Path(target) / 'fec')}), encoding='utf-8')
    return layout_args(target)
