# -*- coding: utf-8 -*-
"""Receipt-verified loader for an immutable staffer-network analysis build.

The default/off branch intentionally returns before touching the filesystem.
Analysis mode exposes artifact paths and metadata; it does not eagerly inflate
the multi-million-row edge corpus into process memory.
"""

from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path
from typing import Any


class StaffNetworkModeError(RuntimeError):
    pass


def mode(environ: dict[str, str] | None = None) -> str:
    selected = (environ if environ is not None else os.environ).get("STAFF_NET", "off")
    if selected not in {"off", "analysis"}:
        raise StaffNetworkModeError(
            "STAFF_NET must be 'off' or 'analysis'; engine consumers are not armed"
        )
    return selected


def load_staffer_network(
    environ: dict[str, str] | None = None,
    *,
    root: str | os.PathLike[str] | None = None,
) -> tuple[dict[str, Any], Any] | None:
    selected_environment = environ if environ is not None else os.environ
    if mode(selected_environment) == "off":
        return None

    selected_root = root or selected_environment.get("STAFF_NET_ROOT")
    if selected_root is None:
        raise StaffNetworkModeError(
            "STAFF_NET=analysis requires root=... or STAFF_NET_ROOT"
        )
    generation_root = Path(selected_root).resolve()
    receipt_path = generation_root / "network_build_receipt.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StaffNetworkModeError(
            f"network build receipt is unreadable: {receipt_path}"
        ) from exc
    if (
        receipt.get("schema_id") != "staffer-network-network-build-receipt-v1"
        or receipt.get("status") != "PASS"
        or receipt.get("canonical_identity_merges") != 0
    ):
        raise StaffNetworkModeError("network build receipt is not analysis-eligible")

    artifacts: dict[str, Path] = {}
    expected_roles = {"network_edges", "network_scores"}
    for file_receipt in receipt.get("files", []):
        role = file_receipt.get("role")
        if role not in expected_roles or role in artifacts:
            raise StaffNetworkModeError(f"unexpected or duplicate artifact role: {role}")
        artifact_path = (generation_root / str(file_receipt.get("path", ""))).resolve()
        if artifact_path.parent != generation_root or not artifact_path.is_file():
            raise StaffNetworkModeError(f"artifact is missing or escapes root: {role}")
        if artifact_path.stat().st_size != file_receipt.get("bytes"):
            raise StaffNetworkModeError(f"artifact byte count mismatch: {role}")
        digest = hashlib.sha256()
        with artifact_path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != file_receipt.get("sha256"):
            raise StaffNetworkModeError(f"artifact hash mismatch: {role}")
        artifacts[str(role)] = artifact_path
    if set(artifacts) != expected_roles:
        raise StaffNetworkModeError("receipt does not bind the complete analysis artifact set")

    return receipt, {
        "root": generation_root,
        "edges_path": artifacts["network_edges"],
        "scores_path": artifacts["network_scores"],
    }
