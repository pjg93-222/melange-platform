from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "fec" / "legislation" / "run_legislation_claude_ambiguity_review.py"
SPEC = importlib.util.spec_from_file_location("claude_ambiguity_review", MODULE_PATH)
assert SPEC and SPEC.loader
REVIEW = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = REVIEW
SPEC.loader.exec_module(REVIEW)
SHA = "a" * 64


def queue_row(*, missing_summary: bool = False, triage_status: str = "flagged"):
    summary = {
        "status": "available",
        "text": "The bill amends the Federal Credit Union Act.",
        "text_sha256": SHA,
        "source_url": "https://www.congress.gov/bill/119th-congress/house-bill/1",
    }
    if missing_summary:
        summary = {"status": "unknown"}
    return {
        "schema": REVIEW.INPUT_SCHEMA,
        "canonical_key": "119:hr:1",
        "query_id": "credit-union-charter",
        "triage_status": triage_status,
        "candidate_preserved": True,
        "category": {
            "category_id": "charter",
            "name": "Credit union charter and field of membership",
            "identifier_terms": ["Federal Credit Union Act", "field of membership"],
        },
        "title": {
            "status": "available",
            "text": "Credit Union Modernization Act",
            "text_sha256": SHA,
            "source_url": "https://www.congress.gov/bill/119th-congress/house-bill/1",
        },
        "official_summary": summary,
        "full_text": {
            "inventory": {
                "expected_version_count": 2,
                "reviewed_version_count": 2,
                "all_versions_reviewed": True,
            },
            "versions": [
                {
                    "version_code": "ih",
                    "body_sha256": SHA,
                    "source_url": "https://www.congress.gov/x/ih",
                    "excerpts": [
                        {
                            "text": "Section 101 of the Federal Credit Union Act is amended.",
                            "locator": "sec. 2",
                        },
                        {
                            "text": "The field of membership may include underserved areas.",
                            "locator": "sec. 3",
                        },
                    ],
                },
                {
                    "version_code": "eh",
                    "body_sha256": SHA,
                    "source_url": "https://www.congress.gov/x/eh",
                    "excerpts": [
                        {
                            "text": "The amendments made by this Act take effect immediately.",
                            "locator": "sec. 5",
                        }
                    ],
                },
            ],
        },
        "identifier_excerpts": [
            {
                "identifier": "Federal Credit Union Act",
                "text": "Section 101 of the Federal Credit Union Act is amended.",
            },
            {
                "identifier": "field of membership",
                "text": "The field of membership may include underserved areas.",
            },
        ],
        "stage": {
            "value": "passed_house",
            "actions": [
                {"date": "2026-01-01", "text": "Introduced in House"},
                {"date": "2026-02-01", "text": "Passed House"},
            ],
        },
        "relationships": {
            "role_provenance_complete": True,
            "sponsors": [{"name": "Rep. Sponsor", "bioguide_id": "S000001"}],
            "original_cosponsors": [
                {"name": "Rep. Original", "bioguide_id": "O000001"}
            ],
            "cosponsors": [{"name": "Rep. Later", "bioguide_id": "L000001"}],
        },
    }


def model_decision(
    disposition: str = "keep",
    *,
    confidence: str = "high",
    evidence_ids=None,
    full_text_complete: bool = True,
    absence_used: bool = False,
):
    return {
        "disposition": disposition,
        "confidence": confidence,
        "reason": "Official bill text directly resolves this category membership.",
        "reason_code": "official_text_category_match",
        "evidence_ids": evidence_ids
        if evidence_ids is not None
        else ["official_title", "full_text:ih:0"],
        "full_text_rule_out_complete": full_text_complete,
        "absence_used_as_exclusion_evidence": absence_used,
        "uncertainty": "",
        "matched_category_basis": "The operative provision directly amends the named law.",
    }


@dataclass
class FakeTransport:
    decision: dict
    calls: int = 0

    def review(self, **kwargs):
        self.calls += 1
        return REVIEW.ProviderResult(
            output=self.decision,
            response_metadata={
                "provider_response_id": f"fake-{self.calls}",
                "provider_model": kwargs["model"],
            },
            input_tokens=100,
            output_tokens=50,
            cost_usd=Decimal("0.001"),
        )


def write_queue(path: Path, rows) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def arguments(queue: Path, out: Path, run_id: str = "test-run"):
    return REVIEW.parse_args(
        [
            "--input",
            str(queue),
            "--out",
            str(out),
            "--run-id",
            run_id,
            "--provider",
            "anthropic-api",
            "--max-cost-usd",
            "1.00",
            "--workers",
            "2",
        ]
    )


def test_prompt_contains_every_required_evidence_surface_without_clipping():
    prepared = REVIEW.validate_queue([queue_row()])[0]
    document, prompt = REVIEW.prompt_document(prepared)
    envelope = document["evidence_envelope"]
    assert envelope["official_title"]["text"] in prompt
    assert envelope["official_summary"]["text"] in prompt
    assert len(envelope["full_text"]["versions"]) == 2
    assert sum(len(row["excerpts"]) for row in envelope["full_text"]["versions"]) == 3
    assert len(envelope["identifier_excerpts"]) == 2
    assert len(envelope["stage_and_actions"]["actions"]) == 2
    assert len(envelope["relationship_roles"]["sponsors"]) == 1
    assert len(envelope["relationship_roles"]["original_cosponsors"]) == 1
    assert len(envelope["relationship_roles"]["cosponsors"]) == 1
    assert envelope["all_supplied_evidence_included"] is True


def test_non_flagged_queue_is_rejected_before_dispatch():
    with pytest.raises(REVIEW.ReviewError, match="not flagged/ambiguous"):
        REVIEW.validate_queue([queue_row(triage_status="keep")])


def test_missing_evidence_never_calls_provider_and_never_becomes_junk():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        queue = base / "queue.ndjson"
        write_queue(queue, [queue_row(missing_summary=True)])
        transport = FakeTransport(model_decision("junk"))
        summary = REVIEW.run(arguments(queue, base / "out"), transport=transport)
        assert transport.calls == 0
        assert summary["effective_dispositions"] == {
            "flagged": 1,
            "junk": 0,
            "keep": 0,
        }
        receipt = json.loads(
            next((base / "out" / "runs" / "test-run" / "receipts").glob("*.json"))
            .read_text(encoding="utf-8")
        )
        assert receipt["status"] == "missing_evidence_flagged"
        assert receipt["provider_call_performed"] is False
        assert receipt["effective_decision"]["disposition"] == "flagged"


@pytest.mark.parametrize(
    "decision, downgrade_reason",
    [
        (model_decision("junk", confidence="medium"), "junk_requires_high_confidence"),
        (
            model_decision("junk", full_text_complete=False),
            "junk_requires_complete_full_text_rule_out",
        ),
        (
            model_decision("junk", absence_used=True),
            "absence_cannot_support_exclusion",
        ),
        (
            model_decision("junk", evidence_ids=["official_title"]),
            "junk_requires_official_full_text_citation",
        ),
    ],
)
def test_unsafe_junk_is_downgraded_to_flagged(decision, downgrade_reason):
    validated = REVIEW.validate_model_output(
        decision,
        {"official_title", "full_text:ih:0"},
    )
    effective = REVIEW.policy_effective_decision(
        validated,
        missing_evidence=[],
    )
    assert effective["disposition"] == "flagged"
    assert downgrade_reason in effective["uncertainty"]


def test_strict_output_rejects_extra_fields_and_unknown_citations():
    extra = {**model_decision(), "not_in_schema": True}
    with pytest.raises(REVIEW.ReviewError, match="fields differ"):
        REVIEW.validate_model_output(
            extra, {"official_title", "full_text:ih:0"}
        )
    unknown = model_decision(evidence_ids=["invented"])
    with pytest.raises(REVIEW.ReviewError, match="unknown evidence"):
        REVIEW.validate_model_output(
            unknown, {"official_title", "full_text:ih:0"}
        )


def test_run_is_resumable_and_has_one_terminal_receipt_per_dispatch():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        queue = base / "queue.ndjson"
        rows = [
            queue_row(),
            {**queue_row(), "query_id": "second-category"},
        ]
        write_queue(queue, rows)
        transport = FakeTransport(model_decision())
        args = arguments(queue, base / "out")
        first = REVIEW.run(args, transport=transport)
        assert transport.calls == 2
        second = REVIEW.run(args, transport=transport)
        assert transport.calls == 2
        assert first["proposal_generation_sha256"] == second[
            "proposal_generation_sha256"
        ]
        run_dir = base / "out" / "runs" / "test-run"
        assert len(list((run_dir / "dispatches").glob("*.json"))) == 2
        assert len(list((run_dir / "receipts").glob("*.json"))) == 2
        assert second["one_terminal_receipt_per_dispatch"] is True


def test_call_start_without_receipt_is_terminalized_without_replay():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        queue = base / "queue.ndjson"
        write_queue(queue, [queue_row()])
        args = arguments(queue, base / "out")
        prepared = REVIEW.validate_queue([queue_row()])[0]
        job = REVIEW.make_job(
            prepared,
            model=args.model,
            provider=args.provider,
            effort=args.effort,
            max_output_tokens=args.max_output_tokens,
            input_cost_per_million=args.input_rate,
            output_cost_per_million=args.output_rate,
        )
        run_dir = (base / "out" / "runs" / "test-run").resolve()
        REVIEW.persist_dispatch_and_prompt(run_dir, job, args)
        paths = REVIEW.artifact_paths(run_dir, job.dispatch_id)
        REVIEW.exclusive_write(
            paths["call"],
            {
                "schema": REVIEW.CALL_SCHEMA,
                "dispatch_id": job.dispatch_id,
                "simulated_crash_after_this_marker": True,
            },
        )
        transport = FakeTransport(model_decision())
        summary = REVIEW.run(args, transport=transport)
        assert transport.calls == 0
        receipt = json.loads(paths["receipt"].read_text(encoding="utf-8"))
        assert receipt["status"] == "indeterminate_prior_call_flagged"
        assert receipt["effective_decision"]["disposition"] == "flagged"
        assert summary["one_terminal_receipt_per_dispatch"] is True


def test_cost_cap_blocks_provider_without_retry_waits():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        queue = base / "queue.ndjson"
        write_queue(queue, [queue_row()])
        args = REVIEW.parse_args(
            [
                "--input",
                str(queue),
                "--out",
                str(base / "out"),
                "--run-id",
                "cost-cap",
                "--max-cost-usd",
                "0",
            ]
        )
        transport = FakeTransport(model_decision())
        summary = REVIEW.run(args, transport=transport)
        assert transport.calls == 0
        assert summary["terminal_statuses"] == {"cost_cap_flagged": 1}
        assert summary["accounted_cost_usd"] == "0.000000"


def test_secret_redaction_never_persists_api_key_shape():
    redacted = REVIEW.redact_secrets(
        "request failed ANTHROPIC_API_KEY=sk-ant-secret-example-123456"
    )
    assert "sk-ant-" not in redacted
    assert "ANTHROPIC_API_KEY=" not in redacted


def run_cli_transport(monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen.update(command=command, kwargs=kwargs)
        envelope = {
            "structured_output": model_decision(),
            "usage": {"input_tokens": 7, "output_tokens": 3},
            "total_cost_usd": 0.002,
            "session_id": "cli-1",
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(envelope), stderr="")

    monkeypatch.setattr(REVIEW.subprocess, "run", fake_run)
    result = REVIEW.ClaudeCliTransport("claude").review(
        system_prompt="SYSTEM POLICY",
        user_prompt="EVIDENCE TEXT",
        schema={"type": "object"},
        model="sonnet",
        effort="low",
        max_output_tokens=100,
        remaining_budget_usd=Decimal("0.5"),
    )
    return seen, result


def test_cli_transport_is_locked_down_and_runs_outside_the_repo(monkeypatch):
    seen, result = run_cli_transport(monkeypatch)
    command, kwargs = seen["command"], seen["kwargs"]
    for flag in ("--safe-mode", "--disable-slash-commands", "--tools", "--strict-mcp-config", "--mcp-config",
                 "--no-session-persistence", "--no-chrome", "--permission-mode"):
        assert flag in command, flag
    assert command[command.index("--tools") + 1] == ""
    assert json.loads(command[command.index("--mcp-config") + 1]) == {"mcpServers": {}}
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    cwd = Path(kwargs["cwd"]).resolve()
    assert cwd == Path(tempfile.gettempdir()).resolve()
    assert cwd != ROOT and ROOT not in cwd.parents
    assert kwargs["shell"] is False
    # Legitimate use is unchanged: evidence goes over stdin, never argv; the structured result parses.
    assert kwargs["input"] == "EVIDENCE TEXT" and "EVIDENCE TEXT" not in command
    assert command[command.index("--system-prompt") + 1] == "SYSTEM POLICY"
    assert result.output == model_decision()
    assert (result.input_tokens, result.output_tokens) == (7, 3)
    assert result.cost_usd == Decimal("0.002")


def test_cli_transport_lockdown_flags_match_the_classifier_runtime_sibling(monkeypatch):
    import classifier_runtime

    classifier = {}

    def fake_run(command, **kwargs):
        classifier["command"] = command
        return SimpleNamespace(returncode=0, stdout=json.dumps({"structured_output": {"ok": 1}}), stderr="")

    monkeypatch.setenv("MELANGE_CLASSIFIER_COMMAND", "claude")
    monkeypatch.setattr(classifier_runtime.subprocess, "run", fake_run)
    classifier_runtime.classify_json("Policy", "Evidence", {"type": "object"})
    sibling = classifier["command"]
    lockdown = sibling[sibling.index("-p") + 1 : sibling.index("--output-format")]
    assert "--safe-mode" in lockdown and "--tools" in lockdown  # the slice really is the lockdown set
    seen, _ = run_cli_transport(monkeypatch)
    command = seen["command"]
    assert command[command.index("-p") + 1 : command.index("--output-format")] == lockdown
