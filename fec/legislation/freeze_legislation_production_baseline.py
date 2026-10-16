# -*- coding: utf-8 -*-
"""Freeze an immutable pre-sync baseline for legislation-dependent artifacts.

The source tree is read-only. Existing baseline copies are never overwritten:
an existing destination must be byte-identical or the command fails. The
manifest therefore remains a trustworthy old-vs-new and rollback reference.
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any


HERE = Path(__file__).resolve().parent.parent
ROOT = HERE.parent
DEFAULT_OUT = ROOT / "audits" / "legfix5" / "production_baseline"

NAMED_ARTIFACTS = (
    "Moneyball Main Version - Blended.xlsx",
    "archive_leg.json",
    "legislation_routing_audit.json",
    "vote_category_lean.json",
    "behavior_panel.json",
    "behavioral_dispositions.json",
    "bipartisan.json",
    "coop_v2_candidate.json",
    "member_receptivity.json",
    "category_stance.json",
    "data/engine/pillar_inputs_snapshot.json",
    "ranks_python.json",
    "say_do_gap.json",
    "data/money/electoral_plays.json",
    "per_topic_alloc.json",
    "data/lobbying/per_topic_coalition.json",
    "data/money/budget_frontier.json",
    "data/money/budget_frontier_electoral.json",
    "optimize_result.json",
    "im_allocation.json",
    "salience_advisory.json",
    "dashboard_data.json",
    "Moneyball Dashboard.html",
    "member_desk_bundle.json",
    "dashboard_exclusion_reconciliation.json",
    "Moneyball.html",
    "cu-member-desk.html",
)

SOURCE_CONTRACTS = (
    "fec/bill_rules.py",
    "fec/legislation/build_archive_legislative.py",
    "fec/members/behavior_panel.py",
    "fec/members/behavioral_dispositions.py",
    "fec/members/coop_v2_candidate.py",
    "fec/legislation/vote_by_category.py",
    "fec/stance/per_category_stance.py",
    "fec/legislation/apply_leg_stance.py",
    "fec/engine/compute_pillars.py",
    "fec/network/build_multilayer_network.py",
    "fec/surfaces/build_dashboard_bundle.py",
    "fec/engine/build_electoral_plays.py",
    "fec/engine/per_topic_flip.py",
    "fec/engine/per_topic_coalition.py",
    "fec/engine/budget_frontier.py",
    "fec/engine/optimize.py",
    "fec/engine/build_im_allocation.py",
    "fec/engine/salience_shock.py",
    "fec/surfaces/make_dashboard_html.py",
    "fec/surfaces/make_member_desk.py",
    "fec/audit/dashboard_integrity.py",
    "fec/surfaces/make_combined.py",
    "fec/stance/say_do_gap.py",
    "fec/run_pipeline.py",
    "fec/weekly_refresh.py",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def assert_safe_out(root: Path, out: Path) -> None:
    audit_root = (root / "audits" / "legfix5").resolve()
    try:
        out.resolve().relative_to(audit_root)
    except ValueError as exc:
        raise SystemExit(
            "REFUSING: baseline output must be under %s" % audit_root
        ) from exc
    if out.resolve() == audit_root:
        raise SystemExit("REFUSING: choose a baseline subdirectory")


def copy_immutable(source: Path, destination: Path) -> dict[str, Any]:
    source_hash = sha256(source)
    if destination.exists():
        if sha256(destination) != source_hash:
            raise SystemExit(
                "REFUSING: immutable baseline collision at %s" % destination
            )
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=destination.name + ".", suffix=".copying",
            dir=str(destination.parent),
        )
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            shutil.copy2(source, temporary)
            if sha256(temporary) != source_hash:
                raise OSError("baseline copy hash mismatch")
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
    stat = source.stat()
    return {
        "source": str(source.resolve()),
        "copy": str(destination.resolve()),
        "size": stat.st_size,
        "source_mtime": dt.datetime.fromtimestamp(
            stat.st_mtime, tz=dt.timezone.utc
        ).isoformat(),
        "sha256": source_hash,
    }


def selected_paths(root: Path) -> tuple[list[Path], dict[str, Any]]:
    archives = [
        Path(path)
        for path in glob.glob(str(root / "congress_bills_archive_*.xlsx"))
    ]
    networks = [
        Path(path)
        for path in glob.glob(str(root / "congress_cosponsor_network_*.xlsx"))
    ]
    archive_mtime = max(
        archives, key=lambda path: path.stat().st_mtime, default=None
    )
    archive_lexical = sorted(archives)[-1] if archives else None
    augmented = [
        path for path in networks if "AUG" in path.name.upper()
    ]
    engine_network = max(
        augmented or networks,
        key=lambda path: path.stat().st_mtime,
        default=None,
    )
    base_networks = [
        path for path in networks if "AUG" not in path.name.upper()
    ]
    base_network = max(
        base_networks,
        key=lambda path: path.stat().st_mtime,
        default=None,
    )
    selected = [
        path
        for path in (
            archive_mtime,
            archive_lexical,
            base_network,
            engine_network,
        )
        if path is not None
    ]
    selected.extend(root / name for name in NAMED_ARTIFACTS)
    selected.extend(root / name for name in SOURCE_CONTRACTS)
    deduped = list(dict.fromkeys(path for path in selected if path.exists()))
    selection = {
        "archive_selected_by_mtime": (
            archive_mtime.name if archive_mtime else None
        ),
        "archive_selected_lexically": (
            archive_lexical.name if archive_lexical else None
        ),
        "archive_selection_methods_agree": (
            archive_mtime == archive_lexical and archive_mtime is not None
        ),
        "base_network_selected_by_mtime": (
            base_network.name if base_network else None
        ),
        "engine_network_selected_augmented_first": (
            engine_network.name if engine_network else None
        ),
    }
    return deduped, selection


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    out = args.out.resolve()
    assert_safe_out(root, out)
    out.mkdir(parents=True, exist_ok=True)
    sources, selection = selected_paths(root)
    receipts = []
    for source in sources:
        relative = source.relative_to(root)
        receipts.append(copy_immutable(source, out / "files" / relative))
    missing = [
        name for name in NAMED_ARTIFACTS if not (root / name).exists()
    ]
    manifest = {
        "schema": "legislation-production-baseline-v1",
        "frozen_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_root": str(root),
        "immutable_existing_copy_policy": True,
        "selection": selection,
        "missing_named_artifacts": missing,
        "file_count": len(receipts),
        "files": receipts,
    }
    manifest_path = out / "baseline_manifest.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        previous_hashes = {
            row["source"]: row["sha256"] for row in previous.get("files", [])
        }
        current_hashes = {
            row["source"]: row["sha256"] for row in receipts
        }
        if previous_hashes != current_hashes:
            raise SystemExit(
                "REFUSING: production changed after this baseline was frozen; "
                "use a new explicit baseline directory"
            )
        print("baseline already frozen and byte-identical:", manifest_path)
        return 0
    fd, temporary_name = tempfile.mkstemp(
        prefix=manifest_path.name + ".", suffix=".writing", dir=str(out),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, manifest_path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    print(
        "production baseline frozen: %d files, %d missing named artifacts"
        % (len(receipts), len(missing))
    )
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
