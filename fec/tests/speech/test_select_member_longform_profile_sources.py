import copy
import json
from pathlib import Path

import pytest

from fec.speech import select_member_longform_profile_sources as mod


STREAMS = ("primary", "retry", "retry2", "retry3", "leader")


def dump_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def dump_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def episode(tmp_path, member, source_id, *, positive=True, recorded_at=None):
    """Real hash-bound files; no fake digests that bypass the gate."""
    directory = tmp_path / (member.replace(" ", "-") + "-" + source_id)
    directory.mkdir(exist_ok=True)
    wav, words = directory / "audio.wav", directory / "words.jsonl"
    wav.write_bytes((member + ":" + source_id + ":audio").encode())
    dump_jsonl(words, [{"word": member, "start_sec": 1, "end_sec": 2}])
    acquisition = {"member": member, "source_id": source_id,
                   "status": "ACQUIRED_IDENTITY_UNVERIFIED", "wav": str(wav),
                   "wav_sha256": mod.SMR.sha256_file(wav),
                   "title": f"Source {source_id} for {member}",
                   "url": f"https://example.test/{source_id}"}
    if recorded_at:
        acquisition["recorded_at"] = recorded_at
    transcript = {**acquisition, "status": "LOCAL_ASR_READY_IDENTITY_UNVERIFIED",
                  "transcript": str(words), "transcript_sha256": mod.SMR.sha256_file(words)}
    cue = {**transcript,
           "status": "HARD_IN_BAND_NAME_CUE_CANDIDATE" if positive else "NO_FULL_NAME_CUE_FOUND",
           "cues": [{"phrase": member, "start_sec": 1, "end_sec": 2}] if positive else []}
    return acquisition, transcript, cue


class Scenario:
    def __init__(self, tmp_path, monkeypatch, members, roles=None, leader_sources=None):
        self.root, self.monkeypatch = tmp_path, monkeypatch
        self.streams = {s: {k: [] for k in ("acquisition", "transcripts", "cues")} for s in STREAMS}
        self.paths = {s: {k: tmp_path / f"{s}-{k}.jsonl" for k in ("acquisition", "transcripts", "cues")} for s in STREAMS}
        self.roster, self.roles = tmp_path / "roster.json", tmp_path / "roles.json"
        self.sources = tmp_path / "leader-sources.jsonl"
        self.queue, self.rejected = tmp_path / "primary-queue.jsonl", tmp_path / "rejected.jsonl"
        self.quality = tmp_path / "quality-holds.jsonl"
        self.out, self.receipt = tmp_path / "out.jsonl", tmp_path / "receipt.json"
        self.queue_rows, self.rejected_rows, self.quality_rows = [], [], []
        dump_json(self.roster, {"members": {m: {} for m in members}})
        dump_json(self.roles, {"members": roles or {}})
        dump_jsonl(self.sources, leader_sources or [])

    def add(self, label, member, source_id, **kwargs):
        values = episode(self.root, member, source_id, **kwargs)
        for kind, row in zip(("acquisition", "transcripts", "cues"), values):
            self.streams[label][kind].append(row)
        return values

    def run(self):
        args = ["prog", "--roster", str(self.roster), "--roles", str(self.roles),
                "--leader-sources", str(self.sources), "--primary-queue", str(self.queue),
                "--rejected-sources", str(self.rejected), "--quality-holds", str(self.quality),
                "--out", str(self.out), "--receipt", str(self.receipt)]
        dump_jsonl(self.queue, self.queue_rows)
        dump_jsonl(self.rejected, self.rejected_rows)
        dump_jsonl(self.quality, self.quality_rows)
        for label in STREAMS:
            for kind in ("acquisition", "transcripts", "cues"):
                path = self.paths[label][kind]
                dump_jsonl(path, self.streams[label][kind])
                args.extend([f"--{label}-{kind}", str(path)])
        self.monkeypatch.setattr("sys.argv", args)
        assert mod.main() == 0
        return {r["member"]: r for r in mod.optional_rows(self.out)}


def test_selection_prefers_prerole_for_leader_and_retry_for_nonleader(tmp_path, monkeypatch):
    s = Scenario(tmp_path, monkeypatch, ("Leader", "Member"),
                 roles={"Leader": {"current_role": "Chair", "current_role_assumed_on": "2020-01-01"}},
                 leader_sources=[{"member": "Leader", "status": "REVIEWED_SOURCE", "source_id": "old"}])
    s.add("primary", "Leader", "post-role", recorded_at="2022-01-01")
    s.add("primary", "Member", "one", positive=False)
    s.add("retry", "Member", "two")
    s.add("leader", "Leader", "old", recorded_at="2019-01-01")
    rows = s.run()
    assert rows["Leader"]["source_id"] == "old"
    assert rows["Leader"]["source_era"] == "PRE_CURRENT_LEADERSHIP_ROLE"
    assert rows["Member"]["source_id"] == "two"
    assert rows["Member"]["selection"] == "independent_second_source_after_primary_no_cue"
    assert all(r["speaker_identity_authority"] is False and r["semantic_authority"] is False for r in rows.values())


def test_leadership_gap_is_explicit(tmp_path, monkeypatch):
    s = Scenario(tmp_path, monkeypatch, ("Leader",),
                 roles={"Leader": {"current_role": "Whip", "current_role_assumed_on": "2005-01-03"}},
                 leader_sources=[{"member": "Leader", "status": "HISTORICAL_LONGFORM_SOURCE_GAP", "gap_reason": "restricted archive"}])
    row = s.run()["Leader"]
    assert row["status"] == "LEADERSHIP_PREROLE_SOURCE_GAP"
    assert row["profile_effect"] == "NONE"


def test_selection_uses_third_source_after_two_failed_cue_screens(tmp_path, monkeypatch):
    s = Scenario(tmp_path, monkeypatch, ("Member",))
    s.add("primary", "Member", "one", positive=False)
    s.add("retry", "Member", "two", positive=False)
    s.add("retry2", "Member", "three")
    row = s.run()["Member"]
    assert row["source_id"] == "three"
    assert row["selection"] == "independent_third_source_after_two_failed_cue_screens"


def test_selection_uses_fourth_source_only_after_retry2_and_hashes_retry3_inputs(tmp_path, monkeypatch):
    s = Scenario(tmp_path, monkeypatch, ("Third", "Fourth"))
    for member in ("Third", "Fourth"):
        s.add("primary", member, "one", positive=False)
        s.add("retry", member, "two", positive=False)
        s.add("retry2", member, "three", positive=member == "Third")
        s.add("retry3", member, "four")
    rows = s.run()
    assert rows["Fourth"]["source_id"] == "four"
    assert rows["Fourth"]["selection"] == "independent_fourth_source_after_three_failed_cue_screens"
    assert rows["Third"]["source_id"] == "three"
    inputs = json.loads(s.receipt.read_text())["inputs"]
    for kind in ("acquisition", "transcripts", "cues"):
        assert inputs[f"retry3_{kind}_sha256"] == mod.SMR.sha256_file(s.paths["retry3"][kind])


def test_fourth_source_transcript_pending_is_explicit(tmp_path, monkeypatch):
    s = Scenario(tmp_path, monkeypatch, ("Member",))
    for label, source_id in (("primary", "one"), ("retry", "two"), ("retry2", "three")):
        s.add(label, "Member", source_id, positive=False)
    s.streams["retry3"]["transcripts"] = [{"member": "Member", "source_id": "four"}]
    row = s.run()["Member"]
    assert row["status"] == "FOURTH_SOURCE_TRANSCRIPT_PENDING"
    assert row["source_id"] == "four"


@pytest.mark.parametrize("positive", [True, False])
@pytest.mark.parametrize("field", ["source_id", "wav_sha256"])
def test_stale_primary_cue_cannot_select_or_authorize_a_retry(tmp_path, monkeypatch, positive, field):
    s = Scenario(tmp_path, monkeypatch, ("Member",))
    _, _, cue = s.add("primary", "Member", "new", positive=positive)
    cue[field] = "old-rejected-value"
    s.add("retry", "Member", "valid-retry")
    with pytest.raises(SystemExit, match="source lineage mismatch"):
        s.run()
    assert not s.out.exists() and not s.receipt.exists()


def test_old_transcript_with_new_audio_is_rejected(tmp_path, monkeypatch):
    s = Scenario(tmp_path, monkeypatch, ("Member",))
    _, transcript, _ = s.add("primary", "Member", "new")
    transcript["source_id"] = "old"
    with pytest.raises(SystemExit, match="transcript lineage mismatch"):
        s.run()
    assert not s.out.exists()


@pytest.mark.parametrize("field", ["wav", "transcript"])
def test_physical_media_and_transcript_tampering_are_rejected(tmp_path, monkeypatch, field):
    s = Scenario(tmp_path, monkeypatch, ("Member",))
    _, _, cue = s.add("primary", "Member", "one")
    Path(cue[field]).write_bytes(b"tampered bytes")
    with pytest.raises(SystemExit, match=f"physical {field} hash mismatch"):
        s.run()
    assert not s.out.exists()


def test_different_transcript_path_is_rejected_even_when_hash_matches(tmp_path, monkeypatch):
    s = Scenario(tmp_path, monkeypatch, ("Member",))
    _, transcript, cue = s.add("primary", "Member", "one")
    copied = tmp_path / "copy.jsonl"
    copied.write_bytes(Path(cue["transcript"]).read_bytes())
    transcript["transcript"] = str(copied)
    with pytest.raises(SystemExit, match="path mismatch"):
        s.run()


def test_direct_source_record_call_cannot_join_old_cue_to_replacement_audio(tmp_path):
    old, new = episode(tmp_path, "Member", "old"), episode(tmp_path, "Member", "new")
    with pytest.raises(SystemExit, match="source lineage mismatch"):
        mod.source_record(old[2], new[0], selection="primary", source_era="TEST")


def test_reviewed_primary_must_match_current_approved_queue(tmp_path, monkeypatch):
    s = Scenario(tmp_path, monkeypatch, ("Member",))
    s.add("primary", "Member", "superseded")
    s.queue_rows = [{"member": "Member", "primary": {"source_id": "replacement", "reviewed_replacement": True}}]
    with pytest.raises(SystemExit, match="approved primary replacement not acquired"):
        s.run()
    assert not s.out.exists()


def test_superseded_retry_excluded_without_blocking_independent_retry_or_other_member(tmp_path, monkeypatch):
    s = Scenario(tmp_path, monkeypatch, ("Member", "Other"))
    for member in ("Member", "Other"):
        s.add("primary", member, "approved", positive=False)
        s.add("retry", member, "superseded")
        s.queue_rows.append({"member": member, "primary": {"source_id": "approved", "reviewed_replacement": True}})
    s.add("retry2", "Member", "independent")
    s.rejected_rows = [{"member": "Member", "source_id": "superseded", "status": "SUPERSEDED_PRIMARY_PENDING_REVIEW"}]
    rows = s.run()
    assert rows["Member"]["source_id"] == "independent"
    assert rows["Other"]["source_id"] == "superseded"
    assert rows["Member"]["excluded_sources"] == [{"stream": "retry", "source_id": "superseded", "reason": "SUPERSEDED_OR_REJECTED_PRIMARY_PENDING_REVIEW", "media_preserved": True}]
    assert "excluded_sources" not in rows["Other"]
    receipt = json.loads(s.receipt.read_text())
    assert receipt["inputs"]["rejected_sources_sha256"] == mod.SMR.sha256_file(s.rejected)
    assert receipt["inputs"]["primary_queue_sha256"] == mod.SMR.sha256_file(s.queue)
    assert receipt["contract"]["physical_audio_and_transcript_hashes_verified"] is True


def test_queue_embedded_superseded_source_is_also_excluded(tmp_path, monkeypatch):
    s = Scenario(tmp_path, monkeypatch, ("Member",))
    s.add("primary", "Member", "approved", positive=False)
    s.add("retry", "Member", "superseded")
    s.queue_rows = [{"member": "Member", "primary": {"source_id": "approved", "reviewed_replacement": True}, "rejected_source_ids": ["superseded"]}]
    row = s.run()["Member"]
    assert row["status"] == "NO_HARD_NAME_CUE_AFTER_AVAILABLE_SOURCES"
    assert row["excluded_sources"][0]["source_id"] == "superseded"


def test_manifest_change_during_selection_prevents_output(tmp_path, monkeypatch):
    s = Scenario(tmp_path, monkeypatch, ("Member",))
    s.add("primary", "Member", "one")
    original = mod.require_lineage

    def concurrent_change(*args, **kwargs):
        original(*args, **kwargs)
        path = s.paths["primary"]["acquisition"]
        path.write_text(path.read_text() + "\n", encoding="utf-8")

    monkeypatch.setattr(mod, "require_lineage", concurrent_change)
    with pytest.raises(SystemExit, match="input changed during verification"):
        s.run()
    assert not s.out.exists()


def test_duplicate_member_rows_are_rejected(tmp_path, monkeypatch):
    s = Scenario(tmp_path, monkeypatch, ("Member",))
    s.add("primary", "Member", "one")
    s.streams["primary"]["cues"].append(copy.deepcopy(s.streams["primary"]["cues"][0]))
    with pytest.raises(SystemExit, match="duplicate member"):
        s.run()


def test_exact_quality_hold_uses_independent_retry_and_binds_ledger(tmp_path, monkeypatch):
    s = Scenario(tmp_path, monkeypatch, ("Member",))
    _, _, cue = s.add("primary", "Member", "held")
    s.add("retry", "Member", "independent")
    s.quality_rows = [{key: cue[key] for key in ("member", "source_id", "wav_sha256", "transcript_sha256")}]
    s.quality_rows[0]["reason"] = "ASR repeated-text loop"
    row = s.run()["Member"]
    assert row["source_id"] == "independent"
    assert row["quality_held_sources"][0]["source_id"] == "held"
    assert row["quality_held_sources"][0]["reason"] == "ASR repeated-text loop"
    receipt = json.loads(s.receipt.read_text())
    assert receipt["inputs"]["quality_holds_sha256"] == mod.SMR.sha256_file(s.quality)


@pytest.mark.parametrize("leader", [False, True])
def test_quality_hold_without_alternative_stays_explicitly_pending(tmp_path, monkeypatch, leader):
    roles = {"Member": {"current_role": "Chair", "current_role_assumed_on": "2020-01-01"}} if leader else {}
    sources = [{"member": "Member", "source_id": "held"}] if leader else []
    s = Scenario(tmp_path, monkeypatch, ("Member",), roles=roles, leader_sources=sources)
    _, _, cue = s.add("leader" if leader else "primary", "Member", "held", recorded_at="2019-01-01")
    s.quality_rows = [{key: cue[key] for key in ("member", "source_id", "wav_sha256", "transcript_sha256")}]
    row = s.run()["Member"]
    assert row["status"] == ("LEADERSHIP_PREROLE_QUALITY_REVIEW_PENDING" if leader else "QUALITY_REVIEW_PENDING")
    assert row["profile_effect"] == "NONE"


@pytest.mark.parametrize("field", ["member", "source_id", "wav_sha256", "transcript_sha256"])
def test_quality_hold_is_exactly_bound_not_member_wide(tmp_path, monkeypatch, field):
    s = Scenario(tmp_path, monkeypatch, ("Member",))
    _, _, cue = s.add("primary", "Member", "current")
    s.quality_rows = [{key: cue[key] for key in ("member", "source_id", "wav_sha256", "transcript_sha256")}]
    s.quality_rows[0][field] = "different"
    row = s.run()["Member"]
    assert row["status"] == "SELECTED_HARD_NAME_CUE_CANDIDATE"
    assert "quality_held_sources" not in row
    assert row["semantic_authority"] is False


def test_quality_hold_with_missing_binding_is_not_silently_ignored(tmp_path, monkeypatch):
    s = Scenario(tmp_path, monkeypatch, ("Member",))
    s.add("primary", "Member", "current")
    s.quality_rows = [{"member": "Member", "source_id": "current"}]
    with pytest.raises(SystemExit, match="quality hold lacks exact"):
        s.run()
