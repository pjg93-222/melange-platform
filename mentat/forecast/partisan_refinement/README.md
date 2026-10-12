# Offline partisan-share refinement

This directory evaluates and stages alternatives to the published House/Senate
two-party giving-share model. It does not publish a model, change the live v2
bundle, restart Mentat, or import the application.

`evaluate.py` compares five fixed candidates with nested chronological selection.
It records every forecast and freezes the final chamber choices in a hash-bound
manifest. Development accepts only the balanced four-industry 2012–2022 share
history. Its separate `--holdout` mode is **reserved historical confirmation**:
the project's 2024 outcomes were already used elsewhere, so this is not a fresh
holdout. It accepts only 2024 rows and cannot select another model.

`verify_results.py` independently derives candidate predictions and summary
errors using only the standard library, and checks the incumbent's chronological
MAE against the preserved R-pipeline summary. It does not import the evaluator.

Run the synthetic controls with:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s mentat/forecast/partisan_refinement -p 'test_*.py' -v
```

Evaluation commands (use new output directories, never overwrite an old run):

```text
python evaluate.py --development --bundle ACTIVE_BUNDLE.json --output NEW_DEVELOPMENT_DIRECTORY
python verify_results.py --evaluation NEW_DEVELOPMENT_DIRECTORY/evaluation.json --bundle ACTIVE_BUNDLE.json --published-backtests FROZEN_BACKTESTS.csv --output NEW_VERIFICATION.json
python evaluate.py --holdout --bundle ACTIVE_BUNDLE.json --holdout-input RESERVED_2024.json --policy-manifest NEW_DEVELOPMENT_DIRECTORY/policy_manifest.json --policy-sha256 EXACT_SAVED_SHA256 --output NEW_CONFIRMATION_DIRECTORY
```

The October 5 experiment selected House persistence and Senate equal blending of
the prior-cycle and four-years-earlier shares. The independent chronological
checks passed, but publication is held: House's nested selector did not beat
persistence; House failed all four existing G6 leave-one-election-out cells; and
Senate's strict four-year anchor leaves the 2014 G6 fold unavailable. The Senate
2024 result also lost to persistence and worsened credit-union error.

`predict.py` supplies a separate, honest baseline-estimator package for offline
review. It must not be disguised as the old OLS estimator or inherit its p-values
and intervals. A ready point calculation is not publication approval or evidence
of company/employee/PAC-specific accuracy. The staged package records these
holds and cannot enable live activation.

The complete protocol, result tables, provenance, row-level predictions, and
review package are under the configured report directory in
`MENTAT_PARTISAN_REFINEMENT_2026-10-05`. Internal immutable inputs and runs are
under `%LOCALAPPDATA%/Mentat/forecast/partisan-refinement-20261005`.
