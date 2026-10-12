#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Refresh the canonical Congress.gov bill-ID discovery checkpoint.

This is a thin scheduled wrapper around the audited Advanced Search collector.
It discovers candidates only; it never ratifies, scores, or promotes them.
The Excel ledger sync records the output as pending until the existing human
adjudication workflow clears a bill.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import os as _bootstrap_os, sys as _bootstrap_sys
_bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
from paths import ROOT as ROOT_TEXT


ROOT = Path(ROOT_TEXT)
COLLECTOR = ROOT / "fec" / "collect" / "collect_congress_advanced_pages.mjs"
GOVINFO_FALLBACK = ROOT / "fec" / "legislation" / "govinfo_discovery_fallback.py"
REGISTRY = ROOT / "source_data" / "legislation" / "acu_legislation_search_registry.json"
DEFAULT_OUT = ROOT / "data" / "legislation" / "congress_advanced_current"
CYCLE_FILE = "discovery_refresh_cycle.json"


def _write_cycle(out_dir: Path, cycle: dict) -> None:
    target = out_dir / CYCLE_FILE
    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(cycle, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def _complete_manifest_hash(out_dir: Path) -> str | None:
    manifest = out_dir / "manifest.json"
    if not manifest.is_file():
        return None
    body = manifest.read_bytes()
    value = json.loads(body.decode("utf-8-sig"))
    return hashlib.sha256(body).hexdigest() if value.get("status") == "complete" else None


def prepare_discovery_cycle(out_dir: Path, registry: Path, categories: str | None) -> dict:
    """Resume only this collection cycle, never a completed prior query cache.

    The browser's --resume accepts old complete queries without contacting the
    source. Preserve those query/page artifacts outside its active cache before
    every new cycle. Canonical cumulative ledgers and GovInfo's version-bound
    cache stay in place so the fallback retains all previously discovered bills.
    A preparing marker makes an interrupted cache move safe to finish on retry.
    """
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    scope = {
        "registry_sha256": hashlib.sha256(registry.read_bytes()).hexdigest(),
        "categories": sorted(set(filter(None, (part.strip() for part in (categories or "").split(","))))),
    }
    state_path = out_dir / CYCLE_FILE
    cycle = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    if cycle and (
        cycle.get("schema") != "congress-discovery-refresh-cycle-v1"
        or cycle.get("status") not in {"preparing", "running", "complete"}
        or len(str(cycle.get("id", ""))) != 32
        or any(char not in "0123456789abcdef" for char in str(cycle.get("id", "")))
    ):
        raise RuntimeError("invalid Congress discovery refresh cycle marker")
    manifest_hash = _complete_manifest_hash(out_dir)
    completed_during_interruption = bool(
        cycle.get("status") == "running"
        and manifest_hash
        and manifest_hash != cycle.get("previous_manifest_sha256")
    )
    if (
        not cycle
        or cycle.get("status") == "complete"
        or cycle.get("scope") != scope
        or completed_during_interruption
    ):
        cycle = {
            "schema": "congress-discovery-refresh-cycle-v1",
            "id": uuid.uuid4().hex,
            "status": "preparing",
            "started_at": time.time(),
            "scope": scope,
            "previous_manifest_sha256": manifest_hash,
        }
        _write_cycle(out_dir, cycle)
    if cycle["status"] == "preparing":
        archive = out_dir / ".refresh_cycles" / cycle["id"]
        for relative in (Path("queries"), Path("raw") / "pages"):
            source = out_dir / relative
            destination = archive / relative
            if not source.exists():
                continue
            # Resolve both sides before moving a directory, including symlinks.
            source.resolve().relative_to(out_dir)
            destination.resolve().relative_to(out_dir)
            if destination.exists():
                raise RuntimeError(f"discovery cache changed during cycle preparation: {relative}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
        cycle["status"] = "running"
        _write_cycle(out_dir, cycle)
    return cycle


def complete_discovery_cycle(out_dir: Path, cycle: dict) -> None:
    manifest_hash = _complete_manifest_hash(out_dir)
    if not manifest_hash or manifest_hash == cycle.get("previous_manifest_sha256"):
        raise RuntimeError("Congress.gov discovery did not produce a new complete manifest")
    cycle.update(status="complete", completed_at=time.time(), manifest_sha256=manifest_hash)
    _write_cycle(out_dir, cycle)


def node_runtime() -> tuple[str, dict[str, str]]:
    environment = os.environ.copy()
    node_root = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node"
    bundled = node_root / "bin" / ("node.exe" if os.name == "nt" else "node")
    node = str(bundled) if bundled.is_file() else shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required for Congress.gov discovery")
    modules = node_root / "node_modules"
    if modules.is_dir() and not environment.get("NODE_PATH"):
        environment["NODE_PATH"] = str(modules)
    return node, environment


def latest_automatic_verification_failure(
    out_dir: Path, *, started_after: float
) -> Path | None:
    """Return this run's fail-closed verification receipt, never a stale one."""
    matches: list[Path] = []
    for path in out_dir.glob("manifest.partial.*.json"):
        try:
            if path.stat().st_mtime < started_after - 2:
                continue
            manifest = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if (
            manifest.get("status") == "failed"
            and (manifest.get("error") or {}).get("code")
            == "CONGRESS_AUTOMATIC_VERIFICATION_TIMEOUT"
            and (manifest.get("automatic_verification_timeout") or {}).get(
                "collection_stopped_without_result_acceptance"
            )
            is True
        ):
            matches.append(path)
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def active_cooldown_failure(out_dir: Path, *, now: float | None = None) -> Path | None:
    """Use the official fallback while an evidenced Congress cooldown remains active.

    This neither contacts Congress nor changes its cooldown. Require the exact
    immutable timeout receipt, its manifest hash, and the current no-interaction
    state to agree before reusing an earlier failure as fallback authorization.
    """
    out_dir = out_dir.resolve()
    now = time.time() if now is None else now
    try:
        state = json.loads((out_dir / "automatic_verification_cooldown.json").read_text(encoding="utf-8-sig"))
        observed = datetime.fromisoformat(state["observed_at"].replace("Z", "+00:00"))
        retry = datetime.fromisoformat(state["next_retry_not_before"].replace("Z", "+00:00"))
        policy = state.get("policy") or {}
        classification = state.get("classification") or {}
        if not (
            state.get("schema") == "congress-automatic-verification-cooldown-v1"
            and state.get("status") == "active"
            and observed.tzinfo is not None and retry.tzinfo is not None
            and observed.timestamp() <= now < retry.timestamp()
            and isinstance(state.get("consecutive_timeouts"), int)
            and state["consecutive_timeouts"] >= 1
            and isinstance(state.get("cooldown_ms"), (int, float))
            and state["cooldown_ms"] >= 0
            and round((retry - observed).total_seconds() * 1000) == state["cooldown_ms"]
            and policy.get("kind") == "exponential_backoff"
            and policy.get("reset_after_validated_result_page") is True
            and all(policy.get(key) is False for key in (
                "automatic_refresh_or_reload_during_cooldown", "captcha_bypass_allowed",
                "verification_interaction_allowed"))
            and classification.get("kind") == "automatic_security_verification"
            and all(classification.get(key) is False for key in (
                "interactive", "captcha_bypass_attempted", "verification_interaction_performed"))
        ):
            return None
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
    for path in sorted(out_dir.glob("manifest.partial.*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8-sig"))
            timeout = manifest.get("automatic_verification_timeout") or {}
            if not (
                manifest.get("schema") == "acu-congress-advanced-pages-manifest-v1"
                and manifest.get("status") == "failed"
                and (manifest.get("error") or {}).get("code") == "CONGRESS_AUTOMATIC_VERIFICATION_TIMEOUT"
                and timeout.get("classification") == "automatic_verification_timeout"
                and timeout.get("collection_stopped_without_result_acceptance") is True
                and timeout.get("interaction_performed") is False
                and timeout.get("captcha_bypass_attempted") is False
                and timeout.get("state_file") == "automatic_verification_cooldown.json"
                and all(timeout.get(key) == state.get(key) for key in (
                    "consecutive_timeouts", "cooldown_ms", "next_retry_not_before"))
            ):
                continue
            receipt = (out_dir / timeout["receipt_file"]).resolve()
            receipt.relative_to(out_dir)
            raw = receipt.read_bytes()
            if (
                hashlib.sha256(raw).hexdigest() == timeout.get("receipt_sha256")
                and json.loads(raw.decode("utf-8-sig")) == state
            ):
                return path
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--categories")
    parser.add_argument("--delay-ms", type=int, default=1500)
    parser.add_argument("--fallback-workers", type=int, default=8)
    parser.add_argument("--fallback-timeout-seconds", type=float, default=60.0)
    args = parser.parse_args()
    cycle = prepare_discovery_cycle(args.out, REGISTRY, args.categories)
    failure = active_cooldown_failure(args.out)
    environment = os.environ.copy()
    if failure is not None:
        print("Congress.gov verification cooldown remains active; honoring it without a browser retry.")
    else:
        node, environment = node_runtime()
        command = [node, str(COLLECTOR), "--registry", str(REGISTRY), "--out", str(args.out),
                   "--delay-ms", str(args.delay_ms), "--headless", "--resume"]
        if args.categories:
            command.extend(["--categories", args.categories])
        browser_started_at = time.time()
        completed = subprocess.run(command, cwd=ROOT, env=environment, check=False)
        if completed.returncode:
            failure = latest_automatic_verification_failure(
                args.out.resolve(), started_after=browser_started_at
            )
            if failure is None:
                raise SystemExit(completed.returncode)
    if failure is not None:
        fallback = [
            sys.executable,
            str(GOVINFO_FALLBACK),
            "--registry",
            str(REGISTRY),
            "--out",
            str(args.out),
            "--browser-failure",
            str(failure),
            "--workers",
            str(max(1, args.fallback_workers)),
            "--timeout-seconds",
            str(args.fallback_timeout_seconds),
        ]
        if args.categories:
            fallback.extend(["--categories", args.categories])
        print(
            "Congress.gov stopped at automatic security verification; "
            "running the official GovInfo pending-only discovery fallback."
        )
        fallback_completed = subprocess.run(
            fallback, cwd=ROOT, env=environment, check=False
        )
        if fallback_completed.returncode:
            raise SystemExit(fallback_completed.returncode)
    records = args.out / "congress_advanced_records.ndjson"
    if not records.is_file() or records.stat().st_size == 0:
        raise RuntimeError("Congress.gov discovery completed without a records ledger")
    complete_discovery_cycle(args.out, cycle)
    print(f"Canonical pending-only discovery checkpoint: {records}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
