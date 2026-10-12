"""Resolve frozen-manifest source identities to their current live paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any


# 2026-08-05, Phase 2+3 reorganization: the frozen manifest stays immutable
# and remains the identity key. This map only redirects where the live copy is
# read; drift semantics remain live bytes versus the frozen copy.
# per_topic_coalition.json and per_topic_coalition.py do not collide because
# keys are full basenames, including their extensions.
LIVE_RELOCATIONS = {
    "pillar_inputs_snapshot.json": "data/engine/pillar_inputs_snapshot.json",
    "electoral_plays.json": "data/money/electoral_plays.json",
    "per_topic_coalition.json": "data/lobbying/per_topic_coalition.json",
    "budget_frontier.json": "data/money/budget_frontier.json",
    "budget_frontier_electoral.json": (
        "data/money/budget_frontier_electoral.json"
    ),
    "build_archive_legislative.py": (
        "fec/legislation/build_archive_legislative.py"
    ),
    "behavior_panel.py": "fec/members/behavior_panel.py",
    "behavioral_dispositions.py": "fec/members/behavioral_dispositions.py",
    "coop_v2_candidate.py": "fec/members/coop_v2_candidate.py",
    "vote_by_category.py": "fec/legislation/vote_by_category.py",
    "per_category_stance.py": "fec/stance/per_category_stance.py",
    "apply_leg_stance.py": "fec/legislation/apply_leg_stance.py",
    "compute_pillars.py": "fec/engine/compute_pillars.py",
    "build_multilayer_network.py": "fec/network/build_multilayer_network.py",
    "build_dashboard_bundle.py": "fec/surfaces/build_dashboard_bundle.py",
    "build_electoral_plays.py": "fec/engine/build_electoral_plays.py",
    "per_topic_flip.py": "fec/engine/per_topic_flip.py",
    "per_topic_coalition.py": "fec/engine/per_topic_coalition.py",
    "budget_frontier.py": "fec/engine/budget_frontier.py",
    "optimize.py": "fec/engine/optimize.py",
    "build_im_allocation.py": "fec/engine/build_im_allocation.py",
    "salience_shock.py": "fec/engine/salience_shock.py",
    "make_dashboard_html.py": "fec/surfaces/make_dashboard_html.py",
    "make_member_desk.py": "fec/surfaces/make_member_desk.py",
    "dashboard_integrity.py": "fec/audit/dashboard_integrity.py",
    "make_combined.py": "fec/surfaces/make_combined.py",
    "say_do_gap.py": "fec/stance/say_do_gap.py",
}


def resolve_live_source(value: Any, root: Path) -> Path:
    """Return the current live path for a manifest-recorded source."""
    recorded_source = Path(str(value))
    relocated = LIVE_RELOCATIONS.get(recorded_source.name)
    if relocated is not None:
        return (root / relocated).resolve()
    if not recorded_source.is_absolute():
        recorded_source = root / recorded_source
    return recorded_source.resolve()

