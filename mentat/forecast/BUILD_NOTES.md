# Giving Forecast build branch

Branch `claude/forecast-build-20260930`, cut from `codex/mentat-crypto` at `7287dbb50`
("Record the Giving Forecast and the Insights forecast fixes"). Sparse checkout: `mentat/` only.

Owner-approved phases (2026-09-30):

1. R refit pipeline under `mentat/forecast/refit/` that rebuilds the model panels from raw
   sources and reproduces `mentat/app/data/giving-models-v1.json` exactly before any change.
2. Bundle v2: slope rules selected by at least 5% better out-of-sample accuracy in both
   leave-one-election-out and chronological designs (D-065; supersedes the initial
   DerSimonian-Laird plan); pooled slopes remain the default; previous-cycle PAC anchor; January-June 2026
   expected-control input; remainder accepts coverage on or before the as-of date; a
   prediction interval beside the coefficient interval; seats removed as a signal (seat
   regressions stay as evidence). Ordinary p-values stay primary. v1 is kept for rollback.
3. Console: the naive Insights "Forecasts" card hidden behind a setting; Chart Studio's
   transform labelled "Same-phase baseline". (Built separately against the live checkout,
   because two of its files carry another lane's uncommitted work.)
4. Sector Growth Study backtest items I-K (the study is untracked; built separately).
5. New-industry refit agent: triggered by Mentat export receipts (read-only), history from
   OpenSecrets + IBISWorld (IBISWorld supplied by Paul), auto-publish when gates pass,
   every step emitted to Glass Box.
6. Pilot: property & casualty insurance.

Landing target: `codex/mentat-crypto`, preserving unrelated working-copy edits.

## RunPod completion record — 2026-09-30

The current build was run in pod `d3e3e3ac8c64`, isolated under
`/workspace/forecast-audit-5603f2`. All model, ingest, test, and browser processes ran there.
The transfer included 121 hash-pinned model inputs and the existing aggregate anchor snapshot;
donor-level indexes stayed on the workstation.

The P&C pilot now uses the approved F09 all-insurance baseline with an explicit scope caveat.
Its 2024 revenue is the 2024-11 IBISWorld current-year estimate from the same vintage;
the estimate is displaced when a newer complete history includes that year. Share anchors
are chosen per industry and chamber, with a 5% win required in both validation designs.
The first completed run, `pod-final-20260930`, correctly held publication: House-share
LOEO MAE increased for commercial banks (1.053 to 1.550), credit unions (1.522 to 1.836),
and auto (1.338 to 1.766). Paul then explicitly authorized: "Waive these three cells and publish".
Those three visible warnings join the existing quiet banks-lobbying waiver in
`refit/registry/gate_waivers.json`; every other G6 cell retains the no-degradation rule.

Run `pod-publish-20260930` first published after that authorization. The final corrected run,
`pod-publish-final-20260930`, published at `2026-09-30T21:53:03Z`.
Active version: `refit-20260930-377f7324-c3f221343515`.
Bundle SHA256: `7bc7bcf175f7a3c291cc67300f981d610ef7b52059eefb7cb21e2dc4e020e0bb`.
The final numerical outputs are exactly identical to the authorized publication (5,052 numeric
leaves checked); its fixes preserve the publisher environment, persistent rollback archive,
and accurate previous-model lineage. The original HOLD and waived-publication receipts remain.
G4 and G9 retain their warnings; no thresholds were relaxed.

Publication now stores immutable bundle versions, validates the selected pointer's hash and
identity in both R and Python, and supports tested v1 rollback. A failed pointer switch leaves
the active bundle unchanged. Model version identities include the input vintage and the
build's run, time, and producer-code hash.

Local console restart and Windows task registration are not executed by this pod-only session.
The Phase3 UI and SGS commits are included in the landing history. Existing peer edits
remain in the working copy and are excluded from the forecast commit.
