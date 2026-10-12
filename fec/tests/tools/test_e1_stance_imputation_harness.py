from __future__ import annotations

import ast
import csv
import json
import math
import subprocess
import sys
from pathlib import Path

from fec.tools import e1_stance_imputation_harness as harness


def _write_csv(path: Path, rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)


def _members(member_ids: tuple[str, ...], parties: dict[str, str] | None = None):
    return {
        member_id: harness.MemberRecord(
            member_id,
            (parties or {}).get(member_id, "A"),
            "HOUSE" if index % 2 else "SENATE",
            10 + index,
        )
        for index, member_id in enumerate(member_ids)
    }


def _data(
    values: dict[tuple[str, str], float | None],
    edges: dict[tuple[str, str], float] | None = None,
    parties: dict[str, str] | None = None,
) -> harness.HarnessData:
    member_ids = tuple(sorted({key[0] for key in values}))
    targets = tuple(sorted({key[1] for key in values}))
    complete = {
        (member_id, target): values.get((member_id, target))
        for member_id in member_ids
        for target in targets
    }
    return harness.HarnessData(
        member_ids,
        targets,
        complete,
        _members(member_ids, parties),
        edges or {},
    )


def _standard_files(tmp_path: Path) -> dict[str, Path]:
    targets = [f"T{index}" for index in range(5)]
    stance = tmp_path / "stance.csv"
    rows: list[list[object]] = [["member_id", *targets]]
    for member_index in range(10):
        rows.append(
            [
                f"M{member_index}",
                *[member_index + target_index / 10 for target_index in range(5)],
            ]
        )
    _write_csv(stance, rows)
    members = tmp_path / "members.csv"
    _write_csv(
        members,
        [["member_id", "party", "chamber", "coverage_count"]]
        + [
            [
                f"M{index}",
                "A" if index % 2 == 0 else "B",
                "HOUSE" if index % 3 else "SENATE",
                100 + index,
            ]
            for index in range(10)
        ],
    )
    edges = tmp_path / "edges.csv"
    _write_csv(
        edges,
        [["member_a", "member_b", "weight"]]
        + [[f"M{index}", f"M{(index + 1) % 10}", 1.0] for index in range(10)],
    )
    pflip = tmp_path / "pflip.csv"
    _write_csv(
        pflip,
        [["member_id", "target", "prediction"]]
        + [
            [f"M{member_index}", target, 100.0]
            for member_index in range(10)
            for target in targets
        ],
    )
    return {
        "edges": edges,
        "members": members,
        "pflip": pflip,
        "stance": stance,
    }


def _mask_cli_args(files: dict[str, Path], out: Path) -> list[str]:
    return [
        "--mask-recover",
        "--stance-matrix",
        str(files["stance"]),
        "--edges",
        str(files["edges"]),
        "--edge-weight",
        "1.0",
        "--members",
        str(files["members"]),
        "--pflip-predictions",
        str(files["pflip"]),
        "--out",
        str(out),
        "--seed",
        "1",
        "--seed",
        "2",
        "--seed",
        "3",
        "--allow-real-data",
    ]


def _comparison(
    name: str, imputer_mse: float, baseline_mse: float, denominator: int = 10
) -> harness.PairedComparison:
    reduction = (
        None
        if baseline_mse == 0.0
        else (baseline_mse - imputer_mse) / baseline_mse
    )
    return harness.PairedComparison(
        name,
        imputer_mse,
        baseline_mse,
        denominator,
        harness.MASK_WINDOW,
        reduction,
    )


def _write_battery_pflip(path: Path, seed: int, prediction: float = 100.0) -> int:
    planted, _, _ = harness.generate_battery_fixtures(seed)
    rows = [
        [member_id, target, prediction]
        for member_id in planted.member_ids
        for target in planted.targets
        if planted.values[(member_id, target)] is not None
    ]
    _write_csv(path, [["member_id", "target", "prediction"], *rows])
    return len(rows)


def test_look_without_lock_file_returns_look_refused_unlocked(tmp_path: Path):
    out = tmp_path / "out.json"
    code = harness.main(
        [
            "--look",
            "--allow-real-data",
            "--out",
            str(out),
            "--seed",
            "1",
            "--seed",
            "2",
            "--seed",
            "3",
        ]
    )
    assert code != 0
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == (
        "LOOK_REFUSED_UNLOCKED"
    )


def test_look_with_draft_lock_returns_look_refused_unlocked(tmp_path: Path):
    lock = tmp_path / "lock.md"
    lock.write_text("DRAFT -- NOT LOCKED\n", encoding="utf-8")
    out = tmp_path / "out.json"
    code = harness.main(
        [
            "--look",
            "--allow-real-data",
            "--lock-file",
            str(lock),
            "--out",
            str(out),
            "--seed",
            "1",
            "--seed",
            "2",
            "--seed",
            "3",
        ]
    )
    assert code != 0
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == (
        "LOOK_REFUSED_UNLOCKED"
    )


def test_look_with_locked_marker_and_draft_text_still_refuses(tmp_path: Path):
    lock = tmp_path / "lock.md"
    lock.write_text(
        "STATUS: **LOCKED**\nDRAFT -- NOT LOCKED\n", encoding="utf-8"
    )
    out = tmp_path / "out.json"
    code = harness.main(
        [
            "--look",
            "--allow-real-data",
            "--lock-file",
            str(lock),
            "--out",
            str(out),
            "--seed",
            "1",
            "--seed",
            "2",
            "--seed",
            "3",
        ]
    )
    assert code != 0
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == (
        "LOOK_REFUSED_UNLOCKED"
    )


def test_look_accepts_exact_markdown_locked_marker(tmp_path: Path):
    files = _standard_files(tmp_path)
    lock = tmp_path / "lock.md"
    lock.write_text("STATUS: **LOCKED**\n", encoding="utf-8")
    out = tmp_path / "out.json"
    args = _mask_cli_args(files, out)
    args[0] = "--look"
    args = args + ["--lock-file", str(lock)]
    assert harness.main(args) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["mode"] == "look"


def test_look_with_valid_lock_without_allow_real_data_returns_real_data_refused(
    tmp_path: Path,
):
    lock = tmp_path / "lock.md"
    lock.write_text("STATUS: **LOCKED**\n", encoding="utf-8")
    out = tmp_path / "out.json"
    code = harness.main(
        [
            "--look",
            "--lock-file",
            str(lock),
            "--out",
            str(out),
            "--seed",
            "1",
            "--seed",
            "2",
            "--seed",
            "3",
        ]
    )
    assert code != 0
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == (
        "REAL_DATA_REFUSED"
    )


def test_mask_recover_without_allow_real_data_returns_real_data_refused(
    tmp_path: Path,
):
    out = tmp_path / "out.json"
    code = harness.main(
        [
            "--mask-recover",
            "--out",
            str(out),
            "--seed",
            "1",
            "--seed",
            "2",
            "--seed",
            "3",
        ]
    )
    assert code != 0
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == (
        "REAL_DATA_REFUSED"
    )


def test_mask_recover_requires_exactly_three_seeds(tmp_path: Path):
    out = tmp_path / "out.json"
    code = harness.main(
        [
            "--mask-recover",
            "--allow-real-data",
            "--out",
            str(out),
            "--seed",
            "1",
        ]
    )
    assert code != 0
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == (
        "INVALID_ARGUMENTS"
    )


def test_battery_requires_exactly_one_seed(tmp_path: Path):
    out = tmp_path / "out.json"
    code = harness.main(
        [
            "--battery",
            "--out",
            str(out),
            "--seed",
            "1",
            "--seed",
            "2",
        ]
    )
    assert code != 0
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == (
        "INVALID_ARGUMENTS"
    )


def test_layer_edges_are_summed_with_explicit_multipliers(tmp_path: Path):
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    rows = [["member_a", "member_b", "weight"], ["B", "A", 2.0]]
    _write_csv(first, rows)
    _write_csv(second, [["member_a", "member_b", "weight"], ["A", "B", 4.0]])
    assert harness.read_layered_edges([first, second], [3.0, 0.5]) == {
        ("A", "B"): 8.0
    }


def test_mask_selects_exactly_twenty_percent_of_observed_cells_only():
    values = {
        (f"M{member}", f"T{target}"): (
            None if target == 4 else float(member + target)
        )
        for member in range(5)
        for target in range(5)
    }
    data = _data(values)
    mask = harness.select_mask(data, 0.20, 11)
    assert len(harness.observed_cells(data)) == 20
    assert len(mask) == 4
    assert all(data.values[cell] is not None for cell in mask)


def test_mask_same_seed_is_reproducible():
    data = _data(
        {
            (f"M{member}", f"T{target}"): float(member + target)
            for member in range(5)
            for target in range(5)
        }
    )
    assert harness.select_mask(data, 0.20, 7) == harness.select_mask(
        data, 0.20, 7
    )


def test_mask_different_seed_changes_selection():
    data = _data(
        {
            (f"M{member}", f"T{target}"): float(member + target)
            for member in range(5)
            for target in range(5)
        }
    )
    assert harness.select_mask(data, 0.20, 7) != harness.select_mask(
        data, 0.20, 8
    )


def test_global_mean_baseline_uses_training_cells_only():
    data = _data(
        {("M1", "T1"): 1.0, ("M1", "T2"): 3.0, ("M2", "T1"): 5.0, ("M2", "T2"): 7.0}
    )
    assert harness.global_mean_prediction(data, [("M1", "T1")]) == 5.0


def test_party_mean_baseline_and_global_fallback_match_hand_calculation():
    data = _data(
        {
            ("M1", "T1"): 2.0,
            ("M1", "T2"): 8.0,
            ("M2", "T1"): 4.0,
            ("M2", "T2"): None,
            ("M3", "T1"): 10.0,
            ("M3", "T2"): 6.0,
        },
        parties={"M1": "A", "M2": "A", "M3": "B"},
    )
    predictions = harness.party_mean_predictions(
        data, [("M1", "T1"), ("M1", "T2")]
    )
    assert predictions[("M1", "T1")] == 4.0
    assert math.isclose(predictions[("M1", "T2")], 20.0 / 3.0)


def test_additive_marginal_baseline_and_empty_fallback_match_hand_calculation():
    data = _data(
        {
            ("M1", "T1"): 2.0,
            ("M1", "T2"): 4.0,
            ("M2", "T1"): 6.0,
            ("M2", "T2"): 8.0,
            ("M3", "T1"): 10.0,
            ("M3", "T2"): None,
        }
    )
    predictions = harness.additive_marginal_predictions(
        data, [("M1", "T1"), ("M3", "T1")]
    )
    assert predictions[("M1", "T1")] == 4.0
    assert predictions[("M3", "T1")] == 6.0


def test_pflip_baseline_restricts_to_mask_and_counts_missing_predictions():
    mask = [("M1", "T1"), ("M2", "T1")]
    restricted, excluded = harness.pflip_predictions_for_mask(
        mask, {("M1", "T1"): 3.0, ("M9", "T9"): 4.0}
    )
    assert restricted == {("M1", "T1"): 3.0}
    assert excluded == 1


def test_knn_prediction_matches_hand_computed_weighted_mean():
    values = {
        (member, target): value
        for member, row in {
            "M": [9.0, 0.0, 0.0, 0.0],
            "N1": [2.0, 0.0, 0.0, 0.0],
            "N2": [8.0, 1.0, 1.0, 1.0],
        }.items()
        for target, value in zip(("T0", "T1", "T2", "T3"), row)
    }
    data = _data(values, {("M", "N1"): 2.0, ("M", "N2"): 1.0})
    prediction, fallback = harness.knn_prediction(
        data, ("M", "T0"), [("M", "T0")], 99.0, 10
    )
    assert math.isclose(prediction, 3.2)
    assert fallback is False


def test_knn_excludes_neighbor_with_fewer_than_three_coobserved_cells():
    data = _data(
        {
            ("M", "T0"): 9.0,
            ("M", "T1"): 0.0,
            ("M", "T2"): 0.0,
            ("M", "T3"): 0.0,
            ("N", "T0"): 2.0,
            ("N", "T1"): 0.0,
            ("N", "T2"): 0.0,
            ("N", "T3"): None,
        },
        {("M", "N"): 1.0},
    )
    prediction, fallback = harness.knn_prediction(
        data, ("M", "T0"), [("M", "T0")], 4.5, 10
    )
    assert prediction == 4.5
    assert fallback is True


def test_knn_similarity_excludes_target_and_all_masked_cells():
    values = {}
    for target, left, right in (
        ("T0", 100.0, -100.0),
        ("T1", 50.0, -50.0),
        ("T2", 0.0, 1.0),
        ("T3", 0.0, 1.0),
        ("T4", 0.0, 1.0),
    ):
        values[("A", target)] = left
        values[("B", target)] = right
    result = harness.rmsd_excluding_target_and_mask(
        _data(values), "A", "B", "T0", [("A", "T1")]
    )
    assert result is not None
    assert result[1] == 3
    assert result[0] == 1.0


def test_knn_uses_global_fallback_when_no_neighbor_is_eligible():
    data = _data(
        {
            ("M1", "T1"): 1.0,
            ("M1", "T2"): 2.0,
            ("M2", "T1"): 3.0,
            ("M2", "T2"): 4.0,
        }
    )
    prediction, fallback = harness.knn_prediction(
        data, ("M1", "T1"), [("M1", "T1")], 3.0, 10
    )
    assert prediction == 3.0
    assert fallback is True


def test_coverage_terciles_break_ties_by_member_id():
    member_ids = ("M6", "M5", "M4", "M3", "M2", "M1")
    values = {(member_id, "T"): 1.0 for member_id in member_ids}
    records = {
        member_id: harness.MemberRecord(member_id, "A", "HOUSE", 10)
        for member_id in member_ids
    }
    data = harness.HarnessData(member_ids, ("T",), values, records, {})
    terciles = harness.assign_coverage_terciles(data)
    assert {member for member, label in terciles.items() if label == "LOW"} == {
        "M1",
        "M2",
    }
    assert {member for member, label in terciles.items() if label == "MIDDLE"} == {
        "M3",
        "M4",
    }
    assert {member for member, label in terciles.items() if label == "HIGH"} == {
        "M5",
        "M6",
    }


def test_pflip_comparison_uses_identical_available_mask_subset():
    first = ("M1", "T")
    second = ("M2", "T")
    comparison = harness.paired_comparison(
        "pflip",
        {first: 0.0, second: 10.0},
        {first: 99.0, second: 8.0},
        {second: 7.0},
        [first, second],
        harness.PFLIP_WINDOW,
    )
    assert comparison.denominator == 1
    assert comparison.imputer_mse == 4.0
    assert comparison.baseline_mse == 9.0


def test_stability_is_stable_pass_when_every_seed_beats_all_four():
    assert harness.stability_verdict(
        [{"imputer_beats_all_four": True}] * 3
    ) == "STABLE_PASS"


def test_stability_is_fragile_when_one_seed_flips():
    assert harness.stability_verdict(
        [
            {"imputer_beats_all_four": True},
            {"imputer_beats_all_four": False},
            {"imputer_beats_all_four": True},
        ]
    ) == "FRAGILE"


def test_stability_is_fail_when_no_seed_beats_all_four():
    assert harness.stability_verdict(
        [{"imputer_beats_all_four": False}] * 3
    ) == "FAIL"


def test_evaluate_planted_probe_pass_and_fail_directions():
    passing = {name: _comparison(name, 1.0, 2.0) for name in harness.BASELINE_NAMES}
    assert harness.evaluate_planted_probe(passing).passed is True
    failing = dict(passing)
    failing["pflip"] = _comparison("pflip", 1.0, 0.5)
    result = harness.evaluate_planted_probe(failing)
    assert result.passed is False
    assert result.verdict == "PLANTED_FAIL"


def test_evaluate_planted_probe_fails_when_one_baseline_is_exactly_tied():
    comparisons = {
        name: _comparison(name, 1.0, 2.0) for name in harness.BASELINE_NAMES
    }
    comparisons["global_mean"] = _comparison("global_mean", 1.0, 1.0)
    result = harness.evaluate_planted_probe(comparisons)
    assert result.passed is False
    assert result.verdict == "PLANTED_FAIL"


def test_evaluate_null_probe_pass_and_fail_directions():
    passing = harness.evaluate_null_probe(_comparison("global_mean", 0.98, 1.0))
    failing = harness.evaluate_null_probe(_comparison("global_mean", 0.97, 1.0))
    assert passing.passed is True
    assert failing.passed is False
    assert failing.verdict == "NULL_FAIL"


def test_evaluate_confound_probe_pass_and_fail_directions():
    passing = harness.evaluate_confound_probe(_comparison("party_mean", 1.2, 1.0))
    failing = harness.evaluate_confound_probe(_comparison("party_mean", 0.5, 1.0))
    assert passing.passed is True
    assert failing.passed is False
    assert failing.verdict == "CONFOUND_FAIL"


def test_battery_pass_path_with_generated_well_formed_fixture(tmp_path: Path):
    seed = 23
    pflip = tmp_path / "battery-pflip.csv"
    _write_battery_pflip(pflip, seed)
    out = tmp_path / "battery.json"
    code = harness.main(
        [
            "--battery",
            "--pflip-predictions",
            str(pflip),
            "--out",
            str(out),
            "--seed",
            str(seed),
        ]
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0
    assert report["verdict"] == "BATTERY_PASS"
    assert [probe["name"] for probe in report["probes"]] == [
        "PLANTED",
        "NULL",
        "CONFOUND",
    ]
    assert [probe["verdict"] for probe in report["probes"]] == [
        "PLANTED_PASS",
        "NULL_PASS",
        "CONFOUND_PASS",
    ]
    parameters = report["generator_parameters"]
    observation = parameters["observation_pattern"]
    assert observation["network_independent"] is True
    assert (
        observation[
            "expected_coobserved_cells_per_random_member_pair_theoretical"
        ]
        > 3.0
    )
    assert parameters["grid_dimensions"]["planted"] == {
        "members": 40,
        "targets": 240,
    }
    assert parameters["network_generation"]["configured_degree"] == 2
    assert parameters["latent_and_loading"]["smoothing"] == (
        "unweighted_mean_of_self_and_true_edge_neighbors"
    )
    assert parameters["swap_counts"] == {
        "attempted": 400,
        "attempts_per_edge": 10,
    }


def test_battery_refused_path_names_controlled_failed_probe():
    passing_planted = harness.evaluate_planted_probe(
        {name: _comparison(name, 1.0, 2.0) for name in harness.BASELINE_NAMES}
    )
    failing_null = harness.evaluate_null_probe(
        _comparison("global_mean", 0.5, 1.0)
    )
    passing_confound = harness.evaluate_confound_probe(
        _comparison("party_mean", 1.0, 1.0)
    )
    verdict, failing = harness.aggregate_battery_results(
        [passing_planted, failing_null, passing_confound]
    )
    assert verdict == "BATTERY_REFUSED"
    assert failing == ("NULL",)


def test_battery_aggregate_refuses_empty_probe_composition():
    verdict, failing = harness.aggregate_battery_results([])
    assert verdict == "BATTERY_REFUSED"
    assert "COMPOSITION_EMPTY" in failing
    assert "COMPOSITION_MISSING:PLANTED" in failing
    assert "COMPOSITION_MISSING:NULL" in failing
    assert "COMPOSITION_MISSING:CONFOUND" in failing


def test_battery_aggregate_refuses_missing_probe_composition():
    planted = harness.evaluate_planted_probe(
        {name: _comparison(name, 1.0, 2.0) for name in harness.BASELINE_NAMES}
    )
    null = harness.evaluate_null_probe(_comparison("global_mean", 1.0, 1.0))
    verdict, failing = harness.aggregate_battery_results([planted, null])
    assert verdict == "BATTERY_REFUSED"
    assert failing == ("COMPOSITION_MISSING:CONFOUND",)


def test_battery_aggregate_refuses_duplicated_probe_composition():
    planted = harness.evaluate_planted_probe(
        {name: _comparison(name, 1.0, 2.0) for name in harness.BASELINE_NAMES}
    )
    null = harness.evaluate_null_probe(_comparison("global_mean", 1.0, 1.0))
    confound = harness.evaluate_confound_probe(
        _comparison("party_mean", 1.0, 1.0)
    )
    verdict, failing = harness.aggregate_battery_results(
        [planted, planted, null, confound]
    )
    assert verdict == "BATTERY_REFUSED"
    assert failing == ("COMPOSITION_DUPLICATE:PLANTED",)


def test_report_records_per_cell_fallbacks_and_pflip_exclusions():
    data = _data(
        {
            (member, target): float(member_index + target_index)
            for member_index, member in enumerate(("M1", "M2"))
            for target_index, target in enumerate(("T0", "T1", "T2", "T3", "T4"))
        }
    )
    result = harness.evaluate_mask(data, [("M1", "T0")], {}, 10)
    assert result["fallback_count"] == 1
    assert result["pflip_exclusion_count"] == 1
    assert result["masked_cells"][0]["imputer"]["fallback"] is True
    assert result["masked_cells"][0]["pflip"]["excluded"] is True


def test_report_metrics_have_d007_windows_and_denominators():
    data = _data(
        {
            (member, target): float(member_index + target_index)
            for member_index, member in enumerate(("M1", "M2"))
            for target_index, target in enumerate(("T0", "T1", "T2", "T3", "T4"))
        }
    )
    result = harness.evaluate_mask(
        data, [("M1", "T0")], {("M1", "T0"): 0.0}, 10
    )
    metrics = list(result["pooled_mse"].values())
    for comparison in result["comparisons"].values():
        metrics.extend(
            [
                comparison["baseline_mse"],
                comparison["imputer_mse"],
                comparison["relative_reduction"],
            ]
        )
    for row in result["strata"]:
        metrics.extend(row["methods"].values())
    assert metrics
    assert all("window" in metric and "denominator" in metric for metric in metrics)


def test_report_never_masks_or_replaces_dash_cells():
    data = _data(
        {
            (f"M{member}", f"T{target}"): (
                None if target == 4 else float(member + target)
            )
            for member in range(5)
            for target in range(5)
        }
    )
    mask = harness.select_mask(data, 0.20, 19)
    missing = {key for key, value in data.values.items() if value is None}
    result = harness.evaluate_mask(data, mask, {}, 10)
    reported = {
        (cell["member_id"], cell["target"]) for cell in result["masked_cells"]
    }
    assert missing.isdisjoint(mask)
    assert missing.isdisjoint(reported)
    assert all(data.values[key] is None for key in missing)


def test_literal_dash_cells_are_excluded_end_to_end(tmp_path: Path):
    targets = ["T0", "T1", "T2", "T3", "T4"]
    matrix_rows: list[list[object]] = [
        ["M0", "-", 1, 2, 3, 4],
        ["M1", 5, "-", 6, 7, 8],
        ["M2", 9, 10, "-", 11, 12],
        ["M3", 13, 14, 15, "-", 16],
        ["M4", 17, 18, 19, 20, "-"],
    ]
    stance = tmp_path / "dash-stance.csv"
    _write_csv(stance, [["member_id", *targets], *matrix_rows])
    members = tmp_path / "dash-members.csv"
    _write_csv(
        members,
        [["member_id", "party", "chamber", "coverage_count"]]
        + [
            [f"M{index}", "A" if index % 2 == 0 else "B", "HOUSE", 10 + index]
            for index in range(5)
        ],
    )
    edges = tmp_path / "dash-edges.csv"
    _write_csv(
        edges,
        [["member_a", "member_b", "weight"]]
        + [[f"M{index}", f"M{(index + 1) % 5}", 1.0] for index in range(5)],
    )
    observed: dict[tuple[str, str], float] = {}
    dash_keys: set[tuple[str, str]] = set()
    for row in matrix_rows:
        member_id = str(row[0])
        for target, raw in zip(targets, row[1:]):
            if raw == "-":
                dash_keys.add((member_id, target))
            else:
                observed[(member_id, target)] = float(raw)
    pflip = tmp_path / "dash-pflip.csv"
    _write_csv(
        pflip,
        [["member_id", "target", "prediction"]]
        + [[member, target, 0.0] for member, target in sorted(observed)],
    )
    out = tmp_path / "dash-report.json"
    code = harness.main(
        [
            "--mask-recover",
            "--allow-real-data",
            "--stance-matrix",
            str(stance),
            "--edges",
            str(edges),
            "--edge-weight",
            "1.0",
            "--members",
            str(members),
            "--pflip-predictions",
            str(pflip),
            "--out",
            str(out),
            "--seed",
            "1",
            "--seed",
            "2",
            "--seed",
            "3",
        ]
    )
    assert code == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    observed_total = sum(observed.values())
    for result in report["results"]:
        cells = result["masked_cells"]
        masked_keys = {(cell["member_id"], cell["target"]) for cell in cells}
        assert len(masked_keys) == 4
        assert dash_keys.isdisjoint(masked_keys)
        expected_global_mean = (
            observed_total - sum(cell["actual"] for cell in cells)
        ) / 16
        assert all(
            math.isclose(cell["global_mean"], expected_global_mean)
            for cell in cells
        )
    reported_keys = {
        (cell["member_id"], cell["target"])
        for result in report["results"]
        for cell in result["masked_cells"]
    }
    assert dash_keys.isdisjoint(reported_keys)


def test_identical_cli_invocations_write_byte_identical_json(tmp_path: Path):
    files = _standard_files(tmp_path)
    out = tmp_path / "report.json"
    args = _mask_cli_args(files, out)
    worktree_root = Path(__file__).resolve().parents[3]
    command = [
        sys.executable,
        "-m",
        "fec.tools.e1_stance_imputation_harness",
        *args,
    ]
    first_run = subprocess.run(
        command,
        cwd=worktree_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert first_run.returncode == 0, first_run.stderr
    first = out.read_bytes()
    second_run = subprocess.run(
        command,
        cwd=worktree_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert second_run.returncode == 0, second_run.stderr
    second = out.read_bytes()
    assert first == second


def test_battery_requires_and_records_supplied_pflip_path_and_row_count(
    tmp_path: Path,
):
    refused_out = tmp_path / "refused.json"
    refused_code = harness.main(
        [
            "--battery",
            "--out",
            str(refused_out),
            "--seed",
            "31",
        ]
    )
    assert refused_code != 0
    assert json.loads(refused_out.read_text(encoding="utf-8"))["verdict"] == (
        "INVALID_ARGUMENTS"
    )

    seed = 31
    pflip_path = tmp_path / "pinned-pflip.csv"
    expected_rows = _write_battery_pflip(pflip_path, seed, prediction=50.0)
    predictions, row_count = harness.read_pflip_predictions(pflip_path)
    report = harness.build_battery_report(
        seed=seed,
        invocation=["synthetic-battery"],
        allow_real_data=False,
        pflip_predictions=predictions,
        pflip_path=pflip_path,
        pflip_row_count=row_count,
        mask_fraction=0.20,
        knn_k=10,
    )
    pflip_input = report["inputs"]["pflip_predictions"]
    assert pflip_input == {"path": str(pflip_path), "row_count": expected_rows}
    planted = report["probes"][0]
    pflip_comparison = planted["numbers"]["comparisons"]["pflip"]
    assert pflip_comparison["baseline_mse"]["denominator"] > 0
    assert pflip_comparison["baseline_mse"]["value"] is not None


def test_harness_imports_match_pinned_stdlib_allowlist_and_exclude_fec():
    worktree_root = Path(__file__).resolve().parents[3]
    harness_path = worktree_root / "fec" / "tools" / "e1_stance_imputation_harness.py"
    tree = ast.parse(harness_path.read_text(encoding="utf-8"), filename=str(harness_path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".", 1)[0])
    allowed = {
        "__future__",
        "argparse",
        "csv",
        "dataclasses",
        "json",
        "math",
        "pathlib",
        "random",
        "sys",
        "typing",
    }
    assert imported == allowed
    assert all(not name.startswith("fec") for name in imported)
