"""Run the unchanged say/do producer after refreshed downstream artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import ROOT as MB_ROOT  # noqa: E402 - required root-aware contract

from legislation.run_legislation_say_do_first_pass import (
    SayDoError,
    run_pass,
    validate_run_dir,
    validate_shadow_environment,
)


SECOND_SEQUENCE_INPUTS = (
    "category_stance.json",
    "data/engine/pillar_inputs_snapshot.json",
    "ranks_python.json",
    "dashboard_data.json",
    "intermediate/pillar_inputs_first_pass.json",
    "intermediate/ranks_first_pass.json",
    "intermediate/network_dependents_first_pass.receipt.json",
    "intermediate/dashboard_member_desk_first_pass.receipt.json",
)
SECOND_RECEIPT = Path("intermediate") / "say_do_second_pass.receipt.json"


def main() -> int:
    try:
        run_dir = validate_run_dir(Path(MB_ROOT))
        validate_shadow_environment(run_dir)
        result = run_pass(
            pass_name="second",
            run_dir=run_dir,
            sequence_inputs=SECOND_SEQUENCE_INPUTS,
            receipt_relative=SECOND_RECEIPT,
            copy_relative=None,
            require_first_receipt=True,
            wrapper_path=Path(__file__),
        )
    except (SayDoError, OSError) as exc:
        print(f"legislation say/do second pass: FAIL CLOSED: {exc}")
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
