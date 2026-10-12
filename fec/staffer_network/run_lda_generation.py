"""Run a fresh, immutable, rate-limited LDA sweep generation.

This is an orchestration wrapper around the established LDA collector, not a
second collector.  It seals the legacy tree before and after the run and writes
an atomic local status file so a long anonymous acquisition remains observable.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

FEC_ROOT = Path(__file__).resolve().parents[1]
if str(FEC_ROOT) not in sys.path:
    sys.path.insert(0, str(FEC_ROOT))

from staffer_network.lda_contract import audit_sweeps, sweep_tree_inventory
from staffer_network.source_manifest import canonical_json_bytes


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_status(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_bytes(canonical_json_bytes(value))
    os.replace(temporary, path)


def _parse_years(value: str) -> list[int]:
    years = [int(part) for part in value.split(",") if part]
    if not years or len(years) != len(set(years)):
        raise ValueError("years must be a nonempty, duplicate-free CSV")
    return years


def run(args: argparse.Namespace) -> int:
    generation_root = Path(args.generation_root).resolve()
    legacy_root = Path(args.legacy_sweeps_root).resolve()
    years = _parse_years(args.years)
    if generation_root == legacy_root:
        raise RuntimeError("generation root cannot equal the legacy sweep root")
    if generation_root.exists() and any(generation_root.iterdir()):
        raise RuntimeError(f"generation root is not empty: {generation_root}")
    generation_root.mkdir(parents=True, exist_ok=True)
    status_path = generation_root / "generation_status.json"
    legacy_before = sweep_tree_inventory(legacy_root, expected_years=args.legacy_years)
    if legacy_before["inventory_sha256"] != args.legacy_inventory_sha256:
        raise RuntimeError("legacy sweep inventory does not match the approved start seal")

    state: dict[str, Any] = {
        "schema_id": "staffer-network-lda-generation-status-v1",
        "generation_id": generation_root.name,
        "status": "RUNNING",
        "started_at": _now(),
        "completed_at": None,
        "authentication_mode": "anonymous",
        "rate_limit_rpm": 15,
        "terms_uri": "https://lda.gov/api/tos/",
        "terms_checked_at": args.terms_checked_at,
        "years_expected": years,
        "years_completed": [],
        "current_year": None,
        "legacy_inventory_before": legacy_before,
        "legacy_inventory_after": None,
        "audit": None,
        "error": None,
    }
    _write_status(status_path, state)

    collector = Path(__file__).resolve().parents[1] / "lobbying" / "collect_lda_filings.py"
    environment = dict(os.environ)
    environment.pop("LDA_API_KEY", None)
    environment["LDA_RPM"] = "15"
    environment["LDA_MAX_TRIES"] = "12"
    environment["LDA_MAX_BACKOFF_SECONDS"] = "60"
    environment["LDA_TERMS_CHECKED_AT"] = args.terms_checked_at
    try:
        for year in years:
            state["current_year"] = year
            _write_status(status_path, state)
            log_path = generation_root / f"collector_{year}.log"
            command = [
                sys.executable,
                str(collector),
                "--sweep-year",
                str(year),
                "--sweep-generation-root",
                str(generation_root),
            ]
            with log_path.open("wb") as log:
                completed = subprocess.run(
                    command,
                    cwd=str(Path(__file__).resolve().parents[2]),
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            if completed.returncode != 0:
                raise RuntimeError(f"LDA collector failed for {year} with exit {completed.returncode}")
            state["years_completed"].append(year)
            _write_status(status_path, state)

        audit = audit_sweeps(generation_root, expected_years=years)
        if not audit["pass"]:
            raise RuntimeError("fresh LDA generation failed source-inventory reconciliation")
        legacy_after = sweep_tree_inventory(legacy_root, expected_years=args.legacy_years)
        if legacy_after != legacy_before:
            raise RuntimeError("legacy sweep bytes changed during immutable generation")
        state.update({
            "status": "PASS",
            "completed_at": _now(),
            "current_year": None,
            "legacy_inventory_after": legacy_after,
            "audit": audit,
        })
        _write_status(status_path, state)
        return 0
    except Exception as exc:
        try:
            legacy_after = sweep_tree_inventory(legacy_root, expected_years=args.legacy_years)
        except Exception as inventory_exc:  # pragma: no cover - catastrophic local failure
            legacy_after = {"error": str(inventory_exc)}
        state.update({
            "status": "FAILED",
            "completed_at": _now(),
            "current_year": None,
            "legacy_inventory_after": legacy_after,
            "error": str(exc),
        })
        _write_status(status_path, state)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation-root", required=True)
    parser.add_argument("--legacy-sweeps-root", required=True)
    parser.add_argument("--legacy-inventory-sha256", required=True)
    parser.add_argument("--terms-checked-at", required=True)
    parser.add_argument("--years", default="2022,2023,2024,2025,2026")
    parser.add_argument(
        "--legacy-years", type=int, nargs="+", default=[2021, 2022, 2023, 2024, 2025, 2026]
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        return run(_parser().parse_args(argv))
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
