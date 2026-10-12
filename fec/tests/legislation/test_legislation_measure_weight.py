from __future__ import annotations

from pathlib import Path
import sys


FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from legislation_measure_key import measure_weight  # noqa: E402


def test_original_method_measure_weights() -> None:
    assert measure_weight("119:hr:1") == 1.0
    assert measure_weight("119:sres:1") == 1.0
    assert measure_weight("119:hamdt:1") == 0.5
    assert measure_weight("119:samdt:1") == 0.5
