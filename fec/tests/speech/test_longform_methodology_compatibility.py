"""Keep length adaptation compatible with the existing acoustic extraction."""

from pathlib import Path
import sys
from types import SimpleNamespace

from fec.speech import build_descriptive_speech as baseline
from fec.speech import build_member_longform_turn_features as longform
from fec.speech import verify_member_interview_sources as interviews


def test_same_six_feature_columns_as_existing_pipelines():
    assert longform.FEATURE_COLUMNS == baseline.FEATURE_COLUMNS
    assert longform.FEATURE_COLUMNS == interviews.FEATURE_COLUMNS
    assert len(longform.FEATURE_COLUMNS) == 6


def test_same_extractor_configuration_and_numeric_results(monkeypatch):
    configurations, measured_paths = [], []
    raw = {column: index + 0.1234567891
           for index, column in enumerate(baseline.FEATURE_COLUMNS.values())}

    class FakeSmile:
        def __init__(self, **kwargs):
            configurations.append(kwargs)

        def process_file(self, path):
            measured_paths.append(path)
            return SimpleNamespace(iloc={0: raw})

    monkeypatch.setitem(sys.modules, "opensmile", SimpleNamespace(
        __version__="test-only",
        FeatureSet=SimpleNamespace(eGeMAPSv02="eGeMAPSv02"),
        FeatureLevel=SimpleNamespace(Functionals="Functionals"),
        Smile=FakeSmile,
    ))
    path = Path("complete-multi-minute-answer.wav")
    old, new = baseline.measure_file(path, {}), longform.measure_file(path, {})
    assert old == new
    assert configurations == [
        {"feature_set": "eGeMAPSv02", "feature_level": "Functionals"},
        {"feature_set": "eGeMAPSv02", "feature_level": "Functionals"},
    ]
    assert measured_paths == [str(path), str(path)]
    assert new["pause"] == 0.12345679


def test_length_change_does_not_authorize_new_scores_or_weaker_evidence():
    contract = longform.CONTRACT
    assert contract["maximum_turn_seconds"] is None
    assert contract["identity_anchors_modified"] is False
    assert contract["automatic_trait_or_rank_effect"] is False
    assert contract["short_window_percentile_comparability_established"] is False
    assert contract["current_leader_baseline_requires_pre_current_role"] is True
    assert contract["recurrence_requires_two_independent_productions_and_settings"] is True
    assert contract["source_counts_alone_establish_recurrence"] is False
