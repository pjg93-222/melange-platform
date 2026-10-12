# Partisan-dollar forecasting

These tools measure House/Senate Democratic and Republican **dollars**, separately from the earlier partisan-share experiment. The original interval and whole-industry tools remain offline. The separately versioned political extension also uses a pure application predictor and can publish its comparison into the existing static forecast view. It never restarts the app, collects data, or replaces the existing company/PAC model pointers.

- `evaluate.py` evaluates six whole-industry nominal-dollar history models with chronological model selection. Its input is the preserved four-industry combined individual/PAC history, not company or selected-PAC giving.
- `verify_results.py` independently reconstructs all whole-industry predictions and metrics without importing that evaluator.
- `pac_nowcast.py` evaluates six fixed reporting-date PAC nowcast rules on five pinned PAC cohorts. It requires complete classified input, rejects unknown recipients and missing history, and keeps historical selection truth behind the issue-date boundary.
- `bounded_pac.py` implements a separate three-rule uncertainty-aware experiment. Ambiguous recipient amounts contribute conservative intervals; it reports whether the complete prediction/outcome interval is within a stated dollar tolerance. Non-certification is not an observed miss. It never substitutes midpoint scores for unknown truth.
- `predict_interval.py` stages a standalone callable version of all three interval rules, including future even-year feature frames. All 960 frozen predictions reproduce exactly; no actual 2026 forecast or live activation is included.
- `political_features.py` retains the original strict as-of feature contract: dated champion evidence and a coherent historical ideology release are required. It does not accept revised historical releases as original-vintage data.
- `fit_political.py` and `evaluate_political.py` implement a separate, explicitly reconstructed retrospective experiment using prior-cycle bill attention and top-25 attention members' average DW-NOMINATE. Training-only centering, equal election/cohort/chamber weighting, a fixed ridge penalty, and chronological fits prevent target outcomes entering the fitted coefficients. The dollar transfer preserves the baseline total and is constrained by remaining giving. Full source-classification intervals are retained when scoring.
- `build_political_bundle.py` combines frozen coefficients with independently prepared current 2026 frames, records input hashes, and publishes a separate catalog to the whitelisted `pac-giving-forecast.js` asset. The original published total forecasts remain unchanged. Current signed champion-board means can be used in an explicitly unvalidated sensitivity scenario; they are not mislabeled as historical proxy inputs.

Run the synthetic controls:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s mentat/forecast/partisan_dollars -p 'test_*.py' -v
```

Every evaluation requires a new output directory and a hash of its separately saved protocol. Existing runs must not be overwritten. Model families, source adjudication and uncertainty rules are recorded before real PAC scoring. The retained failed/held data builds are evidence, not disposable intermediates.

The 2026-10-05/06 whole-industry experiment did not beat prior-cycle persistence. Reporting-date PAC results have a different population. Their latest amended historical files, later fixed cohorts and retrospective classification prevent an original-vintage backtest claim. All historical outcomes have appeared elsewhere in the project; none is a fresh holdout. A low WAPE is not an accuracy probability, and a favorable retrospective result is not publication approval.

Protocols, final explanations and charts belong in the configured report directory under `MENTAT_PARTISAN_REFINEMENT_ROUND2_2026-10-05`. Internal immutable inputs and runs are under `%LOCALAPPDATA%/Mentat/forecast/partisan-refinement-round2-20261005`.

The political extension has a separate report folder, `MENTAT_PARTISAN_POLITICAL_MODEL_2026-10-06`, and runtime `%LOCALAPPDATA%/Mentat/forecast/political-model-20261006`. It did **not** improve historical accuracy: full-grid within-20% certification fell from 279/320 to 260/320; on the same 96 cases with fitted political inputs it fell from 82/96 to 63/96. Early warm-up origins and unsupported cohorts retain the baseline. These are previously examined outcomes, revised DW scores and reconstructed sources, not a fresh holdout or original-vintage validation. The current-champion sensitivity has no historical accuracy estimate.
