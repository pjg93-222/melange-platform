import gzip
import hashlib
import json
from contextlib import contextmanager
from datetime import date
from pathlib import Path
import sys
import tempfile
import unittest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from staffer_network.employment_history import (
    CanonicalEmploymentProvenance,
    EmploymentHistoryError,
    audit_history_corpus,
    audit_history_completeness,
    employment_episode_digest,
    load_canonical_employment_provenance,
    build_legistorm_employment_checkpoint,
    build_lookup_queue,
    build_temporal_sidecar,
    co_tenure_bounds,
    normalize_source_date,
    transition_gap_bounds,
    union_ranges,
    _latest_checkpoint_observation,
)
from staffer_network.source_manifest import canonical_json_bytes, sha256_file
from staffer_network.schema_validation import (
    SchemaValidationError,
    validate_instance,
)


def _write_gzip(path: Path, rows: list[dict]) -> None:
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as stream:
            for row in rows:
                stream.write(canonical_json_bytes(row))


def _digest(values: list[str]) -> str:
    return hashlib.sha256("\0".join(sorted(values)).encode("utf-8")).hexdigest()


def _attestation_metadata() -> dict:
    return {
        "reviewer_kind": "independent-human",
        "reviewer_id": "reviewer:fixture",
        "rationale": "Fixture establishes exhaustive employment coverage.",
        "attested_at": "2026-08-31T12:00:00Z",
    }


@contextmanager
def _verified_episode_fixture(captures: list[dict] | None = None):
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        captures_path = root / "captures.ndjson.gz"
        checkpoint = root / "checkpoint"
        _write_gzip(captures_path, captures or [_gordon_captures()[0]])
        build_legistorm_employment_checkpoint(captures_path, checkpoint)
        generation = _current_generation(checkpoint)
        receipt_path = generation / "legistorm_employment_checkpoint_receipt.json"
        provenance = load_canonical_employment_provenance(
            receipt_path, expected_sha256=sha256_file(receipt_path)
        )
        with gzip.open(
            generation / "legistorm_employment_episodes.ndjson.gz",
            "rt", encoding="utf-8",
        ) as stream:
            episodes = [json.loads(line) for line in stream if line.strip()]
        yield episodes, provenance


def _auditable_episode(**fields) -> dict:
    return {
        "schema_id": "staffer-network-employment-episode-v1",
        "episode_status": "ACTIVE",
        "identity_status": "ACCEPTED",
        "container_status": "ACCEPTED",
        **fields,
    }


def _episode(
    episode_id: str,
    person: str,
    container: str,
    kind: str,
    start_earliest: str,
    start_latest: str,
    end_earliest: str,
    end_latest: str,
    *,
    member: str | None = None,
    organization: str | None = None,
) -> dict:
    support = ["obs:" + episode_id]
    return {
        "schema_id": "staffer-network-employment-episode-v1",
        "episode_id": episode_id,
        "resolved_person_id": person,
        "normalized_container_id": container,
        "container_kind": kind,
        "member_bioguide_id": member,
        "organization_id": organization,
        "title": "Fixture role",
        "start_earliest": start_earliest,
        "start_latest": start_latest,
        "end_earliest": end_earliest,
        "end_latest": end_latest,
        "start_precision": "day",
        "end_precision": "day",
        "asserted_current": False,
        "source_as_of": "2026-08-31",
        "identity_decision_id": "identity:" + person,
        "identity_status": "ACCEPTED",
        "container_decision_id": "container:" + container,
        "container_status": "ACCEPTED",
        "support_observation_ids": support,
        "support_digest": _digest(support),
        "episode_status": "ACTIVE",
    }


def _gordon_capture(
    source_row_id: str,
    employer: str,
    title: str,
    raw_start: str,
    normalized_start: str,
    start_precision: str,
    raw_end: str,
    normalized_end: str | None,
    end_precision: str,
    end_semantics: str,
    asserted_current: bool,
    *,
    container_id: str,
    container_kind: str,
    member: str | None = None,
    organization: str | None = None,
    container_status: str = "ACCEPTED",
) -> dict:
    assigned = container_status != "REVIEW_REQUIRED"
    return {
        "schema_id": "staffer-network-legistorm-employment-capture-v1",
        "profile_id": "267918",
        "profile_url": "https://www.legistorm.com/person/bio/267918/Gordon_E_Holzberg.html",
        "retrieved_at": "2026-08-31T12:00:00Z",
        "source_as_of": "2026-08-31",
        "profile_title_raw": "Gordon Holzberg - America's Credit Unions (Aug. 2024-), Legislative Advocacy Director - Biography | LegiStorm",
        "lda_source_person_id": "lda:lobbyist:1",
        "source_row_id": source_row_id,
        "raw_person_name": "Gordon E. Holzberg",
        "raw_employer": employer,
        "raw_title": title,
        "raw_start": raw_start,
        "normalized_start": normalized_start,
        "raw_end": raw_end,
        "normalized_end": normalized_end,
        "start_precision": start_precision,
        "end_precision": end_precision,
        "source_end_semantics": end_semantics,
        "asserted_current": asserted_current,
        "person_adjudication": {
            "status": "ACCEPTED",
            "resolved_person_id": "canonical:person:gordon-holzberg",
            "reviewer_kind": "HUMAN",
            "reviewer_id": "fixture-reviewer",
            "recorded_at": "2026-08-31T12:00:00Z",
            "rationale": "LDA name and covered offices agree with the local profile evidence.",
        },
        "container_adjudication": {
            "status": container_status,
            "normalized_container_id": container_id if assigned else None,
            "container_kind": container_kind,
            "member_bioguide_id": member,
            "organization_id": organization,
            "reviewer_kind": "HUMAN" if assigned else "UNASSIGNED",
            "reviewer_id": "fixture-reviewer" if assigned else None,
            "recorded_at": "2026-08-31T12:00:00Z",
            "rationale": (
                "Employer resolved from the local employment record."
                if assigned else "Container awaits independent review."
            ),
        },
    }


def _gordon_captures() -> list[dict]:
    return [
        _gordon_capture(
            "adams-through-2023-01-02", "Rep. Alma Adams", "Legislative staff",
            "May 2021", "2021-05", "month", "01/02/23", "2023-01-02",
            "day", "inclusive", False,
            container_id="member-office:A000370:117",
            container_kind="member_office", member="A000370",
        ),
        _gordon_capture(
            "sykes-2023-01-03-through-2024-01-02", "Rep. Emilia Sykes",
            "Legislative Director", "01/03/23", "2023-01-03", "day",
            "01/02/24", "2024-01-02", "day", "inclusive", False,
            container_id="member-office:S001223:118",
            container_kind="member_office", member="S001223",
        ),
        _gordon_capture(
            "americas-credit-unions-2024-08-current", "America's Credit Unions",
            "Legislative Advocacy Director", "Aug. 2024", "2024-08", "month",
            "Present", None, "unknown", "current", True,
            container_id="lda:registrant:1", container_kind="registrant",
            organization="lda:registrant:1",
        ),
    ]


def _current_generation(root: Path) -> Path:
    pointer = json.loads(
        (root / "legistorm_employment_current.json").read_text(encoding="utf-8")
    )
    return root / "generations" / pointer["generation_id"]


class HistoryCompletenessAuditTests(unittest.TestCase):
    def test_partial_episode_cannot_claim_complete_history(self):
        result = audit_history_completeness({
            "lda_source_person_id": "lda:lobbyist:1",
            "employment_episodes": [{
                "employer": "Example Org",
                "title": "Director",
                "raw_dates": "2024-",
                "source_urls": ["https://example.org/bio"],
            }],
            "education": [],
        })
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertNotIn("EDUCATION_HISTORY_EMPTY", result["failure_codes"])
        self.assertIn(
            "EDUCATION_HISTORY_EMPTY", result["education_diagnostic_codes"]
        )
        self.assertIn("EXHAUSTIVENESS_ATTESTATION_MISSING", result["failure_codes"])

    def test_education_is_nonblocking_when_employment_is_exhaustive(self):
        with _verified_episode_fixture() as (episodes, provenance):
            result = audit_history_completeness({
                "lda_source_person_id": "lda:lobbyist:1",
                "employment_episodes": episodes,
                "education": [],
                "history_completeness_attestation": {
                    **_attestation_metadata(),
                    "employment_exhaustive": True,
                    "unresolved_employment_gaps": [],
                    "adjudication_status": "PASS",
                    "attested_episode_digest": employment_episode_digest(episodes),
                },
            }, canonical_provenance=provenance)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["employment_status"], "COMPLETE")
        self.assertEqual(result["education_status"], "MISSING")
        self.assertEqual(result["failure_codes"], [])
        self.assertIn(
            "EDUCATION_HISTORY_EMPTY", result["education_diagnostic_codes"]
        )

    def test_dated_sourced_exhaustive_histories_pass(self):
        with _verified_episode_fixture() as (episodes, provenance):
            result = audit_history_completeness({
                "lda_source_person_id": "lda:lobbyist:1",
                "employment_episodes": episodes,
                "education": [{
                    "institution": "Example University",
                    "years": "2010-2014",
                    "source_url": "https://example.edu/alumni",
                }],
                "history_completeness_attestation": {
                    **_attestation_metadata(),
                    "employment_exhaustive": True,
                    "education_exhaustive": True,
                    "unresolved_gaps": [],
                    "adjudication_status": "PASS",
                    "attested_episode_digest": employment_episode_digest(episodes),
                },
            }, canonical_provenance=provenance)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["employment_status"], "COMPLETE")
        self.assertEqual(result["education_status"], "COMPLETE")
        self.assertEqual(result["failure_codes"], [])
        self.assertEqual(result["education_diagnostic_codes"], [])

    def test_malformed_dates_and_sources_cannot_claim_complete(self):
        episodes = [{
            "employer": "Example Org",
            "title": "Director",
            "start_date": "unknown",
            "end_date": "n/a",
            "source_urls": ["   "],
        }]
        result = audit_history_completeness({
            "lda_source_person_id": "lda:lobbyist:1",
            "employment_episodes": episodes,
            "history_completeness_attestation": {
                **_attestation_metadata(),
                "employment_exhaustive": True,
                "unresolved_employment_gaps": [],
                "adjudication_status": "PASS",
                "attested_episode_digest": employment_episode_digest(episodes),
            },
        })
        self.assertEqual(result["employment_status"], "INCOMPLETE")
        self.assertIn("EMPLOYMENT_EPISODE_1_START_MISSING", result["failure_codes"])
        self.assertIn("EMPLOYMENT_EPISODE_1_END_MISSING", result["failure_codes"])
        self.assertIn("EMPLOYMENT_EPISODE_1_SOURCE_MISSING", result["failure_codes"])

    def test_attestation_must_bind_exact_episode_set(self):
        original = [{
            "employer": "Example Org",
            "title": "Director",
            "start_date": "2020",
            "end_date": "2021",
            "source_url": "https://example.org/bio",
        }]
        expanded = original + [{
            "employer": "Other Org",
            "title": "Counsel",
            "start_date": "2022",
            "end_date": "2023",
            "source_url": "https://other.example/bio",
        }]
        result = audit_history_completeness({
            "lda_source_person_id": "lda:lobbyist:1",
            "employment_episodes": expanded,
            "history_completeness_attestation": {
                **_attestation_metadata(),
                "employment_exhaustive": True,
                "unresolved_employment_gaps": [],
                "adjudication_status": "PASS",
                "attested_episode_digest": employment_episode_digest(original),
            },
        })
        self.assertEqual(result["employment_status"], "INCOMPLETE")
        self.assertIn("EXHAUSTIVENESS_ATTESTATION_UNBOUND", result["failure_codes"])

    def test_typed_and_legacy_gap_fields_are_unioned(self):
        episodes = [{
            "employer": "Example Org",
            "title": "Director",
            "start_date": "2020",
            "end_date": "2021",
            "source_url": "https://example.org/bio",
        }]
        result = audit_history_completeness({
            "lda_source_person_id": "lda:lobbyist:1",
            "employment_episodes": episodes,
            "history_completeness_attestation": {
                **_attestation_metadata(),
                "employment_exhaustive": True,
                "unresolved_employment_gaps": [],
                "unresolved_gaps": ["1999-2004 unaccounted"],
                "adjudication_status": "PASS",
                "attested_episode_digest": employment_episode_digest(episodes),
            },
        })
        self.assertEqual(result["employment_status"], "INCOMPLETE")
        self.assertIn("UNRESOLVED_EMPLOYMENT_GAPS_REMAIN", result["failure_codes"])

    def test_missing_source_person_id_cannot_claim_complete(self):
        episodes = [{
            "employer": "Example Org",
            "title": "Director",
            "start_date": "2020",
            "end_date": "2021",
            "source_url": "https://example.org/bio",
        }]
        result = audit_history_completeness({
            "employment_episodes": episodes,
            "history_completeness_attestation": {
                **_attestation_metadata(),
                "employment_exhaustive": True,
                "unresolved_employment_gaps": [],
                "adjudication_status": "PASS",
                "attested_episode_digest": employment_episode_digest(episodes),
            },
        })
        self.assertEqual(result["employment_status"], "INCOMPLETE")
        self.assertIn("LDA_SOURCE_PERSON_ID_MISSING", result["failure_codes"])

    def test_conflicting_top_level_and_nested_person_ids_fail_closed(self):
        episodes = [{
            "employer": "Example Org",
            "title": "Director",
            "start_date": "2020",
            "end_date": "2021",
            "source_url": "https://example.org/bio",
        }]
        result = audit_history_completeness({
            "lda_source_person_id": "lda:lobbyist:1",
            "person_identity": {"lda_source_person_id": "lda:lobbyist:999"},
            "employment_episodes": episodes,
            "history_completeness_attestation": {
                **_attestation_metadata(),
                "employment_exhaustive": True,
                "unresolved_employment_gaps": [],
                "adjudication_status": "PASS",
                "attested_episode_digest": employment_episode_digest(episodes),
            },
        })
        self.assertEqual(result["employment_status"], "INCOMPLETE")
        self.assertIn("LDA_SOURCE_PERSON_ID_CONFLICT", result["failure_codes"])

    def test_audit_corpus_validates_v2_result_schema(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "histories.ndjson.gz"
            output = root / "audit"
            _write_gzip(source, [{
                "lda_source_person_id": "lda:lobbyist:1",
                "employment_episodes": [],
                "education": [],
            }])
            receipt = audit_history_corpus([source], output)
            self.assertEqual(receipt["record_rows"], 1)
            self.assertEqual(receipt["incomplete_rows"], 1)
            self.assertEqual(receipt["status"], "INCOMPLETE")
            self.assertEqual(receipt["identified_people"], 1)
            self.assertEqual(receipt["unidentified_rows"], 0)

    def test_canonical_episode_shape_can_be_audited(self):
        with _verified_episode_fixture() as (episodes, provenance):
            result = audit_history_completeness({
                "lda_source_person_id": "lda:lobbyist:1",
                "employment_episodes": episodes,
                "history_completeness_attestation": {
                    **_attestation_metadata(),
                    "employment_exhaustive": True,
                    "unresolved_employment_gaps": [],
                    "adjudication_status": "PASS",
                    "attested_episode_digest": employment_episode_digest(episodes),
                },
            }, canonical_provenance=provenance)
        self.assertEqual(result["employment_status"], "COMPLETE")

    def test_canonical_episode_inverted_quartet_fails_semantic_gate(self):
        episodes = [_episode(
            "episode:inverted", "canonical:person:1",
            "organization:example", "other_org",
            "2021-01-01", "2020-01-01", "2022-01-01", "2022-01-01",
            organization="organization:example",
        )]
        result = audit_history_completeness({
            "lda_source_person_id": "lda:lobbyist:1",
            "employment_episodes": episodes,
            "history_completeness_attestation": {
                **_attestation_metadata(),
                "employment_exhaustive": True,
                "unresolved_employment_gaps": [],
                "adjudication_status": "PASS",
                "attested_episode_digest": employment_episode_digest(episodes),
            },
        })
        self.assertEqual(result["employment_status"], "INCOMPLETE")
        self.assertIn(
            "EMPLOYMENT_EPISODE_1_CANONICAL_SEMANTICS_INVALID",
            result["failure_codes"],
        )

    def test_invalid_hill_container_binding_fails_semantic_gate(self):
        episodes = [_episode(
            "episode:bad-container", "canonical:person:1",
            "member-office:missing", "member_office",
            "2020-01-01", "2020-01-01", "2021-01-01", "2021-01-01",
            member=None,
        )]
        result = audit_history_completeness({
            "lda_source_person_id": "lda:lobbyist:1",
            "employment_episodes": episodes,
            "history_completeness_attestation": {
                **_attestation_metadata(),
                "employment_exhaustive": True,
                "unresolved_employment_gaps": [],
                "adjudication_status": "PASS",
                "attested_episode_digest": employment_episode_digest(episodes),
            },
        })
        self.assertEqual(result["employment_status"], "INCOMPLETE")
        self.assertIn(
            "EMPLOYMENT_EPISODE_1_CANONICAL_SEMANTICS_INVALID",
            result["failure_codes"],
        )

    def test_canonical_episode_requires_external_provenance_binding(self):
        episodes = [_episode(
            "episode:dangling-provenance", "canonical:person:1",
            "organization:example", "other_org",
            "2020-01-01", "2020-01-01", "2021-01-01", "2021-01-01",
            organization="organization:example",
        )]
        record = {
            "lda_source_person_id": "lda:lobbyist:1",
            "employment_episodes": episodes,
            "history_completeness_attestation": {
                **_attestation_metadata(),
                "employment_exhaustive": True,
                "unresolved_employment_gaps": [],
                "adjudication_status": "PASS",
                "attested_episode_digest": employment_episode_digest(episodes),
            },
        }
        unverified = audit_history_completeness(record)
        self.assertIn(
            "EMPLOYMENT_EPISODE_1_CANONICAL_PROVENANCE_UNVERIFIED",
            unverified["failure_codes"],
        )
        with self.assertRaises(EmploymentHistoryError):
            CanonicalEmploymentProvenance(
                generation_receipt_path="missing-receipt.json",
                generation_receipt_sha256="not-a-sha256",
            )

    def test_verified_registry_rejects_mutated_episode_payload(self):
        with _verified_episode_fixture() as (episodes, provenance):
            mutated = [{**episodes[0], "title": "Forged title"}]
            result = audit_history_completeness({
                "lda_source_person_id": "lda:lobbyist:1",
                "employment_episodes": mutated,
                "history_completeness_attestation": {
                    **_attestation_metadata(),
                    "employment_exhaustive": True,
                    "unresolved_employment_gaps": [],
                    "adjudication_status": "PASS",
                    "attested_episode_digest": employment_episode_digest(mutated),
                },
            }, canonical_provenance=provenance)
            self.assertIn(
                "EMPLOYMENT_EPISODE_1_CANONICAL_PROVENANCE_MISMATCH",
                result["failure_codes"],
            )

    def test_verified_registry_rejects_swapped_lda_person(self):
        with _verified_episode_fixture() as (episodes, provenance):
            result = audit_history_completeness({
                "lda_source_person_id": "lda:lobbyist:999999",
                "employment_episodes": episodes,
                "history_completeness_attestation": {
                    **_attestation_metadata(),
                    "employment_exhaustive": True,
                    "unresolved_employment_gaps": [],
                    "adjudication_status": "PASS",
                    "attested_episode_digest": employment_episode_digest(episodes),
                },
            }, canonical_provenance=provenance)
            self.assertEqual(result["employment_status"], "INCOMPLETE")
            self.assertIn(
                "EMPLOYMENT_EPISODE_1_CANONICAL_PROVENANCE_MISMATCH",
                result["failure_codes"],
            )

    def test_verified_registry_requires_complete_person_episode_set(self):
        with _verified_episode_fixture(_gordon_captures()) as (episodes, provenance):
            self.assertEqual(len(episodes), 3)
            omitted = episodes[:1]
            result = audit_history_completeness({
                "lda_source_person_id": "lda:lobbyist:1",
                "employment_episodes": omitted,
                "history_completeness_attestation": {
                    **_attestation_metadata(),
                    "employment_exhaustive": True,
                    "unresolved_employment_gaps": [],
                    "adjudication_status": "PASS",
                    "attested_episode_digest": employment_episode_digest(omitted),
                },
            }, canonical_provenance=provenance)
            self.assertEqual(result["employment_status"], "INCOMPLETE")
            self.assertIn(
                "CANONICAL_EPISODE_SET_MISMATCH", result["failure_codes"]
            )

    def test_malformed_episode_id_fails_closed_with_verified_registry(self):
        with _verified_episode_fixture() as (episodes, provenance):
            malformed = [{**episodes[0], "episode_id": []}]
            result = audit_history_completeness({
                "lda_source_person_id": "lda:lobbyist:1",
                "employment_episodes": malformed,
                "history_completeness_attestation": {
                    **_attestation_metadata(),
                    "employment_exhaustive": True,
                    "unresolved_employment_gaps": [],
                    "adjudication_status": "PASS",
                    "attested_episode_digest": employment_episode_digest(malformed),
                },
            }, canonical_provenance=provenance)
            self.assertEqual(result["employment_status"], "INCOMPLETE")
            self.assertIn(
                "CANONICAL_EPISODE_SET_MISMATCH", result["failure_codes"]
            )

    def test_internal_observation_id_alone_is_not_source_evidence(self):
        episodes = [{
            "normalized_container_id": "member-office:A000001:117",
            "title": "Legislative Director",
            "start_earliest": "2020-01-01",
            "end_earliest": "2021-01-01",
            "support_observation_ids": ["totally-made-up"],
        }]
        result = audit_history_completeness({
            "lda_source_person_id": "lda:lobbyist:1",
            "employment_episodes": episodes,
            "history_completeness_attestation": {
                **_attestation_metadata(),
                "employment_exhaustive": True,
                "unresolved_employment_gaps": [],
                "adjudication_status": "PASS",
                "attested_episode_digest": employment_episode_digest(episodes),
            },
        })
        self.assertEqual(result["employment_status"], "INCOMPLETE")
        self.assertIn("EMPLOYMENT_EPISODE_1_SOURCE_MISSING", result["failure_codes"])

    def test_raw_dates_current_words_do_not_match_substrings(self):
        episodes = [{
            "employer": "Example Org",
            "title": "Director",
            "raw_dates": "presented research in 2020, concurrent duties",
            "source_url": "https://example.org/bio",
        }]
        result = audit_history_completeness({
            "lda_source_person_id": "lda:lobbyist:1",
            "employment_episodes": episodes,
            "history_completeness_attestation": {
                **_attestation_metadata(),
                "employment_exhaustive": True,
                "unresolved_employment_gaps": [],
                "adjudication_status": "PASS",
                "attested_episode_digest": employment_episode_digest(episodes),
            },
        })
        self.assertIn("EMPLOYMENT_EPISODE_1_END_MISSING", result["failure_codes"])

    def test_impossible_iso_date_and_unbounded_current_fail(self):
        episodes = [{
            "employer": "Example Org",
            "title": "Director",
            "start_date": "2026-13-45",
            "asserted_current": True,
            "source_url": "https://example.org/bio",
        }]
        result = audit_history_completeness({
            "lda_source_person_id": "lda:lobbyist:1",
            "employment_episodes": episodes,
            "history_completeness_attestation": {
                **_attestation_metadata(),
                "employment_exhaustive": True,
                "unresolved_employment_gaps": [],
                "adjudication_status": "PASS",
                "attested_episode_digest": employment_episode_digest(episodes),
            },
        })
        self.assertIn("EMPLOYMENT_EPISODE_1_START_MISSING", result["failure_codes"])
        self.assertIn("EMPLOYMENT_EPISODE_1_CURRENT_AS_OF_MISSING", result["failure_codes"])

    def test_raw_current_marker_requires_as_of_bound(self):
        episodes = [{
            "employer": "Example Org",
            "title": "Director",
            "raw_dates": "2020-present",
            "source_url": "https://example.org/bio",
        }]
        result = audit_history_completeness({
            "lda_source_person_id": "lda:lobbyist:1",
            "employment_episodes": episodes,
            "history_completeness_attestation": {
                **_attestation_metadata(),
                "employment_exhaustive": True,
                "unresolved_employment_gaps": [],
                "adjudication_status": "PASS",
                "attested_episode_digest": employment_episode_digest(episodes),
            },
        })
        self.assertEqual(result["employment_status"], "INCOMPLETE")
        self.assertIn("EMPLOYMENT_EPISODE_1_END_MISSING", result["failure_codes"])
        self.assertIn(
            "EMPLOYMENT_EPISODE_1_CURRENT_AS_OF_MISSING",
            result["failure_codes"],
        )

    def test_malformed_raw_and_prose_dates_fail_closed(self):
        for raw_dates in (
            "2015-13-45 to 2016-99-99",
            "circa 2015 maybe to unknown but before 2019",
        ):
            episodes = [{
                "employer": "Example Org",
                "title": "Director",
                "raw_dates": raw_dates,
                "source_url": "https://example.org/bio",
            }]
            result = audit_history_completeness({
                "lda_source_person_id": "lda:lobbyist:1",
                "employment_episodes": episodes,
                "history_completeness_attestation": {
                    **_attestation_metadata(),
                    "employment_exhaustive": True,
                    "unresolved_employment_gaps": [],
                    "adjudication_status": "PASS",
                    "attested_episode_digest": employment_episode_digest(episodes),
                },
            })
            self.assertEqual(result["employment_status"], "INCOMPLETE")
            self.assertIn("EMPLOYMENT_EPISODE_1_START_MISSING", result["failure_codes"])
            self.assertIn("EMPLOYMENT_EPISODE_1_END_MISSING", result["failure_codes"])

    def test_literal_unresolved_person_id_fails_closed(self):
        episodes = [{
            "employer": "Example Org",
            "title": "Director",
            "start_date": "2020",
            "end_date": "2021",
            "source_url": "https://example.org/bio",
        }]
        result = audit_history_completeness({
            "lda_source_person_id": "UNRESOLVED",
            "employment_episodes": episodes,
            "history_completeness_attestation": {
                **_attestation_metadata(),
                "employment_exhaustive": True,
                "unresolved_employment_gaps": [],
                "adjudication_status": "PASS",
                "attested_episode_digest": employment_episode_digest(episodes),
            },
        })
        self.assertEqual(result["employment_status"], "INCOMPLETE")
        self.assertIn("LDA_SOURCE_PERSON_ID_MISSING", result["failure_codes"])

    def test_structured_prose_dates_do_not_supply_bounds(self):
        episodes = [{
            "employer": "Example Org",
            "title": "Director",
            "start_date": "circa 2015 maybe",
            "end_date": "unknown but before 2019",
            "source_url": "https://example.org/bio",
        }]
        result = audit_history_completeness({
            "lda_source_person_id": "lda:lobbyist:1",
            "employment_episodes": episodes,
            "history_completeness_attestation": {
                **_attestation_metadata(),
                "employment_exhaustive": True,
                "unresolved_employment_gaps": [],
                "adjudication_status": "PASS",
                "attested_episode_digest": employment_episode_digest(episodes),
            },
        })
        self.assertIn("EMPLOYMENT_EPISODE_1_START_MISSING", result["failure_codes"])
        self.assertIn("EMPLOYMENT_EPISODE_1_END_MISSING", result["failure_codes"])

    def test_inverted_and_out_of_window_dates_fail_closed(self):
        for episode, expected_code in (
            (
                {
                    "employer": "Example Org",
                    "title": "Director",
                    "start_date": "2025",
                    "end_date": "2020",
                    "source_url": "https://example.org/bio",
                },
                "EMPLOYMENT_EPISODE_1_DATE_ORDER_INVALID",
            ),
            (
                {
                    "employer": "Example Org",
                    "title": "Director",
                    "raw_dates": "2025 to 2020",
                    "source_url": "https://example.org/bio",
                },
                "EMPLOYMENT_EPISODE_1_DATE_ORDER_INVALID",
            ),
            (
                {
                    "employer": "Example Org",
                    "title": "Director",
                    "raw_dates": "2099 to 2100",
                    "source_url": "https://example.org/bio",
                },
                "EMPLOYMENT_EPISODE_1_START_MISSING",
            ),
            (
                {
                    "employer": "Example Org",
                    "title": "Director",
                    "start_date": "2020",
                    "asserted_current": True,
                    "source_as_of": "2019",
                    "source_url": "https://example.org/bio",
                },
                "EMPLOYMENT_EPISODE_1_DATE_ORDER_INVALID",
            ),
        ):
            episodes = [episode]
            result = audit_history_completeness({
                "lda_source_person_id": "lda:lobbyist:1",
                "employment_episodes": episodes,
                "history_completeness_attestation": {
                    **_attestation_metadata(),
                    "employment_exhaustive": True,
                    "unresolved_employment_gaps": [],
                    "adjudication_status": "PASS",
                    "attested_episode_digest": employment_episode_digest(episodes),
                },
            })
            self.assertEqual(result["employment_status"], "INCOMPLETE")
            self.assertIn(expected_code, result["failure_codes"])

    def test_raw_month_range_cannot_bypass_canonical_episode_schema(self):
        episodes = [_auditable_episode(
            employer="Example Org",
            title="Director",
            raw_dates="Jan 2020 - Feb 2021",
            source_url="https://example.org/bio",
        )]
        result = audit_history_completeness({
            "lda_source_person_id": "lda:lobbyist:1",
            "employment_episodes": episodes,
            "history_completeness_attestation": {
                **_attestation_metadata(),
                "employment_exhaustive": True,
                "unresolved_employment_gaps": [],
                "adjudication_status": "PASS",
                "attested_episode_digest": employment_episode_digest(episodes),
            },
        })
        self.assertEqual(result["employment_status"], "INCOMPLETE")
        self.assertIn(
            "EMPLOYMENT_EPISODE_1_CANONICAL_SCHEMA_INVALID",
            result["failure_codes"],
        )

    def test_provisional_episode_cannot_pass_with_forged_exhaustive_attestation(self):
        episodes = [{
            "schema_id": "staffer-network-provisional-employment-episode-v1",
            "episode_status": "PROVISIONAL",
            "identity_status": "PROVISIONALLY_ACCEPTED_CORROBORATED",
            "container_status": "LITERAL_REVIEW_REQUIRED",
            "promotion_blockers": ["CONTAINER_NORMALIZATION_REQUIRED"],
            "canonical_eligible": False,
            "temporal_eligible": False,
            "employer": "Example Org",
            "title": "Director",
            "start_date": "2020-01-01",
            "end_date": "2021-01-01",
            "source_url": "https://example.org/bio",
        }]
        result = audit_history_completeness({
            "lda_source_person_id": "lda:lobbyist:1",
            "employment_episodes": episodes,
            "history_completeness_attestation": {
                **_attestation_metadata(),
                "employment_exhaustive": True,
                "unresolved_employment_gaps": [],
                "adjudication_status": "PASS",
                "attested_episode_digest": employment_episode_digest(episodes),
            },
        })
        self.assertEqual(result["employment_status"], "INCOMPLETE")
        self.assertIn(
            "EMPLOYMENT_EPISODE_1_CANONICAL_SCHEMA_REQUIRED",
            result["failure_codes"],
        )
        self.assertIn(
            "EMPLOYMENT_EPISODE_1_CONTAINER_ACCEPTED_REQUIRED",
            result["failure_codes"],
        )

    def test_review_container_blocks_otherwise_adjudicated_episode(self):
        episodes = [_auditable_episode(
            employer="Example Org",
            title="Director",
            start_date="2020-01-01",
            end_date="2021-01-01",
            source_url="https://example.org/bio",
            container_status="REVIEW_REQUIRED",
        )]
        result = audit_history_completeness({
            "lda_source_person_id": "lda:lobbyist:1",
            "employment_episodes": episodes,
            "history_completeness_attestation": {
                **_attestation_metadata(),
                "employment_exhaustive": True,
                "unresolved_employment_gaps": [],
                "adjudication_status": "PASS",
                "attested_episode_digest": employment_episode_digest(episodes),
            },
        })
        self.assertEqual(result["employment_status"], "INCOMPLETE")
        self.assertIn(
            "EMPLOYMENT_EPISODE_1_CONTAINER_ACCEPTED_REQUIRED",
            result["failure_codes"],
        )

    def test_duplicate_person_rows_make_corpus_incomplete(self):
        episodes = [{
            "employer": "Example Org",
            "title": "Director",
            "start_date": "2020",
            "end_date": "2021",
            "source_url": "https://example.org/bio",
        }]
        row = {
            "lda_source_person_id": "lda:lobbyist:1",
            "employment_episodes": episodes,
            "history_completeness_attestation": {
                **_attestation_metadata(),
                "employment_exhaustive": True,
                "unresolved_employment_gaps": [],
                "adjudication_status": "PASS",
                "attested_episode_digest": employment_episode_digest(episodes),
            },
        }
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            left = root / "left.ndjson.gz"
            right = root / "right.ndjson.gz"
            _write_gzip(left, [row])
            _write_gzip(right, [{
                **row,
                "lda_source_person_id": " LDA:LOBBYIST:1 ",
            }])
            receipt = audit_history_corpus([left, right], root / "audit")
        self.assertEqual(receipt["status"], "INCOMPLETE")
        self.assertEqual(receipt["duplicate_identified_person_rows"], 1)
        self.assertEqual(receipt["complete_people"], 0)
        self.assertEqual(receipt["incomplete_people"], 1)

    def test_schemas_reject_contradictory_complete_and_pass_verdicts(self):
        schema_root = FEC / "staffer_network" / "schemas"
        row_schema = json.loads(
            (schema_root / "history_completeness_audit.schema.json").read_text(
                encoding="utf-8"
            )
        )
        receipt_schema = json.loads(
            (
                schema_root / "history_completeness_audit_receipt.schema.json"
            ).read_text(encoding="utf-8")
        )
        bad_row = {
            "schema_id": "staffer-network-employment-completeness-audit-v2",
            "lda_source_person_id": "lda:lobbyist:1",
            "display_name": None,
            "status": "COMPLETE",
            "employment_status": "COMPLETE",
            "education_status": "MISSING",
            "binding_completeness_dimension": "EMPLOYMENT",
            "employment_episode_count": 1,
            "education_episode_count": 0,
            "failure_codes": ["CONTRADICTION"],
            "education_diagnostic_codes": [],
        }
        with self.assertRaises(SchemaValidationError):
            validate_instance(bad_row, row_schema)
        bad_receipt = {
            "schema_id": "staffer-network-employment-completeness-audit-receipt-v2",
            "generated_at": "2026-08-31T12:00:00Z",
            "status": "PASS",
            "binding_completeness_dimension": "EMPLOYMENT",
            "education_is_nonblocking": True,
            "record_rows": 1,
            "identified_people": 1,
            "unidentified_rows": 0,
            "duplicate_identified_person_rows": 0,
            "complete_people": 0,
            "incomplete_people": 1,
            "complete_rows": 0,
            "incomplete_rows": 1,
            "failure_counts": {"CONTRADICTION": 1},
            "education_diagnostic_counts": {},
            "source_files": [{
                "path": "source.ndjson.gz",
                "sha256": "0" * 64,
            }],
            "audit_file": {
                "path": "history_completeness_audit.ndjson.gz",
                "sha256": "0" * 64,
                "bytes": 1,
            },
        }
        with self.assertRaises(SchemaValidationError):
            validate_instance(bad_receipt, receipt_schema)

    def test_missing_id_corpus_emits_incomplete_receipt_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "histories.ndjson.gz"
            output = root / "audit"
            _write_gzip(source, [{"employment_episodes": [], "education": []}])
            receipt = audit_history_corpus([source], output)
            self.assertEqual(receipt["status"], "INCOMPLETE")
            self.assertEqual(receipt["identified_people"], 0)
            self.assertEqual(receipt["unidentified_rows"], 1)

    def test_empty_corpus_emits_incomplete_receipt(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "histories.ndjson.gz"
            output = root / "audit"
            _write_gzip(source, [])
            receipt = audit_history_corpus([source], output)
            self.assertEqual(receipt["status"], "INCOMPLETE")
            self.assertEqual(receipt["record_rows"], 0)
            self.assertEqual(receipt["identified_people"], 0)
            self.assertEqual(receipt["unidentified_rows"], 0)


class EmploymentLookupTests(unittest.TestCase):
    def test_full_lda_person_universe_is_conserved_without_identity_merge(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            lobbyists = [
                {
                    "schema_id": "staffer-network-lda-lobbyist-assertion-v1",
                    "source_system_id": "senate-lda-api",
                    "source_person_id": "lda:lobbyist:1",
                    "source_numeric_id": 1,
                    "display_name": "ALPHA PERSON",
                    "name_variants": ["ALPHA PERSON"],
                    "first_name_variants": ["ALPHA"],
                    "middle_name_variants": [],
                    "last_name_variants": ["PERSON"],
                    "suffix_variants": [],
                    "filing_years": [2026],
                    "first_dt_posted": "2026-01-01T00:00:00Z",
                    "last_dt_posted": "2026-01-01T00:00:00Z",
                    "activity_support_count": 1,
                    "covered_position_support_count": 1,
                    "covered_position_values": ["Legislative Director, House, 2020"],
                },
                {
                    "schema_id": "staffer-network-lda-lobbyist-assertion-v1",
                    "source_system_id": "senate-lda-api",
                    "source_person_id": "lda:lobbyist:2",
                    "source_numeric_id": 2,
                    "display_name": "BETA PERSON",
                    "name_variants": ["BETA PERSON"],
                    "first_name_variants": ["BETA"],
                    "middle_name_variants": [],
                    "last_name_variants": ["PERSON"],
                    "suffix_variants": [],
                    "filing_years": [2026],
                    "first_dt_posted": "2026-01-01T00:00:00Z",
                    "last_dt_posted": "2026-01-01T00:00:00Z",
                    "activity_support_count": 1,
                    "covered_position_support_count": 1,
                    "covered_position_values": ["Chief of Staff"],
                },
                {
                    "schema_id": "staffer-network-lda-lobbyist-assertion-v1",
                    "source_system_id": "senate-lda-api",
                    "source_person_id": "lda:lobbyist:3",
                    "source_numeric_id": 3,
                    "display_name": "GAMMA PERSON",
                    "name_variants": ["GAMMA PERSON"],
                    "first_name_variants": ["GAMMA"],
                    "middle_name_variants": [],
                    "last_name_variants": ["PERSON"],
                    "suffix_variants": [],
                    "filing_years": [2026],
                    "first_dt_posted": "2026-01-01T00:00:00Z",
                    "last_dt_posted": "2026-01-01T00:00:00Z",
                    "activity_support_count": 1,
                    "covered_position_support_count": 0,
                    "covered_position_values": [],
                },
            ]
            lobbyist_path = source / "lda_lobbyist_assertions.ndjson.gz"
            _write_gzip(lobbyist_path, lobbyists)
            source_receipt = {
                "schema_id": "staffer-network-lda-source-native-corpus-receipt-v1",
                "status": "PASS",
                "canonical_identity_merges": 0,
                "name_matches_are_candidates_only": True,
                "lobbyists": 3,
                "files": [{
                    "role": "lobbyist_assertions",
                    "bytes": lobbyist_path.stat().st_size,
                    "sha256": sha256_file(lobbyist_path),
                }],
            }
            (source / "lda_source_native_corpus_receipt.json").write_bytes(
                canonical_json_bytes(source_receipt)
            )
            receipt = build_lookup_queue(
                source, output, generated_at="2026-08-31T00:00:00Z"
            )
            self.assertEqual(receipt["lda_lobbyists"], 3)
            self.assertEqual(receipt["priority_counts"], {"P0": 1, "P1": 1, "P2": 1})
            self.assertEqual(receipt["identity_matches_asserted"], 0)
            self.assertEqual(receipt["canonical_identity_merges"], 0)
            with gzip.open(output / "employment_lookup_queue.ndjson.gz", "rt", encoding="utf-8") as stream:
                rows = [json.loads(line) for line in stream]
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(row["identity_status"] == "REVIEW_REQUIRED" for row in rows))


class LegistormEmploymentCheckpointTests(unittest.TestCase):
    def test_latest_observation_rejects_conflicting_equal_retrieval_times(self):
        with self.assertRaisesRegex(
            EmploymentHistoryError,
            "conflicting employment observations share one retrieval time",
        ):
            _latest_checkpoint_observation([
                {
                    "retrieved_at": "2026-08-31T12:00:00Z",
                    "observation_id": "observation:a",
                },
                {
                    "retrieved_at": "2026-08-31T12:00:00Z",
                    "observation_id": "observation:b",
                },
            ])

    def test_gordon_fixture_preserves_raw_evidence_and_half_open_bounds(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            captures_path = root / "captures.ndjson.gz"
            _write_gzip(captures_path, _gordon_captures())
            receipt = build_legistorm_employment_checkpoint(
                captures_path, root / "checkpoint"
            )
            self.assertEqual(receipt["observation_rows"], 3)
            self.assertEqual(receipt["episode_rows"], 3)
            self.assertEqual(receipt["episode_eligibility_counts"]["PRIMARY_READY"], 3)
            self.assertTrue(receipt["person_container_adjudication_independent"])
            self.assertEqual(receipt["canonical_identity_merges"], 0)

            generation = _current_generation(root / "checkpoint")
            with gzip.open(
                generation / "legistorm_employment_observations.ndjson.gz",
                "rt", encoding="utf-8",
            ) as stream:
                observations = [json.loads(line) for line in stream]
            by_row = {row["source_row_id"]: row for row in observations}
            sykes = by_row["sykes-2023-01-03-through-2024-01-02"]
            self.assertEqual(sykes["raw_start"], "01/03/23")
            self.assertEqual(sykes["raw_end"], "01/02/24")
            self.assertEqual(sykes["start_earliest"], "2023-01-03")
            self.assertEqual(sykes["end_earliest"], "2024-01-03")
            self.assertEqual(
                sykes["profile_url"],
                "https://www.legistorm.com/person/bio/267918/Gordon_E_Holzberg.html",
            )
            self.assertEqual(sykes["retrieved_at"], "2026-08-31T12:00:00Z")
            acu = by_row["americas-credit-unions-2024-08-current"]
            self.assertEqual((acu["start_earliest"], acu["start_latest"]), (
                "2024-08-01", "2024-08-31",
            ))
            self.assertEqual((acu["end_earliest"], acu["end_latest"]), (
                "2026-09-01", "2026-09-01",
            ))
            self.assertRegex(acu["source_record_sha256"], r"^[0-9a-f]{64}$")

    def test_resume_matches_clean_build_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            captures = _gordon_captures()
            first_path = root / "first.ndjson.gz"
            second_path = root / "second.ndjson.gz"
            all_path = root / "all.ndjson.gz"
            _write_gzip(first_path, captures[:2])
            _write_gzip(second_path, captures[2:])
            _write_gzip(all_path, list(reversed(captures)))

            build_legistorm_employment_checkpoint(first_path, root / "resumed")
            resumed = build_legistorm_employment_checkpoint(
                second_path, root / "resumed"
            )
            repeated = build_legistorm_employment_checkpoint(
                second_path, root / "resumed"
            )
            clean = build_legistorm_employment_checkpoint(all_path, root / "clean")
            self.assertEqual(resumed, repeated)
            self.assertEqual(resumed, clean)
            self.assertEqual(
                (root / "resumed" / "legistorm_employment_current.json").read_bytes(),
                (root / "clean" / "legistorm_employment_current.json").read_bytes(),
            )

    def test_provenance_loader_rejects_stale_subset_of_same_key_observations(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            older = json.loads(json.dumps(_gordon_captures()[0]))
            newer = json.loads(json.dumps(_gordon_captures()[0]))
            older["retrieved_at"] = "2026-08-30T12:00:00Z"
            older["source_as_of"] = "2026-08-30"
            older["raw_title"] = "Older title"
            newer["raw_title"] = "Newer title"
            captures_path = root / "captures.ndjson.gz"
            checkpoint = root / "checkpoint"
            _write_gzip(captures_path, [older, newer])
            build_legistorm_employment_checkpoint(captures_path, checkpoint)
            generation = _current_generation(checkpoint)
            observations_path = generation / "legistorm_employment_observations.ndjson.gz"
            episodes_path = generation / "legistorm_employment_episodes.ndjson.gz"
            receipt_path = generation / "legistorm_employment_checkpoint_receipt.json"
            with gzip.open(observations_path, "rt", encoding="utf-8") as stream:
                observations = [json.loads(line) for line in stream if line.strip()]
            with gzip.open(episodes_path, "rt", encoding="utf-8") as stream:
                episode = json.loads(next(line for line in stream if line.strip()))
            oldest = min(observations, key=lambda row: row["retrieved_at"])
            episode.update({
                "title": oldest["raw_title"],
                "start_earliest": oldest["start_earliest"],
                "start_latest": oldest["start_latest"],
                "end_earliest": oldest["end_earliest"],
                "end_latest": oldest["end_latest"],
                "start_precision": oldest["start_precision"],
                "end_precision": oldest["end_precision"],
                "asserted_current": oldest["asserted_current"],
                "source_as_of": oldest["source_as_of"],
                "support_observation_ids": [oldest["observation_id"]],
                "support_digest": _digest([oldest["observation_id"]]),
            })
            _write_gzip(episodes_path, [episode])
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            episode_file = next(
                row for row in receipt["files"]
                if row["role"] == "employment_episodes"
            )
            episode_file["bytes"] = episodes_path.stat().st_size
            episode_file["sha256"] = sha256_file(episodes_path)
            receipt_path.write_bytes(canonical_json_bytes(receipt))
            with self.assertRaisesRegex(
                EmploymentHistoryError,
                "does not include every employment-key observation",
            ):
                load_canonical_employment_provenance(
                    receipt_path, expected_sha256=sha256_file(receipt_path)
                )

    def test_provenance_loader_rejects_cross_subject_supersession(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            captures_path = root / "captures.ndjson.gz"
            checkpoint = root / "checkpoint"
            _write_gzip(captures_path, [_gordon_captures()[0]])
            build_legistorm_employment_checkpoint(captures_path, checkpoint)
            generation = _current_generation(checkpoint)
            decisions_path = generation / "legistorm_adjudication_decisions.ndjson.gz"
            receipt_path = generation / "legistorm_employment_checkpoint_receipt.json"
            with gzip.open(decisions_path, "rt", encoding="utf-8") as stream:
                decisions = [json.loads(line) for line in stream if line.strip()]
            person = next(
                row for row in decisions if row["decision_kind"] == "PERSON_IDENTITY"
            )
            container = next(
                row for row in decisions if row["decision_kind"] == "CONTAINER"
            )
            person["supersedes_decision_id"] = container["decision_id"]
            _write_gzip(decisions_path, decisions)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            decision_file = next(
                row for row in receipt["files"]
                if row["role"] == "adjudication_decisions"
            )
            decision_file["bytes"] = decisions_path.stat().st_size
            decision_file["sha256"] = sha256_file(decisions_path)
            receipt_path.write_bytes(canonical_json_bytes(receipt))
            with self.assertRaisesRegex(
                EmploymentHistoryError, "supersession chain mismatch"
            ):
                load_canonical_employment_provenance(
                    receipt_path, expected_sha256=sha256_file(receipt_path)
                )

    def test_person_and_container_adjudication_gate_independently(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            capture = _gordon_captures()[-1]
            capture["container_adjudication"] = {
                **capture["container_adjudication"],
                "status": "REVIEW_REQUIRED",
                "normalized_container_id": None,
                "reviewer_kind": "UNASSIGNED",
                "reviewer_id": None,
                "rationale": "Container awaits independent review.",
            }
            captures_path = root / "capture.ndjson.gz"
            _write_gzip(captures_path, [capture])
            receipt = build_legistorm_employment_checkpoint(
                captures_path, root / "checkpoint"
            )
            self.assertEqual(receipt["episode_eligibility_counts"], {
                "PRIMARY_READY": 0,
                "IDENTITY_BLOCKED": 0,
                "CONTAINER_BLOCKED": 1,
                "INTERVAL_BLOCKED": 0,
            })
            generation = _current_generation(root / "checkpoint")
            with gzip.open(
                generation / "legistorm_employment_episodes.ndjson.gz",
                "rt", encoding="utf-8",
            ) as stream:
                episode = json.loads(next(stream))
            self.assertEqual(episode["identity_status"], "ACCEPTED")
            self.assertEqual(episode["container_status"], "REVIEW_REQUIRED")

    def test_profile_url_mismatch_and_corrupt_resume_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            invalid = _gordon_captures()[0]
            invalid["profile_url"] = (
                "https://www.legistorm.com/person/bio/999999/Wrong.html"
            )
            invalid_path = root / "invalid.ndjson.gz"
            _write_gzip(invalid_path, [invalid])
            with self.assertRaisesRegex(EmploymentHistoryError, "profile URL/profile ID"):
                build_legistorm_employment_checkpoint(
                    invalid_path, root / "invalid-checkpoint"
                )

            valid_path = root / "valid.ndjson.gz"
            _write_gzip(valid_path, [_gordon_captures()[0]])
            build_legistorm_employment_checkpoint(valid_path, root / "checkpoint")
            generation = _current_generation(root / "checkpoint")
            observations_path = (
                generation / "legistorm_employment_observations.ndjson.gz"
            )
            observations_path.write_bytes(observations_path.read_bytes() + b"corrupt")
            with self.assertRaisesRegex(EmploymentHistoryError, "file binding failed"):
                build_legistorm_employment_checkpoint(valid_path, root / "checkpoint")


class EmploymentTemporalMathTests(unittest.TestCase):
    def test_source_end_inclusive_becomes_next_day_exclusive(self):
        self.assertEqual(
            normalize_source_date(
                "2024-01-02", precision="day", boundary="end", end_semantics="inclusive"
            ),
            (date(2024, 1, 3), date(2024, 1, 3)),
        )
        self.assertEqual(
            normalize_source_date("2024-08", precision="month", boundary="start"),
            (date(2024, 8, 1), date(2024, 8, 31)),
        )

    def test_union_and_half_open_adjacency_do_not_inflate_duration(self):
        ranges = union_ranges([
            (date(2024, 1, 1), date(2024, 1, 10)),
            (date(2024, 1, 1), date(2024, 1, 10)),
            (date(2024, 1, 10), date(2024, 1, 20)),
        ])
        self.assertEqual(ranges, [(date(2024, 1, 1), date(2024, 1, 20))])
        adjacent_left = [_episode("a", "p1", "c", "member_office", "2024-01-01", "2024-01-01", "2024-01-10", "2024-01-10", member="A000001")]
        adjacent_right = [_episode("b", "p2", "c", "member_office", "2024-01-10", "2024-01-10", "2024-01-20", "2024-01-20", member="A000001")]
        guaranteed, possible, _, _ = co_tenure_bounds(adjacent_left, adjacent_right)
        self.assertEqual((guaranteed, possible), (0, 0))

    def test_possible_only_overlap_has_zero_guaranteed_days(self):
        left = [_episode("a", "p1", "c", "member_office", "2024-01-01", "2024-01-31", "2024-03-01", "2024-03-31", member="A000001")]
        right = [_episode("b", "p2", "c", "member_office", "2024-02-01", "2024-03-01", "2024-04-01", "2024-04-30", member="A000001")]
        guaranteed, possible, _, _ = co_tenure_bounds(left, right)
        self.assertEqual(guaranteed, 0)
        self.assertGreater(possible, 0)

    def test_gordon_gap_fixture_is_211_to_241_days(self):
        hill = _episode("hill", "gordon", "sykes", "member_office", "2023-01-03", "2023-01-03", "2024-01-03", "2024-01-03", member="S001205")
        industry = _episode("industry", "gordon", "acu", "registrant", "2024-08-01", "2024-08-31", "2026-09-01", "2026-09-01", organization="lda:registrant:1")
        status, gap_min, gap_max = transition_gap_bounds(hill, industry)
        self.assertEqual((status, gap_min, gap_max), ("GUARANTEED", 211, 241))

    def test_sidecar_counts_people_once_and_keeps_roles_distinct(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            episodes_path = root / "episodes.ndjson.gz"
            episodes = [
                _episode("p1-hill", "p1", "office", "member_office", "2024-01-01", "2024-01-01", "2024-01-11", "2024-01-11", member="A000001"),
                _episode("p2-hill", "p2", "office", "member_office", "2024-01-05", "2024-01-05", "2024-01-15", "2024-01-15", member="A000001"),
                _episode("p1-firm", "p1", "firm", "registrant", "2024-02-01", "2024-02-01", "2024-03-01", "2024-03-01", organization="lda:registrant:7"),
                _episode("p1-client", "p1", "client-event", "client_activity", "2024-02-05", "2024-02-05", "2024-02-06", "2024-02-06", organization="lda:client:8"),
            ]
            _write_gzip(episodes_path, episodes)
            receipt = build_temporal_sidecar(
                episodes_path,
                root / "output",
                as_of="2026-08-31",
                generated_at="2026-08-31T00:00:00Z",
            )
            self.assertEqual(receipt["co_tenure_status_counts"]["GUARANTEED"], 1)
            self.assertEqual(receipt["revolving_status_counts"]["GUARANTEED"], 2)
            with gzip.open(root / "output" / "temporal_edges.ndjson.gz", "rt", encoding="utf-8") as stream:
                edges = [json.loads(line) for line in stream]
            predicates = {row["predicate"] for row in edges}
            self.assertEqual(predicates, {
                "CO_TENURE",
                "FORMER_STAFFER_EMPLOYED_BY_REGISTRANT",
                "FORMER_STAFFER_NAMED_ON_ACTIVITY_FOR_CLIENT",
            })
            revolving = [row for row in edges if row["predicate"] != "CO_TENURE"]
            self.assertTrue(all(row["contributing_person_count"] == 1 for row in revolving))

    def test_sidecar_retains_interval_blocked_revolving_path_without_weight(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            episodes_path = root / "episodes.ndjson.gz"
            hill = _episode(
                "hill", "p1", "office", "member_office",
                "2020-01-01", "2020-01-01", "2021-01-01", "2021-01-01",
                member="A000001",
            )
            hill["end_earliest"] = None
            hill["end_latest"] = None
            hill["end_precision"] = "unknown"
            industry = _episode(
                "industry", "p1", "firm", "registrant",
                "2022-01-01", "2022-01-01", "2023-01-01", "2023-01-01",
                organization="lda:registrant:7",
            )
            _write_gzip(episodes_path, [hill, industry])

            receipt = build_temporal_sidecar(
                episodes_path,
                root / "output",
                as_of="2026-08-31",
                generated_at="2026-08-31T00:00:00Z",
            )
            self.assertEqual(receipt["revolving_candidate_keys"], 1)
            self.assertEqual(
                receipt["revolving_status_counts"],
                {
                    "GUARANTEED": 0,
                    "POSSIBLE_ONLY": 0,
                    "WRONG_ORDER": 0,
                    "INTERVAL_BLOCKED": 1,
                },
            )
            with gzip.open(
                root / "output" / "temporal_paths.ndjson.gz", "rt", encoding="utf-8"
            ) as stream:
                paths = [json.loads(line) for line in stream]
            revolving = [row for row in paths if row["predicate"] != "CO_TENURE"]
            self.assertEqual(len(revolving), 1)
            self.assertEqual(revolving[0]["status"], "INTERVAL_BLOCKED")
            self.assertIsNone(revolving[0]["gap_min_days"])
            self.assertIsNone(revolving[0]["gap_max_days"])
            self.assertEqual(revolving[0]["primary_weight"], 0.0)
            self.assertEqual(revolving[0]["possible_weight"], 0.0)
            with gzip.open(
                root / "output" / "temporal_edges.ndjson.gz", "rt", encoding="utf-8"
            ) as stream:
                edges = [json.loads(line) for line in stream]
            self.assertFalse(
                any(row["predicate"] != "CO_TENURE" for row in edges)
            )


if __name__ == "__main__":
    unittest.main()
