"""Synthetic-first mask-and-recover harness for E1 stance imputation.

The module is deliberately standalone: it imports no FEC engine code and writes
only the report path explicitly supplied by the caller.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


SCHEMA_ID = "e1-harness-report-v1"
BASELINE_NAMES = ("global_mean", "party_mean", "additive_marginal", "pflip")
LOCKED_MARKER = "STATUS: **LOCKED**"
DRAFT_MARKER = "DRAFT -- NOT LOCKED"
MASK_WINDOW = "masked_observed_cells"
PFLIP_WINDOW = "masked_observed_cells_with_pflip_prediction"

BATTERY_MEMBER_COUNT = 40
BATTERY_TARGET_COUNT = 240
BATTERY_OBSERVATION_PROBABILITY = 0.35
BATTERY_LATENT_STDDEV = 1.0
BATTERY_LOADING_RANGE = (0.80, 1.20)
BATTERY_PLANTED_NOISE_SIGMA = 0.05
BATTERY_CONFOUND_NOISE_SIGMA = 0.08
BATTERY_NULL_SWAP_ATTEMPTS_PER_EDGE = 10
BATTERY_CYCLE_WEIGHT_RANGE = (0.75, 1.25)
BATTERY_NETWORK_DEGREE = 2

CellKey = tuple[str, str]


@dataclass(frozen=True)
class MemberRecord:
    member_id: str
    party: str
    chamber: str
    coverage_count: int


@dataclass(frozen=True)
class HarnessData:
    member_ids: tuple[str, ...]
    targets: tuple[str, ...]
    values: Mapping[CellKey, float | None]
    members: Mapping[str, MemberRecord]
    edges: Mapping[tuple[str, str], float]


@dataclass(frozen=True)
class PairedComparison:
    baseline_name: str
    imputer_mse: float | None
    baseline_mse: float | None
    denominator: int
    window: str
    relative_reduction: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "baseline": self.baseline_name,
            "baseline_mse": metric_object(
                self.baseline_mse, self.window, self.denominator
            ),
            "imputer_mse": metric_object(
                self.imputer_mse, self.window, self.denominator
            ),
            "relative_reduction": metric_object(
                self.relative_reduction, self.window, self.denominator
            ),
        }


@dataclass(frozen=True)
class ProbeResult:
    name: str
    passed: bool
    verdict: str
    numbers: Mapping[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "numbers": dict(self.numbers),
            "passed": self.passed,
            "verdict": self.verdict,
        }


def canonical_json_bytes(report: Mapping[str, object]) -> bytes:
    """Return the sole canonical report representation."""

    text = json.dumps(
        report,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return (text + "\n").encode("utf-8")


def write_report(path: Path, report: Mapping[str, object]) -> None:
    with path.open("wb") as handle:
        handle.write(canonical_json_bytes(report))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--mask-recover", action="store_true")
    modes.add_argument("--battery", action="store_true")
    modes.add_argument("--look", action="store_true")
    parser.add_argument("--stance-matrix", type=Path)
    parser.add_argument("--edges", action="append", type=Path)
    parser.add_argument("--edge-weight", action="append", type=float)
    parser.add_argument("--members", type=Path)
    parser.add_argument("--pflip-predictions", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seed", required=True, action="append", type=int)
    parser.add_argument("--mask-fraction", default=0.20, type=float)
    parser.add_argument("--knn-k", default=10, type=int)
    parser.add_argument("--lock-file", type=Path)
    parser.add_argument("--allow-real-data", action="store_true")
    return parser


def mode_name(args: argparse.Namespace) -> str:
    if args.look:
        return "look"
    if args.battery:
        return "battery"
    return "mask-recover"


def lock_is_valid(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return LOCKED_MARKER in content and DRAFT_MARKER not in content


def base_report(
    mode: str,
    seeds: Sequence[int],
    invocation: Sequence[str],
    allow_real_data: bool,
) -> dict[str, object]:
    return {
        "allow_real_data": allow_real_data,
        "invocation": list(invocation),
        "mode": mode,
        "schema_id": SCHEMA_ID,
        "seeds": list(seeds),
    }


def refusal_report(
    mode: str,
    seeds: Sequence[int],
    invocation: Sequence[str],
    allow_real_data: bool,
    verdict: str,
    reason: str,
) -> dict[str, object]:
    report = base_report(mode, seeds, invocation, allow_real_data)
    report.update({"reason": reason, "verdict": verdict})
    return report


def validate_mode_arguments(args: argparse.Namespace) -> str | None:
    mode = mode_name(args)
    expected_seeds = 1 if mode == "battery" else 3
    if len(args.seed) != expected_seeds:
        return f"{mode} requires exactly {expected_seeds} --seed value(s)"
    if not 0.0 < args.mask_fraction <= 1.0:
        return "--mask-fraction must be in (0, 1]"
    if args.knn_k < 1:
        return "--knn-k must be positive"
    if args.pflip_predictions is None:
        return "--pflip-predictions is required"
    if mode != "battery":
        if args.stance_matrix is None or args.members is None:
            return "--stance-matrix and --members are required"
        if not args.edges:
            return "at least one --edges value is required"
        if not args.edge_weight:
            return "one --edge-weight per --edges value is required"
        if len(args.edges) != len(args.edge_weight):
            return "--edges and --edge-weight counts must match"
    return None


def metric_object(
    value: float | None, window: str, denominator: int
) -> dict[str, object]:
    return {"denominator": denominator, "value": value, "window": window}


def _finite_float(raw: str, description: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid float for {description}: {raw!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"non-finite float for {description}: {raw!r}")
    return value


def read_stance_matrix(
    path: Path,
) -> tuple[tuple[str, ...], tuple[str, ...], dict[CellKey, float | None]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError("stance matrix is empty") from exc
        if len(header) < 2 or header[0] != "member_id":
            raise ValueError("stance matrix must start with member_id and targets")
        targets = tuple(header[1:])
        if any(not target for target in targets) or len(set(targets)) != len(targets):
            raise ValueError("stance matrix targets must be unique and nonempty")
        member_ids: list[str] = []
        values: dict[CellKey, float | None] = {}
        for line_number, row in enumerate(reader, start=2):
            if len(row) != len(header):
                raise ValueError(f"stance row {line_number} has the wrong width")
            member_id = row[0]
            if not member_id or member_id in member_ids:
                raise ValueError(f"invalid or duplicate member_id at row {line_number}")
            member_ids.append(member_id)
            for target, raw in zip(targets, row[1:]):
                values[(member_id, target)] = (
                    None
                    if raw == "-"
                    else _finite_float(raw, f"stance row {line_number} target {target}")
                )
    if not member_ids:
        raise ValueError("stance matrix has no members")
    return tuple(member_ids), targets, values


def read_members(
    path: Path, matrix_members: Sequence[str]
) -> dict[str, MemberRecord]:
    required = ("member_id", "party", "chamber", "coverage_count")
    records: dict[str, MemberRecord] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(required):
            raise ValueError("members header must be member_id,party,chamber,coverage_count")
        for line_number, row in enumerate(reader, start=2):
            member_id = row["member_id"]
            if not member_id or member_id in records:
                raise ValueError(f"invalid or duplicate member_id at row {line_number}")
            try:
                coverage_count = int(row["coverage_count"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"invalid coverage_count at members row {line_number}"
                ) from exc
            records[member_id] = MemberRecord(
                member_id=member_id,
                party=row["party"],
                chamber=row["chamber"],
                coverage_count=coverage_count,
            )
    missing = sorted(set(matrix_members) - set(records))
    if missing:
        raise ValueError(f"members file is missing matrix members: {missing}")
    return {member_id: records[member_id] for member_id in matrix_members}


def _edge_key(member_a: str, member_b: str) -> tuple[str, str]:
    return (member_a, member_b) if member_a < member_b else (member_b, member_a)


def read_layered_edges(
    paths: Sequence[Path], multipliers: Sequence[float]
) -> dict[tuple[str, str], float]:
    if len(paths) != len(multipliers) or not paths:
        raise ValueError("edge paths and multipliers must have equal nonzero lengths")
    combined: dict[tuple[str, str], float] = {}
    for path, multiplier in zip(paths, multipliers):
        if not math.isfinite(multiplier):
            raise ValueError("edge multiplier must be finite")
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != ["member_a", "member_b", "weight"]:
                raise ValueError("edges header must be member_a,member_b,weight")
            for line_number, row in enumerate(reader, start=2):
                member_a = row["member_a"]
                member_b = row["member_b"]
                if not member_a or not member_b or member_a == member_b:
                    raise ValueError(f"invalid edge at row {line_number} in {path}")
                weight = _finite_float(
                    row["weight"], f"edge row {line_number} in {path}"
                )
                key = _edge_key(member_a, member_b)
                combined[key] = combined.get(key, 0.0) + multiplier * weight
    return {key: value for key, value in combined.items() if value != 0.0}


def read_pflip_predictions(path: Path) -> tuple[dict[CellKey, float], int]:
    predictions: dict[CellKey, float] = {}
    row_count = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["member_id", "target", "prediction"]:
            raise ValueError(
                "pflip header must be member_id,target,prediction"
            )
        for line_number, row in enumerate(reader, start=2):
            key = (row["member_id"], row["target"])
            if not key[0] or not key[1] or key in predictions:
                raise ValueError(f"invalid or duplicate pflip key at row {line_number}")
            predictions[key] = _finite_float(
                row["prediction"], f"pflip row {line_number}"
            )
            row_count += 1
    return predictions, row_count


def load_harness_data(
    stance_path: Path,
    member_path: Path,
    edge_paths: Sequence[Path],
    edge_multipliers: Sequence[float],
) -> HarnessData:
    member_ids, targets, values = read_stance_matrix(stance_path)
    members = read_members(member_path, member_ids)
    edges = read_layered_edges(edge_paths, edge_multipliers)
    unknown = sorted(
        {member for pair in edges for member in pair} - set(member_ids)
    )
    if unknown:
        raise ValueError(f"edges contain members outside the stance matrix: {unknown}")
    return HarnessData(member_ids, targets, values, members, edges)


def observed_cells(data: HarnessData) -> tuple[CellKey, ...]:
    return tuple(sorted(key for key, value in data.values.items() if value is not None))


def select_mask(
    data: HarnessData, fraction: float, seed: int
) -> tuple[CellKey, ...]:
    cells = observed_cells(data)
    count = int(len(cells) * fraction)
    if count < 1:
        raise ValueError("mask fraction selects no observed cells")
    selected = random.Random(seed).sample(cells, count)
    return tuple(sorted(selected))


def assign_coverage_terciles(data: HarnessData) -> dict[str, str]:
    ordered = sorted(
        data.member_ids,
        key=lambda member_id: (
            data.members[member_id].coverage_count,
            member_id,
        ),
    )
    labels = ("LOW", "MIDDLE", "HIGH")
    count = len(ordered)
    return {
        member_id: labels[min(2, (index * 3) // count)]
        for index, member_id in enumerate(ordered)
    }


def training_values(
    data: HarnessData, masked_cells: Iterable[CellKey]
) -> dict[CellKey, float]:
    masked = set(masked_cells)
    return {
        key: value
        for key, value in data.values.items()
        if value is not None and key not in masked
    }


def global_mean_prediction(
    data: HarnessData, masked_cells: Iterable[CellKey]
) -> float:
    training = training_values(data, masked_cells)
    if not training:
        raise ValueError("no training observations remain")
    return sum(training.values()) / len(training)


def party_mean_predictions(
    data: HarnessData, masked_cells: Iterable[CellKey]
) -> dict[CellKey, float]:
    masked = tuple(masked_cells)
    training = training_values(data, masked)
    global_mean = sum(training.values()) / len(training)
    totals: dict[tuple[str, str], tuple[float, int]] = {}
    for (member_id, target), value in training.items():
        key = (data.members[member_id].party, target)
        total, count = totals.get(key, (0.0, 0))
        totals[key] = (total + value, count + 1)
    result: dict[CellKey, float] = {}
    for member_id, target in masked:
        key = (data.members[member_id].party, target)
        total, count = totals.get(key, (0.0, 0))
        result[(member_id, target)] = total / count if count else global_mean
    return result


def additive_marginal_predictions(
    data: HarnessData, masked_cells: Iterable[CellKey]
) -> dict[CellKey, float]:
    masked = tuple(masked_cells)
    training = training_values(data, masked)
    global_mean = sum(training.values()) / len(training)
    row_totals: dict[str, tuple[float, int]] = {}
    col_totals: dict[str, tuple[float, int]] = {}
    for (member_id, target), value in training.items():
        row_total, row_count = row_totals.get(member_id, (0.0, 0))
        col_total, col_count = col_totals.get(target, (0.0, 0))
        row_totals[member_id] = (row_total + value, row_count + 1)
        col_totals[target] = (col_total + value, col_count + 1)
    result: dict[CellKey, float] = {}
    for member_id, target in masked:
        row_total, row_count = row_totals.get(member_id, (0.0, 0))
        col_total, col_count = col_totals.get(target, (0.0, 0))
        result[(member_id, target)] = (
            row_total / row_count + col_total / col_count - global_mean
            if row_count and col_count
            else global_mean
        )
    return result


def pflip_predictions_for_mask(
    masked_cells: Iterable[CellKey], predictions: Mapping[CellKey, float]
) -> tuple[dict[CellKey, float], int]:
    masked = tuple(masked_cells)
    restricted = {key: predictions[key] for key in masked if key in predictions}
    return restricted, len(masked) - len(restricted)


def rmsd_excluding_target_and_mask(
    data: HarnessData,
    member_a: str,
    member_b: str,
    target: str,
    masked_cells: Iterable[CellKey],
) -> tuple[float, int] | None:
    masked = set(masked_cells)
    squared_differences: list[float] = []
    for other_target in data.targets:
        if other_target == target:
            continue
        key_a = (member_a, other_target)
        key_b = (member_b, other_target)
        if key_a in masked or key_b in masked:
            continue
        value_a = data.values.get(key_a)
        value_b = data.values.get(key_b)
        if value_a is None or value_b is None:
            continue
        squared_differences.append((value_a - value_b) ** 2)
    count = len(squared_differences)
    if count < 3:
        return None
    return math.sqrt(sum(squared_differences) / count), count


def knn_prediction(
    data: HarnessData,
    cell: CellKey,
    masked_cells: Iterable[CellKey],
    global_training_mean: float,
    knn_k: int,
) -> tuple[float, bool]:
    member_id, target = cell
    masked = set(masked_cells)
    candidates: list[tuple[float, str, float]] = []
    for (member_a, member_b), edge_weight in data.edges.items():
        if member_a == member_id:
            neighbor = member_b
        elif member_b == member_id:
            neighbor = member_a
        else:
            continue
        neighbor_key = (neighbor, target)
        neighbor_value = data.values.get(neighbor_key)
        if neighbor_value is None or neighbor_key in masked:
            continue
        rmsd_result = rmsd_excluding_target_and_mask(
            data, member_id, neighbor, target, masked
        )
        if rmsd_result is None:
            continue
        rmsd, _ = rmsd_result
        weight = edge_weight * (1.0 / (1.0 + rmsd))
        candidates.append((weight, neighbor, neighbor_value))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    selected = candidates[:knn_k]
    denominator = sum(weight for weight, _, _ in selected)
    if not selected or denominator == 0.0:
        return global_training_mean, True
    prediction = sum(weight * value for weight, _, value in selected) / denominator
    return prediction, False


def imputer_predictions(
    data: HarnessData,
    masked_cells: Iterable[CellKey],
    knn_k: int,
) -> tuple[dict[CellKey, float], dict[CellKey, bool]]:
    masked = tuple(masked_cells)
    global_mean = global_mean_prediction(data, masked)
    predictions: dict[CellKey, float] = {}
    fallbacks: dict[CellKey, bool] = {}
    for cell in masked:
        prediction, fallback = knn_prediction(
            data, cell, masked, global_mean, knn_k
        )
        predictions[cell] = prediction
        fallbacks[cell] = fallback
    return predictions, fallbacks


def _mse(
    actual: Mapping[CellKey, float],
    predicted: Mapping[CellKey, float],
    keys: Sequence[CellKey],
) -> float | None:
    if not keys:
        return None
    return sum((predicted[key] - actual[key]) ** 2 for key in keys) / len(keys)


def mse_metric(
    actual: Mapping[CellKey, float],
    predicted: Mapping[CellKey, float],
    keys: Iterable[CellKey],
    window: str,
) -> dict[str, object]:
    paired_keys = tuple(sorted(set(keys) & set(actual) & set(predicted)))
    return metric_object(
        _mse(actual, predicted, paired_keys), window, len(paired_keys)
    )


def paired_comparison(
    baseline_name: str,
    actual: Mapping[CellKey, float],
    imputer: Mapping[CellKey, float],
    baseline: Mapping[CellKey, float],
    keys: Iterable[CellKey],
    window: str,
) -> PairedComparison:
    paired_keys = tuple(
        sorted(set(keys) & set(actual) & set(imputer) & set(baseline))
    )
    imputer_mse = _mse(actual, imputer, paired_keys)
    baseline_mse = _mse(actual, baseline, paired_keys)
    relative_reduction = (
        None
        if baseline_mse in (None, 0.0) or imputer_mse is None
        else (baseline_mse - imputer_mse) / baseline_mse
    )
    return PairedComparison(
        baseline_name=baseline_name,
        imputer_mse=imputer_mse,
        baseline_mse=baseline_mse,
        denominator=len(paired_keys),
        window=window,
        relative_reduction=relative_reduction,
    )


def comparison_is_win(comparison: PairedComparison) -> bool:
    return (
        comparison.denominator > 0
        and comparison.imputer_mse is not None
        and comparison.baseline_mse is not None
        and comparison.imputer_mse < comparison.baseline_mse
    )


def _stratum_rows(
    data: HarnessData,
    masked: Sequence[CellKey],
    actual: Mapping[CellKey, float],
    method_predictions: Mapping[str, Mapping[CellKey, float]],
) -> list[dict[str, object]]:
    terciles = assign_coverage_terciles(data)
    member_strata = {
        (
            data.members[member_id].party,
            data.members[member_id].chamber,
            terciles[member_id],
        )
        for member_id in data.member_ids
    }
    rows: list[dict[str, object]] = []
    for party, chamber, tercile in sorted(member_strata):
        keys = tuple(
            key
            for key in masked
            if data.members[key[0]].party == party
            and data.members[key[0]].chamber == chamber
            and terciles[key[0]] == tercile
        )
        window = (
            f"{MASK_WINDOW}:party={party};chamber={chamber};"
            f"coverage_tercile={tercile}"
        )
        methods: dict[str, object] = {}
        for name, predictions in method_predictions.items():
            method_window = PFLIP_WINDOW if name == "pflip" else window
            if name == "pflip":
                method_window = f"{PFLIP_WINDOW}:{window.split(':', 1)[1]}"
            methods[name] = mse_metric(
                actual, predictions, keys, method_window
            )
        pflip_window = f"{PFLIP_WINDOW}:{window.split(':', 1)[1]}"
        pflip_keys = set(keys) & set(method_predictions["pflip"])
        methods["imputer_paired_with_pflip"] = mse_metric(
            actual,
            method_predictions["imputer"],
            pflip_keys,
            pflip_window,
        )
        rows.append(
            {
                "chamber": chamber,
                "coverage_tercile": tercile,
                "methods": methods,
                "party": party,
            }
        )
    return rows


def evaluate_mask(
    data: HarnessData,
    masked_cells: Iterable[CellKey],
    pflip_predictions: Mapping[CellKey, float],
    knn_k: int,
) -> dict[str, object]:
    masked = tuple(sorted(masked_cells))
    actual = {
        key: data.values[key]
        for key in masked
        if data.values.get(key) is not None
    }
    actual_float = {key: float(value) for key, value in actual.items()}
    global_value = global_mean_prediction(data, masked)
    global_predictions = {key: global_value for key in masked}
    party_predictions = party_mean_predictions(data, masked)
    additive_predictions = additive_marginal_predictions(data, masked)
    restricted_pflip, pflip_exclusions = pflip_predictions_for_mask(
        masked, pflip_predictions
    )
    imputer, fallbacks = imputer_predictions(data, masked, knn_k)
    baselines: dict[str, Mapping[CellKey, float]] = {
        "global_mean": global_predictions,
        "party_mean": party_predictions,
        "additive_marginal": additive_predictions,
        "pflip": restricted_pflip,
    }
    comparisons: dict[str, PairedComparison] = {}
    for name in BASELINE_NAMES:
        window = PFLIP_WINDOW if name == "pflip" else MASK_WINDOW
        comparisons[name] = paired_comparison(
            name,
            actual_float,
            imputer,
            baselines[name],
            masked,
            window,
        )
    method_predictions: dict[str, Mapping[CellKey, float]] = {
        "imputer": imputer,
        **baselines,
    }
    pooled = {
        name: mse_metric(
            actual_float,
            predictions,
            masked,
            PFLIP_WINDOW if name == "pflip" else MASK_WINDOW,
        )
        for name, predictions in method_predictions.items()
    }
    pooled["imputer_paired_with_pflip"] = mse_metric(
        actual_float,
        imputer,
        restricted_pflip,
        PFLIP_WINDOW,
    )
    cells: list[dict[str, object]] = []
    for member_id, target in masked:
        key = (member_id, target)
        cells.append(
            {
                "actual": actual_float[key],
                "additive_marginal": additive_predictions[key],
                "global_mean": global_predictions[key],
                "imputer": {
                    "fallback": fallbacks[key],
                    "value": imputer[key],
                },
                "member_id": member_id,
                "party_mean": party_predictions[key],
                "pflip": {
                    "excluded": key not in restricted_pflip,
                    "value": restricted_pflip.get(key),
                },
                "target": target,
            }
        )
    all_four = all(comparison_is_win(comparisons[name]) for name in BASELINE_NAMES)
    return {
        "comparisons": {
            name: comparisons[name].as_dict() for name in BASELINE_NAMES
        },
        "fallback_count": sum(fallbacks.values()),
        "imputer_beats_all_four": all_four,
        "masked_cells": cells,
        "masked_count": len(masked),
        "observed_cell_count": len(observed_cells(data)),
        "pflip_exclusion_count": pflip_exclusions,
        "pooled_mse": pooled,
        "strata": _stratum_rows(
            data, masked, actual_float, method_predictions
        ),
    }


def stability_verdict(seed_results: Sequence[Mapping[str, object]]) -> str:
    wins = [bool(result["imputer_beats_all_four"]) for result in seed_results]
    if wins and all(wins):
        return "STABLE_PASS"
    if any(wins):
        return "FRAGILE"
    return "FAIL"


def build_mask_recover_report(
    mode: str,
    seeds: Sequence[int],
    invocation: Sequence[str],
    allow_real_data: bool,
    data: HarnessData,
    pflip_predictions: Mapping[CellKey, float],
    pflip_path: Path,
    pflip_row_count: int,
    mask_fraction: float,
    knn_k: int,
) -> dict[str, object]:
    results: list[dict[str, object]] = []
    for seed in seeds:
        masked = select_mask(data, mask_fraction, seed)
        result = evaluate_mask(data, masked, pflip_predictions, knn_k)
        result["seed"] = seed
        results.append(result)
    report = base_report(mode, seeds, invocation, allow_real_data)
    report.update(
        {
            "inputs": {
                "pflip_predictions": {
                    "path": str(pflip_path),
                    "row_count": pflip_row_count,
                }
            },
            "knn_k": knn_k,
            "mask_fraction": mask_fraction,
            "results": results,
            "stability_verdict": stability_verdict(results),
            "verdict": "MASK_RECOVER_COMPLETE",
        }
    )
    return report


def generate_connected_network(
    rng: random.Random, member_ids: Sequence[str]
) -> dict[tuple[str, str], float]:
    """Generate a seeded random connected cycle with fixed degree two."""

    count = len(member_ids)
    if count < 4:
        raise ValueError("battery network requires at least four members")
    cycle = list(member_ids)
    rng.shuffle(cycle)
    edges: dict[tuple[str, str], float] = {}
    for index, member_id in enumerate(cycle):
        neighbor = cycle[(index + 1) % count]
        edges[_edge_key(member_id, neighbor)] = rng.uniform(
            *BATTERY_CYCLE_WEIGHT_RANGE
        )
    return edges


def _battery_members(
    rng: random.Random, member_ids: Sequence[str]
) -> dict[str, MemberRecord]:
    records: dict[str, MemberRecord] = {}
    for index, member_id in enumerate(member_ids):
        records[member_id] = MemberRecord(
            member_id=member_id,
            party="A" if index % 2 == 0 else "B",
            chamber="HOUSE" if index % 3 else "SENATE",
            coverage_count=50 + index * 3 + rng.randrange(3),
        )
    return records


def generate_planted_data(
    rng: random.Random,
    member_count: int = BATTERY_MEMBER_COUNT,
    target_count: int = BATTERY_TARGET_COUNT,
    observation_probability: float = BATTERY_OBSERVATION_PROBABILITY,
) -> HarnessData:
    member_ids = tuple(f"M{index:03d}" for index in range(member_count))
    targets = tuple(f"T{index:03d}" for index in range(target_count))
    edges = generate_connected_network(rng, member_ids)
    members = _battery_members(rng, member_ids)
    observed = {
        (member_id, target): rng.random() < observation_probability
        for member_id in member_ids
        for target in targets
    }
    neighbors: dict[str, list[str]] = {member_id: [] for member_id in member_ids}
    for member_a, member_b in edges:
        neighbors[member_a].append(member_b)
        neighbors[member_b].append(member_a)
    values: dict[CellKey, float | None] = {}
    for target in targets:
        loading = rng.uniform(*BATTERY_LOADING_RANGE)
        latents = {
            member_id: rng.gauss(0.0, BATTERY_LATENT_STDDEV)
            for member_id in member_ids
        }
        for member_id in member_ids:
            smoothing_members = (member_id, *sorted(neighbors[member_id]))
            smoothed_latent = sum(
                latents[other_member] for other_member in smoothing_members
            ) / len(smoothing_members)
            generated_value = loading * smoothed_latent + rng.gauss(
                0.0, BATTERY_PLANTED_NOISE_SIGMA
            )
            values[(member_id, target)] = (
                generated_value if observed[(member_id, target)] else None
            )
    return HarnessData(member_ids, targets, values, members, edges)


def degree_preserving_shuffle(
    edges: Mapping[tuple[str, str], float], rng: random.Random
) -> dict[tuple[str, str], float]:
    shuffled = dict(edges)
    attempt_count = max(
        1, BATTERY_NULL_SWAP_ATTEMPTS_PER_EDGE * len(shuffled)
    )
    for _ in range(attempt_count):
        pairs = sorted(shuffled)
        first, second = rng.sample(pairs, 2)
        member_a, member_b = first
        member_c, member_d = second
        if len({member_a, member_b, member_c, member_d}) != 4:
            continue
        if rng.randrange(2):
            new_first = _edge_key(member_a, member_c)
            new_second = _edge_key(member_b, member_d)
        else:
            new_first = _edge_key(member_a, member_d)
            new_second = _edge_key(member_b, member_c)
        if (
            new_first == new_second
            or new_first in shuffled
            or new_second in shuffled
        ):
            continue
        first_weight = shuffled.pop(first)
        second_weight = shuffled.pop(second)
        shuffled[new_first] = first_weight
        shuffled[new_second] = second_weight
    return shuffled


def generate_confound_data(
    rng: random.Random, planted: HarnessData
) -> HarnessData:
    values: dict[CellKey, float | None] = {}
    for member_id in planted.member_ids:
        party_effect = 1.0 if planted.members[member_id].party == "A" else -1.0
        for target in planted.targets:
            key = (member_id, target)
            values[key] = (
                None
                if planted.values[key] is None
                else party_effect + rng.gauss(0.0, BATTERY_CONFOUND_NOISE_SIGMA)
            )
    return HarnessData(
        planted.member_ids,
        planted.targets,
        values,
        planted.members,
        planted.edges,
    )


def generate_battery_fixtures(
    seed: int,
) -> tuple[HarnessData, HarnessData, HarnessData]:
    rng = random.Random(seed)
    planted = generate_planted_data(rng)
    null = HarnessData(
        planted.member_ids,
        planted.targets,
        planted.values,
        planted.members,
        degree_preserving_shuffle(planted.edges, rng),
    )
    confound = generate_confound_data(rng, planted)
    return planted, null, confound


def evaluate_planted_probe(
    comparisons: Mapping[str, PairedComparison],
) -> ProbeResult:
    passed = all(
        name in comparisons and comparison_is_win(comparisons[name])
        for name in BASELINE_NAMES
    )
    return ProbeResult(
        name="PLANTED",
        passed=passed,
        verdict="PLANTED_PASS" if passed else "PLANTED_FAIL",
        numbers={
            "comparisons": {
                name: comparisons[name].as_dict()
                for name in BASELINE_NAMES
                if name in comparisons
            }
        },
    )


def evaluate_null_probe(global_comparison: PairedComparison) -> ProbeResult:
    passed = (
        global_comparison.denominator > 0
        and global_comparison.imputer_mse is not None
        and global_comparison.baseline_mse is not None
        and global_comparison.imputer_mse
        >= 0.98 * global_comparison.baseline_mse
    )
    return ProbeResult(
        name="NULL",
        passed=passed,
        verdict="NULL_PASS" if passed else "NULL_FAIL",
        numbers={"global_mean_comparison": global_comparison.as_dict()},
    )


def evaluate_confound_probe(party_comparison: PairedComparison) -> ProbeResult:
    passed = (
        party_comparison.denominator > 0
        and party_comparison.imputer_mse is not None
        and party_comparison.baseline_mse is not None
        and party_comparison.imputer_mse
        >= 0.98 * party_comparison.baseline_mse
    )
    return ProbeResult(
        name="CONFOUND",
        passed=passed,
        verdict="CONFOUND_PASS" if passed else "CONFOUND_FAIL",
        numbers={"party_mean_comparison": party_comparison.as_dict()},
    )


def _comparison_from_evaluation(
    evaluation: Mapping[str, object], baseline_name: str
) -> PairedComparison:
    comparison = evaluation["comparisons"][baseline_name]  # type: ignore[index]
    imputer_metric = comparison["imputer_mse"]  # type: ignore[index]
    baseline_metric = comparison["baseline_mse"]  # type: ignore[index]
    reduction_metric = comparison["relative_reduction"]  # type: ignore[index]
    return PairedComparison(
        baseline_name=baseline_name,
        imputer_mse=imputer_metric["value"],  # type: ignore[index]
        baseline_mse=baseline_metric["value"],  # type: ignore[index]
        denominator=imputer_metric["denominator"],  # type: ignore[index]
        window=imputer_metric["window"],  # type: ignore[index]
        relative_reduction=reduction_metric["value"],  # type: ignore[index]
    )


def run_battery_probe(
    name: str,
    data: HarnessData,
    masked_cells: Sequence[CellKey],
    pflip_predictions: Mapping[CellKey, float],
    knn_k: int,
) -> ProbeResult:
    evaluation = evaluate_mask(data, masked_cells, pflip_predictions, knn_k)
    comparisons = {
        baseline: _comparison_from_evaluation(evaluation, baseline)
        for baseline in BASELINE_NAMES
    }
    if name == "PLANTED":
        scored = evaluate_planted_probe(comparisons)
    elif name == "NULL":
        scored = evaluate_null_probe(comparisons["global_mean"])
    elif name == "CONFOUND":
        scored = evaluate_confound_probe(comparisons["party_mean"])
    else:
        raise ValueError(f"unknown battery probe {name!r}")
    numbers = dict(scored.numbers)
    numbers["evaluation"] = evaluation
    return ProbeResult(scored.name, scored.passed, scored.verdict, numbers)


def aggregate_battery_results(
    probes: Sequence[ProbeResult],
) -> tuple[str, tuple[str, ...]]:
    expected_names = ("PLANTED", "NULL", "CONFOUND")
    names = tuple(probe.name for probe in probes)
    composition_problems: list[str] = []
    if not names:
        composition_problems.append("COMPOSITION_EMPTY")
    for name in expected_names:
        count = names.count(name)
        if count == 0:
            composition_problems.append(f"COMPOSITION_MISSING:{name}")
        elif count > 1:
            composition_problems.append(f"COMPOSITION_DUPLICATE:{name}")
    for name in sorted(set(names) - set(expected_names)):
        composition_problems.append(f"COMPOSITION_EXTRA:{name}")
    if composition_problems:
        return "BATTERY_REFUSED", tuple(composition_problems)
    failing = tuple(probe.name for probe in probes if not probe.passed)
    return ("BATTERY_PASS" if not failing else "BATTERY_REFUSED", failing)


def battery_generator_parameters(
    planted: HarnessData,
    null: HarnessData,
    confound: HarnessData,
    mask_fraction: float,
) -> dict[str, object]:
    observed_by_member = {
        member_id: sum(
            planted.values[(member_id, target)] is not None
            for target in planted.targets
        )
        for member_id in planted.member_ids
    }
    observed_by_target = {
        target: sum(
            planted.values[(member_id, target)] is not None
            for member_id in planted.member_ids
        )
        for target in planted.targets
    }
    observed_cell_count = sum(observed_by_member.values())
    grid_cell_count = len(planted.member_ids) * len(planted.targets)
    member_pair_count = math.comb(len(planted.member_ids), 2)
    coobserved_pair_incidences = sum(
        math.comb(count, 2) for count in observed_by_target.values()
    )
    subset_sizes = tuple(observed_by_member.values())
    degrees = {member_id: 0 for member_id in planted.member_ids}
    for member_a, member_b in planted.edges:
        degrees[member_a] += 1
        degrees[member_b] += 1
    return {
        "edge_counts": {
            "null": len(null.edges),
            "planted": len(planted.edges),
        },
        "grid_dimensions": {
            "confound": {
                "members": len(confound.member_ids),
                "targets": len(confound.targets),
            },
            "null": {
                "members": len(null.member_ids),
                "targets": len(null.targets),
            },
            "planted": {
                "members": len(planted.member_ids),
                "targets": len(planted.targets),
            },
        },
        "mask_fraction": mask_fraction,
        "latent_and_loading": {
            "latent_distribution": "independent_gaussian",
            "latent_standard_deviation": BATTERY_LATENT_STDDEV,
            "loading_distribution": "independent_uniform",
            "loading_maximum": BATTERY_LOADING_RANGE[1],
            "loading_minimum": BATTERY_LOADING_RANGE[0],
            "smoothing": "unweighted_mean_of_self_and_true_edge_neighbors",
        },
        "network_generation": {
            "configured_degree": BATTERY_NETWORK_DEGREE,
            "degree_maximum": max(degrees.values()),
            "degree_mean": sum(degrees.values()) / len(degrees),
            "degree_minimum": min(degrees.values()),
            "topology": "seeded_random_hamiltonian_cycle",
            "weight_maximum": BATTERY_CYCLE_WEIGHT_RANGE[1],
            "weight_minimum": BATTERY_CYCLE_WEIGHT_RANGE[0],
        },
        "noise_sigma": {
            "confound_absolute": BATTERY_CONFOUND_NOISE_SIGMA,
            "planted_absolute": BATTERY_PLANTED_NOISE_SIGMA,
        },
        "observation_pattern": {
            "coobserved_pair_incidences": coobserved_pair_incidences,
            "distribution": "independent_bernoulli_per_member_target_cell",
            "expected_coobserved_cells_per_random_member_pair_realized": (
                coobserved_pair_incidences / member_pair_count
            ),
            "expected_coobserved_cells_per_random_member_pair_theoretical": (
                BATTERY_OBSERVATION_PROBABILITY
                * BATTERY_OBSERVATION_PROBABILITY
                * len(planted.targets)
            ),
            "grid_cell_count": grid_cell_count,
            "member_subset_size_maximum": max(subset_sizes),
            "member_subset_size_mean": observed_cell_count / len(subset_sizes),
            "member_subset_size_minimum": min(subset_sizes),
            "network_independent": True,
            "null_reuses_planted_values_and_missingness": True,
            "observation_probability": BATTERY_OBSERVATION_PROBABILITY,
            "observed_cell_count": observed_cell_count,
            "observed_members_per_target_maximum": max(
                observed_by_target.values()
            ),
            "observed_members_per_target_minimum": min(
                observed_by_target.values()
            ),
            "realized_probability": observed_cell_count / grid_cell_count,
        },
        "swap_counts": {
            "attempted": (
                BATTERY_NULL_SWAP_ATTEMPTS_PER_EDGE * len(planted.edges)
            ),
            "attempts_per_edge": BATTERY_NULL_SWAP_ATTEMPTS_PER_EDGE,
        },
    }


def build_battery_report(
    seed: int,
    invocation: Sequence[str],
    allow_real_data: bool,
    pflip_predictions: Mapping[CellKey, float],
    pflip_path: Path,
    pflip_row_count: int,
    mask_fraction: float,
    knn_k: int,
) -> dict[str, object]:
    planted, null, confound = generate_battery_fixtures(seed)
    planted_mask = select_mask(planted, mask_fraction, seed)
    null_mask = planted_mask
    confound_mask = select_mask(confound, mask_fraction, seed)
    probes = (
        run_battery_probe(
            "PLANTED", planted, planted_mask, pflip_predictions, knn_k
        ),
        run_battery_probe("NULL", null, null_mask, pflip_predictions, knn_k),
        run_battery_probe(
            "CONFOUND", confound, confound_mask, pflip_predictions, knn_k
        ),
    )
    verdict, failing = aggregate_battery_results(probes)
    report = base_report("battery", [seed], invocation, allow_real_data)
    report.update(
        {
            "failing_probes": list(failing),
            "generator_parameters": battery_generator_parameters(
                planted, null, confound, mask_fraction
            ),
            "inputs": {
                "pflip_predictions": {
                    "path": str(pflip_path),
                    "row_count": pflip_row_count,
                }
            },
            "knn_k": knn_k,
            "mask_fraction": mask_fraction,
            "probes": [probe.as_dict() for probe in probes],
            "verdict": verdict,
        }
    )
    return report


def _invocation_for(argv: Sequence[str] | None) -> tuple[list[str], list[str]]:
    if argv is None:
        return list(sys.argv[1:]), list(sys.argv)
    arguments = list(argv)
    return arguments, [str(Path(__file__)), *arguments]


def main(argv: Sequence[str] | None = None) -> int:
    arguments, invocation = _invocation_for(argv)
    args = build_parser().parse_args(arguments)
    mode = mode_name(args)
    if mode in ("look", "mask-recover") and not args.allow_real_data:
        write_report(
            args.out,
            refusal_report(
                mode,
                args.seed,
                invocation,
                args.allow_real_data,
                "REAL_DATA_REFUSED",
                f"{mode} requires --allow-real-data",
            ),
        )
        return 2
    if mode == "look" and not lock_is_valid(args.lock_file):
        write_report(
            args.out,
            refusal_report(
                mode,
                args.seed,
                invocation,
                args.allow_real_data,
                "LOOK_REFUSED_UNLOCKED",
                "look requires an exact locked, non-draft prereg lock file",
            ),
        )
        return 2
    validation_error = validate_mode_arguments(args)
    if validation_error is not None:
        write_report(
            args.out,
            refusal_report(
                mode,
                args.seed,
                invocation,
                args.allow_real_data,
                "INVALID_ARGUMENTS",
                validation_error,
            ),
        )
        return 2
    try:
        pflip_predictions, pflip_row_count = read_pflip_predictions(
            args.pflip_predictions
        )
        if mode == "battery":
            report = build_battery_report(
                seed=args.seed[0],
                invocation=invocation,
                allow_real_data=args.allow_real_data,
                pflip_predictions=pflip_predictions,
                pflip_path=args.pflip_predictions,
                pflip_row_count=pflip_row_count,
                mask_fraction=args.mask_fraction,
                knn_k=args.knn_k,
            )
            write_report(args.out, report)
            return 0 if report["verdict"] == "BATTERY_PASS" else 3
        data = load_harness_data(
            args.stance_matrix,
            args.members,
            args.edges,
            args.edge_weight,
        )
        report = build_mask_recover_report(
            mode=mode,
            seeds=args.seed,
            invocation=invocation,
            allow_real_data=args.allow_real_data,
            data=data,
            pflip_predictions=pflip_predictions,
            pflip_path=args.pflip_predictions,
            pflip_row_count=pflip_row_count,
            mask_fraction=args.mask_fraction,
            knn_k=args.knn_k,
        )
        report["inputs"].update(  # type: ignore[union-attr]
            {
                "edge_weights": list(args.edge_weight),
                "edges": [str(path) for path in args.edges],
                "members": str(args.members),
                "stance_matrix": str(args.stance_matrix),
            }
        )
        write_report(args.out, report)
        return 0
    except (OSError, UnicodeError, ValueError, csv.Error) as exc:
        write_report(
            args.out,
            refusal_report(
                mode,
                args.seed,
                invocation,
                args.allow_real_data,
                "INPUT_REFUSED",
                str(exc),
            ),
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
