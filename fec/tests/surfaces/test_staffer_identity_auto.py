import hashlib

from fec.surfaces.staffer_identity_auto import resolve_identities, compatible

PROOF = {"path": "/verified/source", "sha256": "verified-source-sha"}


def person(label="Jamie Example", **extra):
    return {"label": label, "kind": "person", "provenance": PROOF, **extra}


def run(nodes, **extra):
    return resolve_identities(nodes, extra.get("episodes", []), extra.get("references", []),
                              extra.get("confirmed_aliases", {}), extra.get("keep_separate_pairs", []))


def biography(text, **extra):
    return {"name": "Jamie Example", "title": "Jamie Example", "text": text,
            "sha256": hashlib.sha256(text.encode()).hexdigest(), "url": "https://official.example/bio",
            "retrieved_at": "2026-09-04T00:00:00Z", "source_kind": "official_biography", "subject_scoped": True, **extra}


def test_homonyms_are_not_merged_on_names_or_firms():
    nodes = {"a": person(source_summary="Shared Corporation"), "b": person(source_summary="Shared Corporation")}
    result = run(nodes, references=[biography("Jamie Example works for Shared Corporation.")])
    assert not result["identity_groups"]
    assert result["unresolved_candidates"]


def test_official_subject_bio_bridges_old_new_jobs_with_citations():
    nodes = {"a": person(source_summary="Old Organization Inc"), "b": person(source_summary="New Organization LLC")}
    reference = biography("Jamie Example worked for Old Organization and now serves New Organization.")
    result = run(nodes, references=[reference])
    assert result["identity_aliases"] == {"b": "a"}
    group = result["identity_groups"][0]
    assert group["status"] == "REFERENCE_RESOLVED_SAME_PERSON"
    assert group["provenance"]["citations"][0]["url"] == reference["url"]
    assert not group["canonical_identity_promoted"]
    reference["sha256"] = "bad"
    assert not run(nodes, references=[reference])["identity_groups"]


def test_multisubject_page_cannot_bridge_careers():
    reference = biography("Jamie Example: Old Organization. Other Person: New Organization.",
                          subject_scoped=False, title="Leadership directory")
    nodes = {"a": person(source_summary="Old Organization"), "b": person(source_summary="New Organization")}
    assert not run(nodes, references=[reference])["identity_groups"]


def test_middle_initial_nickname_and_contradictions():
    assert compatible(person("Greg J Mesack"), person("Gregory James Mesack"))
    assert not compatible(person("Greg J Mesack"), person("Gregory Robert Mesack"))
    assert not compatible(person("Jamie Example Jr"), person("Jamie Example Sr"))
    assert not compatible(person(dob="1980-01-01"), person(date_of_birth="1981-01-01"))


def test_verified_overlap_needs_actual_employment_not_filing_year():
    nodes = {"a": person(), "b": person()}
    episodes = [{"person_id": i, "employer_id": "office:1", "title": "Legislative Director",
        "start": "2020-01-01", "end_exclusive": "2021-01-01", "provenance": PROOF} for i in nodes]
    assert not run(nodes, episodes=episodes)["identity_groups"]
    for ep in episodes:
        ep["employment_verified"] = True
    assert run(nodes, episodes=episodes)["identity_groups"]
    episodes[1]["start"] = "2021-01-01"
    assert not run(nodes, episodes=episodes)["identity_groups"]


def test_distinctive_history_and_component_denial_prevent_false_bridge():
    h1 = "Legislative director to Representative Long Example and committee staff director of agriculture from 2010 to 2015"
    h2 = "Senior counsel to Senator Different Example and international affairs adviser at another organization from 2016 to 2020"
    nodes = {"a": person(covered_positions=[h1], lda_source_person_id="lda:lobbyist:a"),
             "b": person(covered_positions=[h1, h2], lda_source_person_id="lda:lobbyist:b"),
             "c": person(covered_positions=[h2], lda_source_person_id="lda:lobbyist:c")}
    result = run(nodes)
    assert len(result["identity_groups"]) == 1
    assert len(result["identity_groups"][0]["alias_ids"]) == 1
    assert any(r["reason"] == "TRANSITIVE_COMPONENT_CONFLICT" for r in result["unresolved_candidates"])
    blocked = run({"a": nodes["a"], "b": nodes["b"]}, keep_separate_pairs=[["a", "b"]])
    assert not blocked["identity_groups"]


def test_shared_unique_external_key_requires_verified_uniqueness():
    key = {"namespace": "official-directory", "value": "123", "verified_unique": True, "provenance": PROOF}
    result = run({"a": person(external_ids=[key]), "b": person(external_ids=[key])})
    assert result["identity_aliases"] == {"b": "a"}
    assert not run({i: person(external_ids=[key]) for i in "abc"})["identity_groups"]


def test_generic_dba_legal_suffix_and_quote_variants_bridge():
    nodes = {"a": person(source_summary="Example Associates PA"),
             "b": person(source_summary="OLDER LEGAL NAME INC DBA AMERICA'S CREDIT UNIONS")}
    result = run(nodes, references=[biography("Jamie Example left Example Associates for America’s Credit Unions.")])
    assert result["identity_aliases"] == {"b": "a"}


def test_public_biographies_need_two_independent_domains_and_same_anchors():
    nodes = {"a": person(source_summary="Old Organization"), "b": person(source_summary="New Organization")}
    first = biography("Jamie Example left Old Organization for New Organization.",
                      source_kind="public_biography_candidate", url="https://one.co.uk/bio")
    second = biography("Profile: Jamie Example previously served Old Organization and now leads New Organization.",
                       source_kind="public_biography_candidate", url="https://two.co.uk/jamie")
    assert not run(nodes, references=[first])["identity_groups"]
    result = run(nodes, references=[first, second])
    evidence = result["identity_groups"][0]["provenance"]["evidence"][0]
    assert evidence["publisher_domains"] == ["one.co.uk", "two.co.uk"]
    assert len(evidence["citations"]) == 2
    second["url"] = "https://staff.one.co.uk/jamie"
    assert not run(nodes, references=[first, second])["identity_groups"]


def test_public_same_publisher_or_syndicated_text_not_independent():
    nodes = {"a": person(source_summary="Old Organization"), "b": person(source_summary="New Organization")}
    first = biography("Jamie Example left Old Organization for New Organization.",
                      source_kind="public_biography_candidate", url="https://one.example/bio", publisher_id="same-owner")
    second = {**first, "url": "https://two.example/bio"}
    assert not run(nodes, references=[first, second])["identity_groups"]
    second = biography("Profile Jamie Example: formerly Old Organization, now New Organization.",
                       source_kind="public_biography_candidate", url="https://two.example/bio", publisher_id="same-owner")
    assert not run(nodes, references=[first, second])["identity_groups"]
    second.pop("publisher_id")
    second["subject_scoped"] = False
    assert not run(nodes, references=[first, second])["identity_groups"]


def test_related_organization_spellings_cannot_prove_career_bridge():
    nodes = {"a": person(source_summary="American Bankers"),
             "b": person(source_summary="American Bankers Association")}
    reference = biography("Jamie Example serves the American Bankers Association.")
    assert not run(nodes, references=[reference])["identity_groups"]


def test_organization_substring_inside_word_does_not_match():
    nodes = {"a": person(source_summary="National Association"),
             "b": person(source_summary="Old Organization")}
    reference = biography("Jamie Example left Old Organization for International Association.")
    assert not run(nodes, references=[reference])["identity_groups"]


def test_copied_ambiguous_lda_history_is_not_independent_corroboration():
    history = "Legislative director to Representative Long Example and committee staff director of agriculture from 2010 to 2015"
    nodes = {i: person(covered_positions=[history], lda_source_person_id="lda:lobbyist:shared") for i in "abc"}
    assert not run(nodes)["identity_groups"]


def verified_overlap(nodes, end="2021-01-01"):
    return [{"person_id": i, "employer_id": "office:1", "title": "Legislative Director", "employment_verified": True,
             "start": "2020-01-01", "end_exclusive": end, "provenance": PROOF} for i in nodes]


def test_automatic_groups_name_their_proof_kind_and_keep_the_overlay_literal():
    nodes = {"a": person(), "b": person()}
    record = run(nodes, episodes=verified_overlap(nodes))
    group = record["identity_groups"][0]
    assert group["basis"] == "RECORD_RESOLVED"
    assert group["resolution_status"] == "RECORD_RESOLVED_SAME_PERSON"
    assert group["provenance"]["proof_kinds"] == ["record"]
    assert group["provenance"]["evidence_methods"] == ["VERIFIED_SAME_OFFICE_TITLE_OVERLAP"]
    assert group["provenance"]["basis"] == "RECORD_RESOLVED" and group["provenance"]["citations"] == []
    # apply_identity_overlay's status gate still keys on this literal; the proof kind sits beside it.
    assert group["status"] == "REFERENCE_RESOLVED_SAME_PERSON"
    assert record["counts"]["automatic_resolved_groups"] == 1
    assert record["counts"]["resolved_groups_by_basis"] == {"RECORD_RESOLVED": 1}
    assert "reference_resolved_groups" not in record["counts"]
    nodes = {"a": person(source_summary="Old Organization Inc"), "b": person(source_summary="New Organization LLC")}
    cited = run(nodes, references=[biography("Jamie Example worked for Old Organization and now serves New Organization.")])
    group = cited["identity_groups"][0]
    assert group["basis"] == "REFERENCE_RESOLVED" and group["resolution_status"] == "REFERENCE_RESOLVED_SAME_PERSON"
    assert group["provenance"]["proof_kinds"] == ["reference"] and group["provenance"]["citations"]
    assert cited["counts"]["resolved_groups_by_basis"] == {"REFERENCE_RESOLVED": 1}


def test_asserted_at_is_the_groups_own_evidence_date_never_an_unrelated_reference():
    unrelated = biography("Other Person joined Elsewhere Corporation.", name="Other Person", title="Other Person",
                          retrieved_at="2026-09-04T22:21:13+00:00")
    nodes = {"a": person(), "b": person()}
    group = run(nodes, episodes=verified_overlap(nodes, end="2021-01-01"), references=[unrelated])["identity_groups"][0]
    assert (group["asserted_at"], group["asserted_at_basis"]) == ("2021-01-01", "employment_overlap_end")
    assert group["provenance"]["asserted_at_basis"] == "employment_overlap_end"
    history = "Legislative director to Representative Long Example and committee staff director of agriculture from 2010 to 2015"
    nodes = {"a": person(covered_positions=[history], lda_source_person_id="lda:lobbyist:a",
                        provenance={**PROOF, "last_filing_posted_at": "2026-07-20T19:50:48Z"}),
             "b": person(covered_positions=[history], lda_source_person_id="lda:lobbyist:b",
                        provenance={**PROOF, "last_filing_posted_at": "2025-01-01T00:00:00Z"})}
    group = run(nodes, references=[unrelated])["identity_groups"][0]
    assert (group["asserted_at"], group["asserted_at_basis"]) == ("2026-07-20T19:50:48Z", "source_record_posted_at")
    nodes = {"a": person(source_summary="Old Organization Inc"), "b": person(source_summary="New Organization LLC")}
    own = biography("Jamie Example worked for Old Organization and now serves New Organization.",
                    retrieved_at="2026-09-04T22:06:29+00:00")
    group = run(nodes, references=[own, unrelated])["identity_groups"][0]
    assert (group["asserted_at"], group["asserted_at_basis"]) == ("2026-09-04T22:06:29+00:00", "reference_retrieved_at")
    key = {"namespace": "official-directory", "value": "123", "verified_unique": True, "provenance": PROOF}
    nodes = {"a": person(external_ids=[key]), "b": person(external_ids=[key])}
    group = resolve_identities(nodes, [], [unrelated], {}, [], resolved_at="2026-09-05T12:00:00+00:00")["identity_groups"][0]
    assert (group["asserted_at"], group["asserted_at_basis"]) == ("2026-09-05T12:00:00+00:00", "resolution_run_time")
    default = run(nodes, references=[unrelated])["identity_groups"][0]
    assert default["asserted_at_basis"] == "resolution_run_time" and default["asserted_at"] != unrelated["retrieved_at"]


def test_source_person_ids_use_the_source_id_vocabulary_for_every_namespace():
    nodes = {"person:lda:lobbyist:1": person(source_person_ids=["person:lda:lobbyist:1", "lda:lobbyist:3"]),
             "person:hill:person:abc": person()}
    group = run(nodes, episodes=verified_overlap(nodes))["identity_groups"][0]
    assert group["source_person_ids"] == ["hill:person:abc", "lda:lobbyist:1", "lda:lobbyist:3"]
    assert not any(i.startswith("person:") for i in group["source_person_ids"])


def test_middle_initial_pair_without_evidence_is_retrieved_but_stays_unresolved():
    # Guard for the retrieval widening in detect_candidates: merge evidence rules are unchanged.
    nodes = {"person:hill:person:k": person("Kelvin T Lassiter"), "politico:k": person("Kelvin Lassiter")}
    result = run(nodes)
    assert result["identity_groups"] == [] and result["identity_aliases"] == {}
    assert result["unresolved_candidates"] == [{"display_name": "Kelvin T Lassiter",
        "node_ids": ["person:hill:person:k", "politico:k"], "reason": "INSUFFICIENT_IDENTITY_EVIDENCE", "auto_merge": False}]
