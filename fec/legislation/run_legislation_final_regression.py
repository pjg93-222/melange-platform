#!/usr/bin/env python3
"""Run and hash-bind the complete legislation-focused regression suite.

The historical final-regression receipt recorded aggregate counts but not the
source/test inventory that produced them.  This runner records the complete
``fec`` implementation/test inventory before and after execution, compiles
every Python source without writing bytecode, adapts the repository's
dependency-free pytest-style functions to ``unittest``, runs every selected
Python module, syntax-checks every Node implementation, and runs every Node
test under ``fec/tests``.

This module performs no network, collection, scoring, promotion, production,
or mainframe operation.  Test fixtures use temporary directories.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import importlib
import importlib.metadata as importlib_metadata
import inspect
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

NODE_CHECK_TIMEOUT_SECONDS = 60
NODE_TEST_TIMEOUT_SECONDS = 1800


ROOT = Path(__file__).resolve().parents[2]
FEC = ROOT / "fec"
TEST_ROOT = FEC / "tests"
DEFAULT_OUTPUT_ROOT = (
    ROOT / "audits" / "legfix5" / "final_regression_current"
)
REGISTRY = (
    ROOT / "source_data" / "legislation" / "acu_legislation_search_registry.json"
)
STRICT_MANIFEST = (
    ROOT
    / "audits"
    / "legfix5"
    / "congress_advanced_pages_20260725"
    / "manifest.json"
)
CURRENTNESS_POINTER = (
    ROOT
    / "audits"
    / "legfix5"
    / "final_regression_currentness"
    / "current.json"
)

SCHEMA = "legislation-final-regression-current-v1"
POINTER_SCHEMA = "legislation-final-regression-current-pointer-v1"
CODE_SUFFIXES = {".py", ".mjs", ".cjs", ".ps1"}
TEST_KEYWORDS = (
    "legislation",
    "billstatus",
    "congress",
    "accepted",
    "rollcall",
    "formula_cache",
    "partial_review",
    "shadow",
    "postrefresh",
    "decay",
    "membership",
    "moneyball_manual_corroboration_roles",
    "materialize",
    "recompute_legislative",
    "official_text",
    "article_cross_reference",
    "acu_bill",
    "collector_resume",
    "named_outline",
)
EXTRA_TEST_STEMS = {
    "test_audit_completion_20260716",
    "test_bill_archive_provenance",
    "test_bill_audit_console_resolution",
    "test_bill_collision_guards",
    "test_bill_rules_routing",
    "test_legislative_alignment",
    "test_policy_repairs_20260718",
}
# Failures allowed to exist because they encode an unmet ACCEPTANCE gate
# rather than a code regression.
#
# These ids are MODULE-QUALIFIED and matched exactly.  The previous suffix
# match (``test_id.endswith(suffix)``) absolved an identically named
# class+method in any other module, so a brand-new failure could be laundered
# into the allowance simply by naming a test class the same thing.
#
# The residual count is also asserted: an allowance that grows (a new failure
# hiding inside it) or shrinks (a whitelisted test that now passes, meaning the
# allowance is stale) is itself an implementation regression, so the currentness
# auditor's ``actual_implementation_regression_count == 0`` check catches both.
KNOWN_ACCEPTANCE_BLOCKER_TEST_IDS = frozenset(
    {
        (
            "fec.tests.legislation.test_audit_legislation_decay_propagation."
            "DecayPropagationAuditTests."
            "test_artifact_is_hash_bound_and_workflow_snapshot_is_not_authority"
        ),
        (
            "fec.tests.legislation.test_audit_legislation_decay_propagation."
            "DecayPropagationAuditTests."
            "test_production_is_frozen_and_not_cut_over"
        ),
        (
            "fec.tests.legislation.test_audit_legislation_downstream_readiness."
            "DownstreamReadinessTests."
            "test_presync_decay_checks_staged_producers_not_live_cutover"
        ),
        (
            "fec.tests.legislation.test_run_legislation_presync_audit."
            "PresyncAuditTests."
            "test_decay_propagation_receipt_is_required_and_hash_current"
        ),
        (
            "fec.tests.legislation.test_run_legislation_presync_audit."
            "PresyncAuditTests."
            "test_decay_workflow_snapshot_values_are_not_gate_authority"
        ),
    }
)
EXPECTED_ACCEPTANCE_BLOCKER_FAILURE_COUNT = len(
    KNOWN_ACCEPTANCE_BLOCKER_TEST_IDS
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def stable_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_receipt(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def inventory() -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path in sorted(FEC.rglob("*"), key=lambda item: item.as_posix()):
        if (
            not path.is_file()
            or path.suffix.casefold() not in CODE_SUFFIXES
            or "__pycache__" in path.parts
        ):
            continue
        entries.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "scope": "all .py/.mjs/.cjs/.ps1 files recursively under fec",
        "entry_count": len(entries),
        "inventory_sha256": stable_sha256(entries),
        "entries": entries,
    }


def selected_python_tests() -> list[Path]:
    selected: list[Path] = []
    for path in TEST_ROOT.rglob("test_*.py"):
        stem = path.stem.casefold()
        if stem in EXTRA_TEST_STEMS or any(
            keyword in stem for keyword in TEST_KEYWORDS
        ):
            selected.append(path.resolve())
    return sorted(selected, key=lambda item: item.as_posix())


def selected_node_tests() -> list[Path]:
    return sorted(
        (path.resolve() for path in TEST_ROOT.rglob("test_*.mjs")),
        key=lambda item: item.as_posix(),
    )


class MiniMonkeyPatch:
    """The narrow ``monkeypatch.setattr`` surface used by this test corpus."""

    def __init__(self) -> None:
        self._undo: list[tuple[Any, str, Any]] = []

    def setattr(self, target: Any, name: str, value: Any) -> None:
        self._undo.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def undo(self) -> None:
        while self._undo:
            target, name, prior = self._undo.pop()
            setattr(target, name, prior)


class FixtureFunctionTestCase(unittest.TestCase):
    def __init__(self, function: Callable[..., Any]) -> None:
        super().__init__("runTest")
        self.function = function

    def id(self) -> str:
        return f"{self.function.__module__}.{self.function.__name__}"

    def shortDescription(self) -> str | None:
        return self.id()

    def runTest(self) -> None:
        signature = inspect.signature(self.function)
        unexpected = set(signature.parameters) - {"tmp_path", "monkeypatch"}
        if unexpected:
            self.fail(f"unsupported fixture parameter(s): {sorted(unexpected)}")
        patch = MiniMonkeyPatch()
        try:
            with tempfile.TemporaryDirectory(
                prefix="legislation-regression-",
                dir=os.environ.get("TEMP") or None,
            ) as temporary:
                kwargs: dict[str, Any] = {}
                if "tmp_path" in signature.parameters:
                    kwargs["tmp_path"] = Path(temporary)
                if "monkeypatch" in signature.parameters:
                    kwargs["monkeypatch"] = patch
                self.function(**kwargs)
        finally:
            patch.undo()


def compile_python_sources() -> dict[str, Any]:
    paths = sorted(FEC.rglob("*.py"), key=lambda item: item.as_posix())
    failures: list[dict[str, Any]] = []
    for path in paths:
        try:
            compile(path.read_bytes(), str(path), "exec")
        except Exception as exc:  # pragma: no cover - evidence path
            failures.append(
                {
                    "path": path.relative_to(ROOT).as_posix(),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return {
        "file_count": len(paths),
        "passed": len(paths) - len(failures),
        "failed": len(failures),
        "failures": failures,
        "bytecode_written": False,
    }


def check_node_sources(node: Path) -> dict[str, Any]:
    paths = sorted(
        (
            path
            for path in FEC.rglob("*")
            if path.is_file() and path.suffix.casefold() in {".mjs", ".cjs"}
        ),
        key=lambda item: item.as_posix(),
    )
    failures: list[dict[str, Any]] = []
    for path in paths:
        try:
            completed = subprocess.run(
                [str(node), "--check", str(path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=NODE_CHECK_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            failures.append(
                {
                    "path": path.relative_to(ROOT).as_posix(),
                    "exit_code": -1,
                    "output": (
                        f"TIMEOUT after {NODE_CHECK_TIMEOUT_SECONDS}s: "
                        "node --check did not finish"
                    ),
                }
            )
            continue
        if completed.returncode:
            failures.append(
                {
                    "path": path.relative_to(ROOT).as_posix(),
                    "exit_code": completed.returncode,
                    "output": (
                        (completed.stdout or "") + (completed.stderr or "")
                    )[-4000:],
                }
            )
    return {
        "executable": file_receipt(node),
        "file_count": len(paths),
        "passed": len(paths) - len(failures),
        "failed": len(failures),
        "failures": failures,
    }


def build_python_suite(paths: list[Path]) -> tuple[unittest.TestSuite, dict[str, int]]:
    suite = unittest.TestSuite()
    class_test_count = 0
    function_test_count = 0
    for path in paths:
        module_name = ".".join(
            path.relative_to(ROOT).with_suffix("").parts
        )
        try:
            module = importlib.import_module(module_name)
        except Exception:
            # An unimportable test module becomes a failing (_FailedTest)
            # case carrying the traceback, so it is counted and receipted.
            loaded = unittest.defaultTestLoader.loadTestsFromName(module_name)
            class_test_count += loaded.countTestCases()
            suite.addTests(loaded)
            continue
        loaded = unittest.defaultTestLoader.loadTestsFromModule(module)
        class_test_count += loaded.countTestCases()
        suite.addTests(loaded)
        for _, function in inspect.getmembers(module, inspect.isfunction):
            if (
                function.__module__ == module.__name__
                and function.__name__.startswith("test_")
            ):
                suite.addTest(FixtureFunctionTestCase(function))
                function_test_count += 1
    return suite, {
        "class_test_count": class_test_count,
        "function_test_count": function_test_count,
        "total_test_count": class_test_count + function_test_count,
    }


def classify_python_failures(
    failed_ids: set[str],
    error_ids: set[str] = frozenset(),
) -> dict[str, Any]:
    """Split failures into the acceptance allowance and real regressions.

    The allowance is matched by exact, module-qualified test id, and its size
    is asserted against the expected residual so a NEW failure cannot hide
    inside it and a STALE entry cannot silently keep absolving nothing.
    """

    # Only assertion failures may use the allowance; a test that ERRORS
    # (crash, ImportError) is always an implementation regression.
    errored = set(error_ids)
    observed = set(failed_ids) - errored
    acceptance = sorted(observed & KNOWN_ACCEPTANCE_BLOCKER_TEST_IDS)
    implementation = sorted((observed | errored) - set(acceptance))
    stale = sorted(KNOWN_ACCEPTANCE_BLOCKER_TEST_IDS - observed)
    allowance_current = (
        len(acceptance) == EXPECTED_ACCEPTANCE_BLOCKER_FAILURE_COUNT
        and not stale
    )
    return {
        "expected_acceptance_blocker_failures": acceptance,
        "implementation_regression_failures": implementation,
        "acceptance_blocker_allowance": sorted(
            KNOWN_ACCEPTANCE_BLOCKER_TEST_IDS
        ),
        "expected_acceptance_blocker_failure_count": (
            EXPECTED_ACCEPTANCE_BLOCKER_FAILURE_COUNT
        ),
        "observed_acceptance_blocker_failure_count": len(acceptance),
        "stale_acceptance_blocker_allowances": stale,
        "acceptance_blocker_allowance_current": allowance_current,
    }


def run_python_tests(paths: list[Path]) -> dict[str, Any]:
    suite, counts = build_python_suite(paths)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
    failures = [
        {"test": test.id(), "traceback": traceback}
        for test, traceback in result.failures
    ]
    errors = [
        {"test": test.id(), "traceback": traceback}
        for test, traceback in result.errors
    ]
    classification = classify_python_failures(
        {row["test"] for row in failures},
        {row["test"] for row in errors},
    )
    return {
        "module_count": len(paths),
        "modules": [path.relative_to(ROOT).as_posix() for path in paths],
        **counts,
        "tests_run": result.testsRun,
        "passed": result.testsRun
        - len(result.failures)
        - len(result.errors)
        - len(result.skipped),
        "failed": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        **classification,
        "failure_details": failures,
        "error_details": errors,
        "runner_output_tail": stream.getvalue()[-20000:],
    }


def parse_node_summary(output: str) -> dict[str, int | None]:
    def value(label: str) -> int | None:
        match = re.search(rf"(?:^|\n).*{label}\s+(\d+)\s*(?:\n|$)", output)
        return int(match.group(1)) if match else None

    return {
        "tests": value("tests"),
        "passed": value("pass"),
        "failed": value("fail"),
        "skipped": value("skipped"),
    }


def run_node_tests(node: Path, paths: list[Path]) -> dict[str, Any]:
    node_modules = Path(
        os.environ.get(
            "LEGISLATION_NODE_MODULES",
            (
                "C:/Users/pgoff/.cache/codex-runtimes/"
                "codex-primary-runtime/dependencies/node/node_modules"
            ),
        )
    ).resolve()
    environment = os.environ.copy()
    environment["NODE_PATH"] = str(node_modules)
    try:
        completed = subprocess.run(
            [str(node), "--test", *(str(path) for path in paths)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=NODE_TEST_TIMEOUT_SECONDS,
        )
        returncode = completed.returncode
        output = (completed.stdout or "") + (completed.stderr or "")
    except subprocess.TimeoutExpired as exc:
        returncode = -1
        partial = exc.stdout or b""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        output = (
            partial
            + f"\nTIMEOUT after {NODE_TEST_TIMEOUT_SECONDS}s: "
            "node --test did not finish\n"
        )
    matcher = re.search(
        r"official-text matcher behavior:\s*(\d+)\s+PASS",
        output,
    )
    return {
        "executable": file_receipt(node),
        "node_modules": str(node_modules),
        "playwright_package": file_receipt(
            node_modules / "playwright" / "package.json"
        ),
        "module_count": len(paths),
        "modules": [path.relative_to(ROOT).as_posix() for path in paths],
        "exit_code": returncode,
        "summary": parse_node_summary(output),
        "official_text_matcher_internal_passed": (
            int(matcher.group(1)) if matcher else None
        ),
        "output_tail": output[-20000:],
    }


def node_version(node: Path) -> str:
    try:
        return subprocess.run(
            [str(node), "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=NODE_CHECK_TIMEOUT_SECONDS,
        ).stdout.strip()
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {NODE_CHECK_TIMEOUT_SECONDS}s"


def input_address(
    before_inventory: dict[str, Any],
    python_tests: list[Path],
    node_tests: list[Path],
    node: Path,
) -> dict[str, Any]:
    node_modules = Path(
        os.environ.get(
            "LEGISLATION_NODE_MODULES",
            (
                "C:/Users/pgoff/.cache/codex-runtimes/"
                "codex-primary-runtime/dependencies/node/node_modules"
            ),
        )
    ).resolve()
    dependency_names = (
        "lxml",
        "networkx",
        "numpy",
        "openpyxl",
        "pandas",
        "python-docx",
        "scipy",
    )
    dependencies: dict[str, str | None] = {}
    for name in dependency_names:
        try:
            dependencies[name] = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            dependencies[name] = None
    return {
        "schema": SCHEMA,
        "runner": file_receipt(Path(__file__)),
        "implementation_test_inventory_sha256": before_inventory[
            "inventory_sha256"
        ],
        "implementation_test_inventory_entry_count": before_inventory[
            "entry_count"
        ],
        "python_test_modules": [
            file_receipt(path) for path in python_tests
        ],
        "node_test_modules": [file_receipt(path) for path in node_tests],
        "python": {
            "executable": file_receipt(Path(sys.executable)),
            "version": sys.version,
            "sys_path": list(sys.path),
            "pythonpath": os.environ.get("PYTHONPATH"),
            "dependencies": dependencies,
        },
        "node": {
            "executable": file_receipt(node),
            "version": node_version(node),
            "node_modules": str(node_modules),
            "playwright_package": file_receipt(
                node_modules / "playwright" / "package.json"
            ),
        },
        "temporary_directory_policy": {
            "root": "C:/tmp/legislation-final-regression",
            "same_volume_as_system_test_runtime": True,
            "production_path": False,
        },
        "search_registry": file_receipt(REGISTRY),
        "strict_manifest": file_receipt(STRICT_MANIFEST),
        "prior_currentness_pointer": (
            file_receipt(CURRENTNESS_POINTER)
            if CURRENTNESS_POINTER.is_file()
            else None
        ),
    }


def run(output_root: Path, node: Path) -> tuple[dict[str, Any], Path]:
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    python_tests = selected_python_tests()
    node_tests = selected_node_tests()
    before = inventory()
    address = input_address(before, python_tests, node_tests, node)
    run_id = stable_sha256(address)
    final_dir = output_root.resolve() / "runs" / run_id
    if final_dir.exists():
        raise FileExistsError(f"immutable regression run already exists: {final_dir}")

    temporary_parent = Path("C:/tmp/legislation-final-regression")
    temporary_parent.mkdir(parents=True, exist_ok=True)
    prior_tempdir = tempfile.tempdir
    prior_temp = os.environ.get("TEMP")
    prior_tmp = os.environ.get("TMP")
    with tempfile.TemporaryDirectory(
        prefix="current-regression-",
        dir=temporary_parent,
        ignore_cleanup_errors=True,
    ) as temporary_name:
        tempfile.tempdir = temporary_name
        os.environ["TEMP"] = temporary_name
        os.environ["TMP"] = temporary_name
        try:
            compilation = compile_python_sources()
            node_syntax = check_node_sources(node)
            python_result = run_python_tests(python_tests)
            node_result = run_node_tests(node, node_tests)
        finally:
            tempfile.tempdir = prior_tempdir
            if prior_temp is None:
                os.environ.pop("TEMP", None)
            else:
                os.environ["TEMP"] = prior_temp
            if prior_tmp is None:
                os.environ.pop("TMP", None)
            else:
                os.environ["TMP"] = prior_tmp
    after = inventory()
    inventory_stable = before["inventory_sha256"] == after["inventory_sha256"]
    # An allowance that no longer matches its expected residual is itself a
    # regression: either a new failure is hiding inside it, or a whitelisted
    # test now passes and the allowance is stale.  Folding it in here is what
    # makes the downstream currentness auditor's
    # ``actual_implementation_regression_count == 0`` check load bearing.
    allowance_current = bool(
        python_result["acceptance_blocker_allowance_current"]
    )
    implementation_failures = (
        compilation["failed"]
        + node_syntax["failed"]
        + len(python_result["implementation_regression_failures"])
        + int(node_result["exit_code"] != 0)
        + int(not inventory_stable)
        + int(not allowance_current)
    )
    acceptance_failures = len(
        python_result["expected_acceptance_blocker_failures"]
    )
    if implementation_failures:
        status = "FAIL_IMPLEMENTATION_REGRESSIONS"
    elif acceptance_failures:
        status = "BLOCKED_ACCEPTANCE_GATES_NO_IMPLEMENTATION_REGRESSIONS"
    else:
        status = "PASS_CURRENT"

    receipt = {
        "schema": SCHEMA,
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "input_address": address,
        "source_test_inventory_before": before,
        "source_test_inventory_after": after,
        "source_test_inventory_stable": inventory_stable,
        "compilation": compilation,
        "node_syntax": node_syntax,
        "python_regression_suite": python_result,
        "node_regression_suite": node_result,
        "actual_implementation_regression_count": implementation_failures,
        "explicit_acceptance_blocker_failure_count": acceptance_failures,
        "expected_acceptance_blocker_failure_count": (
            EXPECTED_ACCEPTANCE_BLOCKER_FAILURE_COUNT
        ),
        "acceptance_blocker_allowance_current": allowance_current,
        "stale_acceptance_blocker_allowances": python_result[
            "stale_acceptance_blocker_allowances"
        ],
        "authority": {
            "primary_discovery_instrument": (
                "Congress.gov Advanced Search - Legislation"
            ),
            "quorum_used": False,
            "test_currentness_authority": implementation_failures == 0,
            "membership_disposition_authority": False,
            "scoring_authority": False,
            "sync_or_promotion_authority": False,
        },
        "safety": {
            "network_executed": False,
            "collector_process_modified": False,
            "candidate_exclusion_performed": False,
            "scoring_performed": False,
            "production_write_performed": False,
            "sync_or_promotion_performed": False,
        },
    }

    final_dir.mkdir(parents=True)
    receipt_path = final_dir / "receipt.json"
    atomic_json(receipt_path, receipt)
    pointer = {
        "schema": POINTER_SCHEMA,
        "status": status,
        "run_id": run_id,
        "runner": file_receipt(Path(__file__)),
        "receipt": file_receipt(receipt_path),
        "source_test_inventory_sha256": before["inventory_sha256"],
        "source_test_inventory_stable": inventory_stable,
        "actual_implementation_regression_count": implementation_failures,
        "explicit_acceptance_blocker_failure_count": acceptance_failures,
        "expected_acceptance_blocker_failure_count": (
            EXPECTED_ACCEPTANCE_BLOCKER_FAILURE_COUNT
        ),
        "acceptance_blocker_allowance_current": allowance_current,
        "network_executed": False,
        "scoring_performed": False,
        "production_write_performed": False,
        "sync_or_promotion_performed": False,
    }
    atomic_json(output_root.resolve() / "current.json", pointer)
    return pointer, receipt_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--node",
        type=Path,
        default=Path(
            "C:/Users/pgoff/.cache/codex-runtimes/"
            "codex-primary-runtime/dependencies/node/bin/node.exe"
        ),
    )
    parser.add_argument("--list-tests", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.list_tests:
        print(
            json.dumps(
                {
                    "python": [
                        path.relative_to(ROOT).as_posix()
                        for path in selected_python_tests()
                    ],
                    "node": [
                        path.relative_to(ROOT).as_posix()
                        for path in selected_node_tests()
                    ],
                },
                indent=2,
            )
        )
        return 0
    pointer, receipt_path = run(args.output_root, args.node.resolve())
    print(
        json.dumps(
            {
                "status": pointer["status"],
                "run_id": pointer["run_id"],
                "receipt": pointer["receipt"],
                "pointer": file_receipt(args.output_root.resolve() / "current.json"),
                "actual_implementation_regression_count": pointer[
                    "actual_implementation_regression_count"
                ],
                "explicit_acceptance_blocker_failure_count": pointer[
                    "explicit_acceptance_blocker_failure_count"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if pointer["actual_implementation_regression_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
