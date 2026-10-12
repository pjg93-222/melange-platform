import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
path = ROOT / "fec/speech/materialize_leadership_prerole_reviewed_turns.py"
spec = importlib.util.spec_from_file_location("leadership_turn_materializer", path)
materializer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(materializer)


def test_selected_turn_requires_three_consecutive_accepted_windows():
    rows = [
        {"turn_index": 0, "window_index": 0, "accepted": True},
        {"turn_index": 0, "window_index": 1, "accepted": True},
        {"turn_index": 0, "window_index": 2, "accepted": False},
        {"turn_index": 0, "window_index": 3, "accepted": True},
        {"turn_index": 0, "window_index": 4, "accepted": True},
        {"turn_index": 0, "window_index": 5, "accepted": True},
        {"turn_index": 1, "window_index": 0, "accepted": True},
    ]
    assert materializer.maximum_consecutive_passes(rows, 0) == 3
    assert materializer.maximum_consecutive_passes(rows, 1) == 1


def test_human_review_must_match_exact_candidate_boundaries():
    candidate = {"turns": [{"start_sec": 10.0, "end_sec": 140.0}]}
    index, turn = materializer.matching_turn(
        candidate, {"turn_id": "turn-1", "start_sec": 10.0, "end_sec": 140.0}
    )
    assert index == 0
    assert turn["end_sec"] == 140.0
    with pytest.raises(materializer.PromotionRefusal, match="review_not_bound_to_candidate"):
        materializer.matching_turn(
            candidate, {"turn_id": "turn-1", "start_sec": 10.0, "end_sec": 139.0}
        )
