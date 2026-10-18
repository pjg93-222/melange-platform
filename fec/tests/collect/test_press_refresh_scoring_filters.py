# -*- coding: utf-8 -*-
"""Run the private R fixture for refresh_press_delta.R's scoring filters (owner ruling D-219, 2026-10-15)."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from collect import refresh_press_stream as press


def test_undated_and_title_fallback_rows_reach_the_scorer_but_pending_and_mentions_never_do():
    try:
        rscript = press.find_rscript()
    except RuntimeError:
        pytest.skip("Rscript is not installed")
    result = subprocess.run(
        [str(rscript), str(Path(__file__).with_suffix(".R")), str(FEC / "collect/refresh_press_delta.R")],
        capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout


def test_the_driver_scores_through_the_scorable_helper_only():
    source = (FEC / "collect/refresh_press_delta.R").read_text(encoding="utf-8")
    assert source.count("score_scorable_press_rows(materialized$scorable)") == 3
    assert "scoring_dated" not in source
    assert 'materialize_press_outputs(full, "after_network_retry")' in source
