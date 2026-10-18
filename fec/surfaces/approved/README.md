# Approved MELANGE presentation

Implements the September 21, 2026 selection brief against the existing generated data and analytical engines. The authoritative selection/tokens remain in `outputs/higgsfield-tab-mockups-2026-09-21/standardized`. This direction supersedes conflicting historical UI descriptions.

This is an **Operate** surface: dense research, visible source context, permanent desktop navigation, organic Influence network, and cream dossier/research views. Freight Text Pro / Georgia / Times and Articulat / Aptos / Segoe UI stacks remain the application fonts. No new font service or frontend framework was added.

Run from the repository root:

```powershell
python fec/surfaces/build_approved_preview.py
```

The builder updates `Moneyball_Redesign.html` and bundles these adapters into the live server's existing CSS/JS assets. It does not rerun models, change source records, regenerate the published dashboard, or deploy. The existing local preview uses port 8841 with `--dashboard-file Moneyball_Redesign.html`.

This command targets the legacy generated combined preview, which contains exactly one desk hook and one network hook. It deliberately refuses a different format. A preview beginning with `<!-- melange-clean-workspace-v2 -->` uses the separate `fec/surfaces/build_clean_workspace.py` builder; `fec/surfaces/sync_live_dashboard_alias.py` selects that workflow and binds its receipt to the current generation. Do not inject legacy hooks into the clean shell or replace it with a generated combined page to bypass the refusal.

Files:

- `workspace.js`: shared navigation, nine dossier tabs, source scope, directory preview, meeting notes/contact entry, and cited HTML export.
- `research.js`: sector comparisons, chamber-specific action counts, forecasts, fintech comparison, filing ledger, and spending/scenario presentations.
- `workspace-flows.js`: bill dossier, explicit coalition selection/snapshots, persistent filters, and contact ledger.
- `desk-bridge.js`, `network-bridge.js`: narrow interfaces to the existing generation. The standard combined generator also emits these bridges.
- `workspace.css`: approved semantic colors, typography, responsive layout and accessible states.

## Device-local records

Engagement information extends `mdesk_manual_v1`: each office retains its existing fields, `tracker`, and `contacts`. Contact entries have a UUID, office/person, issue/bill, owner, status, date, due date, outcome, next step, source reference/type, and creation/update timestamps. Existing staff meeting flags remain inspectable.

Coalition snapshots reuse `mdesk_coal_snapshots_v1` and retain member names, bill scope, issue, party, committee, ranking basis, timestamp, and `Scenario` source type. Bill pins reuse `mdesk_bill_tracker_v1`. New save paths commit in memory only after browser storage succeeds; failed saves leave entered data available for retry. No shared persistence or synchronization is claimed.

## Data dependencies

Unavailable source fields render explicit unavailable states. These include comprehensive action denominators, institution coordinates, pair-level graph citations, some action dates, forecast horizons/bill-specific calibration, and recomputed alternative allocation outcomes. The allocation multiplier scales proposed inputs only. It does not invoke a model or manufacture an outcome plot.

The filing timeline is grouped by **filing date**, not contact date. Its 50-row pagination and quarter filters use the same records as the detailed source ledger. Action counts on Champions are deduplicated from retained canonical actions within one chamber.

## Review artifacts

See `outputs/approved-implementation-2026-09-21/IMPLEMENTATION-NOTES.md` for verification and remaining acceptance work. That directory contains contrast measurements, build hashes, original/standardized generation provenance, and a resumable Figma state ledger.
