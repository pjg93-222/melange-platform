from pathlib import Path
import sys


FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))

from engine.pillar_policy import (
    LABEL_EVIDENCE_WEIGHTS,
    PILLAR_WEIGHTS,
    scoring_policy,
    serialized_weights,
)


def test_press_sentiment_is_active_but_diminished():
    assert PILLAR_WEIGHTS == {2: 0.45, 3: 0.10, 4: 0.40, 5: 0.05}
    assert sum(PILLAR_WEIGHTS.values()) == 1.0
    assert serialized_weights() == (
        ("2", 0.45), ("3", 0.1), ("4", 0.4), ("5", 0.05)
    )
    assert scoring_policy()["press_sentiment"] == "active_diminished"
    assert scoring_policy()["label_media_weight"] == 0.10


def test_press_label_weight_is_diminished_in_every_category_class():
    assert set(LABEL_EVIDENCE_WEIGHTS) == {"standard", "defensive"}
    assert all(
        weights["media"] == 0.10
        and weights["media"] < weights["legislation"]
        for weights in LABEL_EVIDENCE_WEIGHTS.values()
    )
