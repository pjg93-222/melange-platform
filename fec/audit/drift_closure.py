"""Pinned post-refresh downstream drift closure."""

EXPECTED_CHANGED_NAMES = frozenset(
    {
        "Moneyball Main Version - Blended.xlsx",
        "behavior_panel.json",
        "behavioral_dispositions.json",
        "member_receptivity.json",
        "category_stance.json",
        "ranks_python.json",
        "electoral_plays.json",
        "per_topic_alloc.json",
        "per_topic_coalition.json",
        "budget_frontier.json",
        "budget_frontier_electoral.json",
        "optimize_result.json",
        "im_allocation.json",
        "salience_advisory.json",
        "dashboard_data.json",
        "Moneyball Dashboard.html",
        "member_desk_bundle.json",
        "dashboard_exclusion_reconciliation.json",
        "Moneyball.html",
        "cu-member-desk.html",
        # 2026-08-04: The exact pin grows 20 -> 36 with 0 healed files;
        # ranks_python.json remains its existing entry. Authorized additions map
        # to industry + interactions surfaces (run_pipeline.py, make_combined.py;
        # 15560ea/a14543e), coalition-builder fixes (optimize.py,
        # per_topic_flip.py, per_topic_coalition.py, budget_frontier.py;
        # 03e68de), coalition-pricing/desk evidence (make_member_desk.py),
        # lt_priming deployment (make_dashboard_html.py), legislative-audit and
        # freshness wiring (compute_pillars.py, per_category_stance.py,
        # dashboard_integrity.py, build_dashboard_bundle.py,
        # build_archive_legislative.py; dd60333 lane and after), and regenerated
        # chain artifacts (archive_leg.json, legislation_routing_audit.json,
        # pillar_inputs_snapshot.json). The exact-closure rule remains in force.
        "fec/run_pipeline.py",
        "fec/make_combined.py",
        "fec/optimize.py",
        "fec/per_topic_flip.py",
        "fec/per_topic_coalition.py",
        "fec/budget_frontier.py",
        "fec/make_member_desk.py",
        "fec/make_dashboard_html.py",
        "fec/compute_pillars.py",
        "fec/per_category_stance.py",
        "fec/dashboard_integrity.py",
        "fec/build_dashboard_bundle.py",
        "fec/build_archive_legislative.py",
        "archive_leg.json",
        "legislation_routing_audit.json",
        "pillar_inputs_snapshot.json",
        # 2026-08-05: Phase 3 bootstrap bumps changed producer source bytes;
        # exact closure retained. These eleven manifest identities are the
        # reorganization-only source drift found beyond the 36-file pin.
        "fec/apply_leg_stance.py",
        "fec/behavior_panel.py",
        "fec/behavioral_dispositions.py",
        "fec/build_electoral_plays.py",
        "fec/build_im_allocation.py",
        "fec/build_multilayer_network.py",
        "fec/coop_v2_candidate.py",
        "fec/salience_shock.py",
        "fec/say_do_gap.py",
        "fec/vote_by_category.py",
        "fec/weekly_refresh.py",
    }
)

CHANGED_COUNT = 47
TOTAL = 55
UNCHANGED_COUNT = TOTAL - len(EXPECTED_CHANGED_NAMES)

assert len(EXPECTED_CHANGED_NAMES) == CHANGED_COUNT, (
    "EXPECTED_CHANGED_NAMES must contain exactly "
    f"{CHANGED_COUNT} entries, got {len(EXPECTED_CHANGED_NAMES)}"
)
