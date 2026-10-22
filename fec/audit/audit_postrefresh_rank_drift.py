#!/usr/bin/env python3
"""Materialize a non-authoritative frozen-versus-live rank drift receipt."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
from typing import Any
import uuid

try:
    import roster
    from lane_file_io import atomic_write_bytes
except ImportError:  # pragma: no cover - package import path
    from fec import roster
    from fec.lane_file_io import atomic_write_bytes


ROOT = Path(__file__).resolve().parents[2]
BASELINE_ROOT = (
    ROOT / "audits" / "legfix5" / "production_baseline_full_downstream" / "files"
)
DEFAULT_BASELINE_RANKS = BASELINE_ROOT / "ranks_python.json"
DEFAULT_LIVE_RANKS = ROOT / "ranks_python.json"
DEFAULT_BASELINE_WORKBOOK = BASELINE_ROOT / "Moneyball Main Version - Blended.xlsx"
DEFAULT_LIVE_WORKBOOK = ROOT / "Moneyball Main Version - Blended.xlsx"
DEFAULT_OUT_ROOT = (
    ROOT / "audits" / "legfix5" / "post_weekly_refresh_rank_drift"
)


class AuditError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def pretty_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def file_item(path: Path, *, record_count: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if record_count is not None:
        result["record_count"] = record_count
    return result


def read_rank_file(path: Path) -> dict[str, Any]:
    require(path.is_file(), f"Missing rank file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AuditError(f"Unreadable rank file: {path}: {exc}") from exc
    require(isinstance(value, dict), f"Rank file is not an object: {path}")
    for field in ("rank", "score"):
        require(
            isinstance(value.get(field), dict),
            f"Rank file missing object {field}: {path}",
        )
    rank = value["rank"]
    score = value["score"]
    require(set(rank) == set(score), f"Rank/score member set drift: {path}")
    expected_members = roster.count(root=ROOT)
    require(len(rank) == expected_members, f"Expected {expected_members} rank members: {path}")
    observed_ranks = []
    for member in rank:
        require(
            isinstance(member, str) and member.strip(),
            f"Invalid member key: {path}",
        )
        require(
            isinstance(rank[member], int) and not isinstance(rank[member], bool),
            f"Invalid rank for {member}: {path}",
        )
        require(
            isinstance(score[member], (int, float))
            and not isinstance(score[member], bool)
            and math.isfinite(float(score[member])),
            f"Invalid score for {member}: {path}",
        )
        observed_ranks.append(rank[member])
    require(
        sorted(observed_ranks) == list(range(1, expected_members + 1)),
        f"Ranks are not an exact 1..{expected_members} permutation: {path}",
    )
    return value


def row_bytes(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical_bytes(row) for row in rows)


def cleanup_temporary(path: Path, expected_parent: Path) -> None:
    path = path.resolve()
    expected_parent = expected_parent.resolve()
    require(
        path.parent == expected_parent and path.name.startswith(".rank-drift."),
        f"Unsafe temporary cleanup target: {path}",
    )

    def onexc(function: Any, target: str, error: BaseException) -> None:
        if not isinstance(error, PermissionError):
            raise error
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
        function(target)

    if path.exists():
        shutil.rmtree(path, onexc=onexc)


def build_audit(
    *,
    baseline_ranks_path: Path = DEFAULT_BASELINE_RANKS,
    live_ranks_path: Path = DEFAULT_LIVE_RANKS,
    baseline_workbook_path: Path = DEFAULT_BASELINE_WORKBOOK,
    live_workbook_path: Path = DEFAULT_LIVE_WORKBOOK,
    out_root: Path = DEFAULT_OUT_ROOT,
) -> tuple[Path, dict[str, Any], bool]:
    paths = [
        baseline_ranks_path,
        live_ranks_path,
        baseline_workbook_path,
        live_workbook_path,
    ]
    for path in paths:
        require(path.is_file(), f"Missing exact audit input: {path}")

    old = read_rank_file(baseline_ranks_path)
    new = read_rank_file(live_ranks_path)
    require(
        set(old["rank"]) == set(new["rank"]),
        "Frozen/live member set mismatch",
    )

    rows: list[dict[str, Any]] = []
    for member in sorted(old["rank"], key=lambda name: (old["rank"][name], name)):
        old_rank = int(old["rank"][member])
        new_rank = int(new["rank"][member])
        old_score = float(old["score"][member])
        new_score = float(new["score"][member])
        delta = new_rank - old_rank
        rows.append(
            {
                "schema": "post-weekly-refresh-member-rank-drift-v1",
                "member": member,
                "frozen_rank": old_rank,
                "live_rank": new_rank,
                "rank_delta_live_minus_frozen": delta,
                "absolute_rank_delta": abs(delta),
                "frozen_score": old_score,
                "live_score": new_score,
                "score_delta_live_minus_frozen": new_score - old_score,
                "legislation_recomputation_accepted": False,
                "scoring_authority": False,
                "sync_or_promotion_authority": False,
            }
        )

    squared_delta_sum = sum(
        row["rank_delta_live_minus_frozen"] ** 2 for row in rows
    )
    n_members = float(len(rows))
    rank_correlation = 1.0 - (
        6.0 * squared_delta_sum / (n_members * (n_members**2 - 1.0))
    )
    changed = [row for row in rows if row["absolute_rank_delta"]]
    score_changed = [
        row
        for row in rows
        if abs(row["score_delta_live_minus_frozen"]) > 1e-12
    ]
    upward = sorted(
        changed,
        key=lambda row: (
            row["rank_delta_live_minus_frozen"],
            row["member"],
        ),
    )
    downward = sorted(
        changed,
        key=lambda row: (
            -row["rank_delta_live_minus_frozen"],
            row["member"],
        ),
    )
    rows_body = row_bytes(rows)
    inputs = {
        "frozen_ranks": file_item(baseline_ranks_path),
        "live_ranks": file_item(live_ranks_path),
        "frozen_workbook": file_item(baseline_workbook_path),
        "live_workbook": file_item(live_workbook_path),
    }
    producer = file_item(Path(__file__).resolve())
    summary = {
        "member_count": len(rows),
        "member_set_closed": len(rows) == roster.count(root=ROOT),
        "rank_changed_count": len(changed),
        "score_changed_count": len(score_changed),
        "maximum_absolute_rank_movement": max(
            row["absolute_rank_delta"] for row in rows
        ),
        "mean_absolute_rank_movement": sum(
            row["absolute_rank_delta"] for row in rows
        )
        / len(rows),
        "rank_correlation": rank_correlation,
        "largest_upward_moves": upward[:20],
        "largest_downward_moves": downward[:20],
    }
    identity = {
        "schema": "post-weekly-refresh-rank-drift-identity-v1",
        "inputs": inputs,
        "producer": producer,
        "rows_sha256": sha256_bytes(rows_body),
        "summary": summary,
    }
    audit_id = sha256_bytes(canonical_bytes(identity))
    snapshot_root = out_root / "snapshots"
    snapshot_dir = snapshot_root / audit_id
    out_root.mkdir(parents=True, exist_ok=True)
    snapshot_root.mkdir(parents=True, exist_ok=True)
    # tempfile.mkdtemp() requests mode 0o700. On current Windows Python that
    # creates an explicit owner-only ACL which survives a directory rename and
    # can make an otherwise valid receipt unreadable to sandboxed audit
    # workers. Create the same-volume staging directory with normal inherited
    # permissions instead, then atomically rename it into place.
    temporary = snapshot_root / f".rank-drift.{uuid.uuid4().hex}"
    temporary.mkdir()
    created = False
    try:
        rows_path = temporary / "member_rank_drift.ndjson"
        rows_path.write_bytes(rows_body)
        rows_item = file_item(rows_path, record_count=len(rows))
        rows_item["path"] = str(
            (snapshot_dir / "member_rank_drift.ndjson").resolve()
        )
        manifest = {
            "schema": "post-weekly-refresh-rank-drift-manifest-v1",
            "audit_id": audit_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "PASS_OBSERVATIONAL_NONAUTHORITATIVE",
            "inputs": inputs,
            "producer": producer,
            "outputs": {
                "member_rank_drift": rows_item
            },
            "summary": summary,
            "observation_context": (
                "Frozen baseline compared with live outputs observed after "
                "the scheduled weekly refresh; causation is not inferred "
                "from rank contents."
            ),
            "legislation_recomputation_accepted": False,
            "legislation_rank_cutover_performed": False,
            "authoritative_for_legislation_ranks": False,
            "authoritative_for_scoring": False,
            "authoritative_for_sync_or_promotion": False,
            "production_write_performed": False,
            "sync_or_promotion_performed": False,
            "network_executed": False,
        }
        (temporary / "manifest.json").write_bytes(pretty_bytes(manifest))
        if snapshot_dir.exists():
            existing = json.loads(
                (snapshot_dir / "manifest.json").read_text(encoding="utf-8")
            )
            require(
                existing["audit_id"] == audit_id,
                "Existing rank-drift audit identity mismatch",
            )
            require(
                sha256_file(snapshot_dir / "member_rank_drift.ndjson")
                == identity["rows_sha256"],
                "Existing rank-drift rows hash mismatch",
            )
            manifest = existing
        else:
            os.replace(temporary, snapshot_dir)
            created = True
    finally:
        if temporary.exists():
            cleanup_temporary(temporary, snapshot_root)

    pointer = {
        "schema": "post-weekly-refresh-rank-drift-pointer-v1",
        "audit_id": audit_id,
        "snapshot_dir": str(snapshot_dir.resolve()),
        "manifest": file_item(snapshot_dir / "manifest.json"),
        "producer": producer,
        "authoritative_for_legislation_ranks": False,
        "authoritative_for_scoring": False,
        "authoritative_for_sync_or_promotion": False,
        "sync_or_promotion_performed": False,
    }
    pointer_path = out_root / "current.json"
    atomic_write_bytes(pointer_path, pretty_bytes(pointer))
    return snapshot_dir, manifest, created


def main() -> int:
    snapshot_dir, manifest, created = build_audit()
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "created": created,
                "audit_id": manifest["audit_id"],
                "snapshot_dir": str(snapshot_dir),
                "summary": manifest["summary"],
                "sync_or_promotion_performed": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
