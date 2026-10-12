from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import importlib.util


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "audits" / "staffer_network" / "match_lda_senate_payroll.py"
SPEC = importlib.util.spec_from_file_location("match_lda_senate_payroll", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _write_gzip(path: Path, rows: list[dict]) -> None:
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as stream:
            for row in rows:
                stream.write((json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_queue_name_candidates_include_bounded_compound_surname() -> None:
    assert ("MARIA", "DELACRUZ", ()) in MODULE.queue_name_candidates("MARIA DE LA CRUZ")
    assert ("MARIA", "CRUZ", ("DE", "LA")) in MODULE.queue_name_candidates("MARIA DE LA CRUZ")


def test_matcher_is_deterministic_and_never_promotes(tmp_path: Path) -> None:
    queue = tmp_path / "queue.ndjson.gz"
    payroll = tmp_path / "payroll.ndjson.gz"
    _write_gzip(queue, [{
        "display_name": "JANE Q PUBLIC",
        "lda_source_person_id": "lda:lobbyist:1",
    }])
    _write_gzip(payroll, [{
        "candidate_id": "senate:payroll-candidate:v1:abc",
        "date_intervals": [{"start": "2025-10-01", "end": "2026-03-31", "basis": "REPORT_PERIOD_ONLY"}],
        "office_raw": "SENATOR EXAMPLE",
        "parse_status": "HEURISTIC_SPLIT",
        "payee_name_raw": "PUBLIC, JANE Q",
        "source_pdf_sha256": "f" * 64,
        "source_pdf_url": "https://www.govinfo.gov/example.pdf",
        "title_candidate": "LEGISLATIVE ASSISTANT",
        "title_raw": "LEGISLATIVE ASSISTANT",
    }])
    hashes: list[str] = []
    for suffix in ("a", "b"):
        output = tmp_path / suffix
        subprocess.run(
            [sys.executable, str(SCRIPT), "--queue", str(queue), "--payroll", str(payroll), "--output-root", str(output)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        candidate_path = output / "lda_senate_payroll_match_candidates.ndjson.gz"
        hashes.append(_sha256(candidate_path))
        with gzip.open(candidate_path, "rt", encoding="utf-8") as stream:
            rows = [json.loads(line) for line in stream]
        assert len(rows) == 1
        assert rows[0]["candidate_status"] == "REVIEW_REQUIRED"
        assert rows[0]["canonical_identity_decision"] == "NOT_ADJUDICATED"
        assert rows[0]["canonical_employment_fact"] is False
        assert rows[0]["interval_continuity_asserted"] is False
    assert hashes[0] == hashes[1]
