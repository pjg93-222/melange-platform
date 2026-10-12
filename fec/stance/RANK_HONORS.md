# Behavior-qualified recognition

`field_rank_honors_v1` governs field recognition; `overall_primacy_standing_v2` governs overall labels on the existing primacy board. Both policies, canonical evidence and all member records live together in an immutable generation under `data/stance/rank_honors/<generation_id>/`. `active.json` selects the current generation and binds the hashes of its three JSON files.

## Code divergence (2026-10-04)

Added on 2026-10-04 to record where the code has moved past this document. The text below this section is unchanged since commit `11a34e215` (2026-09-18). Checked against Melange `main` at `4a81d5022` and the published dashboard and desk bundles of 2026-10-01 (honors generation `39f02106174bddae14fed585`, schema `rank-honors-v2`); only the points listed here were re-checked.

- **Field ladder.** "Award rules" below describes the legacy ladder. Since commit `e3e5ece7c` (2026-09-16; brain decision D-031), `HONORS_LADDER` defaults to `g` (`fec/stance/build_rank_honors.py:275`); `HONORS_LADDER=legacy` restores the rules as written.
- **Rule G bands** (`fec/stance/rank_honors_policy.py:28-43`, `:181-201`, `:302-322`). Members with an accepted action in a field are split by the sign of their score, and the cutoffs are taken within each signed sub-cohort. Positive: Champion top 5%, Ally to 15%, Highly Persuadable to 20%, Persuadable to 50%, Lean Persuadable for the rest; in Interchange, Credit Card Mandates and CU Tax Status the top band is named Defender. Negative: Opponent for the most negative 5%, Highly Skeptical to 15%, Skeptic to 30%, Lean Skeptic for the rest. A zero net score is True Neutral. A tie that does not fit inside a cutoff falls to the next milder band that it fits, so only a member with no accepted action in the field has no label.
- **Rule G score.** The ranking value is a signed per-service-year rate: each action's weight times its direction, summed and divided by the member's House or Senate service years between 2021-01-03 and the as-of date, held between 0.5 and 6 (`fec/stance/build_rank_honors.py:44-83`). No Congress decay applies, and the tensor tie-break is divided by the same service years (`fec/stance/rank_honors_policy.py:128-147`). Votes weigh 0.4 instead of 1; sponsor, original-cosponsor and cosponsor weights stay 3/2/1.
- **Displayed overall label.** Since the same commit, `OVERALL_LABEL` defaults to `analytic`: the dashboard and member desk show the analytic overall band, with the board's top 13 crowned Champion, instead of the rank-based standing described under "Overall primacy and stance" (`fec/surfaces/dashboard_honors.py:18-36`, `:76-79`; `fec/surfaces/make_member_desk.py:2631-2665`). That standing is still computed, kept in the generation and written to the workbook's Overall Standing sheet (`fec/stance/honors_workbook.py:73-89`); `OVERALL_LABEL=legacy` displays it again. Overall Champion is unchanged: the active board's top 13, each with a qualifying action (`fec/stance/rank_honors_policy.py:326-361`).

## Award rules

Only members with an accepted action in the particular field belong to that field's cohort. Accepted actions include source-reconciled sponsorship, original cosponsorship, cosponsorship, accepted letters, explicit neutral legislative records, and actual final-passage Yea/Nay votes in Congresses 117–119. Paired, announced, absent, unresolved and out-of-window records do not qualify. Media mentions and inferred stances are not actions.

The primary ranking value is the **raw signed action sum**: direction × role weight × Congress decay, summed across accepted actions. Sponsor/original cosponsor/cosponsor weights are 3/2/1; vote weight is 1. Congress 119/118/117 decay is 1/0.8/0.64. No percentile, min-max, rank-weight or present-signal normalization enters this value. Zero, negative and neutral observed records still belong to the denominator. Positive recognition requires at least one supportive action and a positive raw sum.

Within equal primary-score groups only, use the raw signed CU tensor sum. Its action sign is the member's position on the measure, preventing double inversion for an opposing measure. Every member in the equal-primary group must have complete tensor coverage; missing values are never substituted with zero. Any remaining merit tie shares one rank interval. Stable member IDs order storage only and never decide an award.

The cumulative cutoffs are `floor(5% × N)` for Champion, `floor(15% × N)` for Ally, and `floor(20% × N)` for Highly Persuadable. The highest qualifying award is shown. The entire residual tie must fit inside a cutoff. Consequently small cohorts and ties can leave awards unfilled. Changing Champion to another integer from 1–5 is an explicit versioned policy change, not an automatic adjustment by field.

Overall Champion belongs to exactly the first 13 members of the active overall board. Every one must have qualifying action evidence; a missing action stops publication. This policy consumes the active board and does not activate a different overall ranking formula.

## Overall primacy and stance

The existing primacy/tensor board remains the only overall order. Its Tier 1/2/3 memberships and ranks are retained. Champion is the top 13. The stance rank is this same board rank, never a second action-volume rank.

Positive rank bands are cumulative: Ally top 15%, Highly Persuadable top 20%, Persuadable top 50%, Lean Persuadable top 75%. Qualifying supportive action is required. The 75% limit is a versioned implementation default for the authorized correction. Below these bands, an observed member is Uncommitted unless there is opposition evidence. Uncommitted means the record does not establish sufficient overall CU commitment; it does not claim neutrality. Neutral-only observed records are True Neutral. No recorded action remains Insufficient evidence.

Adverse leadership can cap a rank-based label. Its evidence must have both an opposing accepted action and a position adverse to CU interests in the beneficiary-primacy tags or CU tensor. The tensor takes precedence where available. Missing tensor coverage does not erase an explicitly adverse beneficiary tag, and a broad category disagreement on a shared-benefit measure does not establish core CU opposition. The evidence ledger retains all adverse actions, the qualifying core measures, leadership measures, and Congresses separately.

| Evidence of opposition to CU interests | Label |
|---|---|
| Sponsorship/original cosponsorship on at least two distinct core adverse measures spanning at least two Congresses | Opponent |
| At least two distinct core adverse leadership measures within one Congress | Highly Skeptical |
| A core adverse sponsorship, or at least two distinct core adverse measures | Skeptic |
| Other opposing action with nonpositive action balance or primacy below the top 50% | Lean Skeptic |

An isolated original cosponsorship does not erase a strong primacy record. Its mixed evidence remains visible. Opposing leadership is not canceled by adding unrelated positive events. Negative classifications are not quotas, and do not depend on the size of a net-negative cohort. No names, parties, or bespoke member overrides enter these rules. A Champion with qualifying severe opposition blocks publication for reconciliation instead of silently displaying contradictory labels.

Rank-band exceptions are explicit evidence caps; tier sorting still uses the unchanged primacy board. Signed action totals and full-tensor coverage remain diagnostics, not an alternative ranking. The subfield policy and evidence gates are unchanged.

## Display and analytical contracts

- Public recognition and the observed action record are separate: Supportive, Opposing, Mixed, Neutral, or No recorded action. A low rank never implies opposition, and silence never earns a label.
- Dashboard `honors` / `overall_honors` and member-desk `honors` records supply visible labels, ranks, colors, filters and explanations.
- Existing analytical `stance`, `gen`, category matrices and model scores retain their original values. Those older ladder names remain internal model inputs; they are not recalibrated by this display change.
- Excel exposes **Field Recognition**, **Recognition Detail**, and **Overall Standing**. The existing workbook views show primacy rank, stance, rank basis and action evidence. There is no separate observed-action ranking. Old analytical stance tabs/columns are hidden, while their cell values, formulas and cached results are preserved.
- Dashboard, desk and combined HTML carry one matching generation marker. Combination and email packaging reject missing or mixed markers. Workbook receipts bind the selected generation and the authored package.

## Build and validation

The canonical pipeline builds evidence and honors after the active overall board, audits them, synchronizes the workbook, and renders the surfaces. The primacy engine-input stage creates only the analytical desk bundle so it does not depend on a recognition generation that has yet to be built.

For a standalone candidate, use the project's existing Python runtime:

```text
python fec/stance/build_rank_honors.py --as-of YYYY-MM-DD
python fec/audit/audit_rank_honors.py --generation <24-character-generation-id>
```

`--publish` atomically updates the active pointer. Rebuild both dashboard and member desk before combining or packaging, then run `python fec/audit/audit_rank_honors.py --surfaces`. Use the existing pipeline writer lease for a coordinated publication. The active loader rejects modified source hashes, a different overall-board mode, incorrect ordering and fabricated evidence summaries.

Run the registered suites: `test_rank_honors`, `test_overall_stance_policy`, `test_audit_rank_honors`, `test_honors_workbook`, `test_dashboard_honors`, `test_member_desk_honors`, and `test_honors_publication`. The member-desk suite also invokes its Node rendering tests. The independent audit recomputes field and overall ordering, the overall board, beneficiary/tensor opposition scope, rank bands, mixed and neutral states, priority memberships and deduplicated evidence directly from normalized action rows.

`transition_report.json` includes all old/new field records, chamber counts, unresolved cutoff ties and vote-weight sensitivity at 0.5/1/2, plus full overall rows and counts by standing, priority tier and record pattern.

Email packages retain the existing 14 MiB limit. When standard level-9 ZIP compression exceeds it, the package builder uses `zopfli==0.4.3` (pinned in `fec/requirements-honors.txt`) for stronger standard DEFLATE compression. It verifies every extracted entry's bytes, CRC and metadata before replacement, and still refuses an oversized result. This fallback takes longer than ordinary packaging and changes no delivered HTML content. Detailed event IDs stay in the immutable evidence artifact rather than being repeated in both page payloads.

## Repeated builds and rollback

Before a managed workbook model update, `honors_workbook.py --prepare-model-update` reverts the publication. The pipeline invokes this before `sync_stance_g.py`; the following honors publication captures a new baseline. A fresh audit workbook can explicitly replace its baseline only when it contains no managed recognition sheet.

A publication owns three recognition sheets, the label column it hides on `Rank & Allocation` (G) and `Legislative Agenda` (C), and the `Category Stance`/`Coalition Profiles` tab states. The binding records a digest of that region -- the views' cell VALUES, so an Excel or openpyxl rewrite of the same content is not an edit -- alongside the whole-file hashes it keeps as provenance. When the package is still byte-for-byte as published the whole recorded file is restored, which is the only lossless answer. When other sheets have moved on, only that region is reverted and those edits are kept; every other ZIP entry is carried across byte for byte and verified, including `xl/styles.xml`, whose appended entries stay because an intervening edit may reference a style index appended after the publication. An edit INSIDE a recognition view still refuses, naming the sheet.

A publication may therefore land on top of unrelated edits, and it then inherits the previous publication's rollback copy, which predates them. The binding records that as `original_exact`, and such a binding is only ever reverted by region -- never by whole-file restore, which would discard that work. Exactness is sticky: a later clean publication does not hand it back; the next rollback plus publication does, because that captures a fresh copy of the kept state.

`HONORS_WORKBOOK_SCOPE=legacy` restores the pre-2026-09-18 behaviour: the binding is the whole-file hash and any intervening edit to any sheet refuses. Registered suites: `test_honors_workbook`, `test_honors_workbook_region`.

`HONORS_MODE=legacy` enables a coordinated legacy rebuild. The honors builder then leaves the active pointer alone, the workbook restores its hash-checked original package, and both surface renderers use their legacy projections. Rebuild all dependent HTML and packages together: active-marked pages cannot be packaged in legacy mode. Clearing the variable re-enables the active recognition policy.
