# -*- coding: utf-8 -*-
"""The Haiku reader and its kappa gate (owner ruling D-219, 2026-10-15).  The Claude CLI is always faked."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import sys

import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from stance import press_haiku_rater as HR

N = HR.GATE_SAMPLE_N


def reading(label, confidence="high"):
    return {"target": "t", "stance_toward_target": label, "target_cu_valence": 1, "cu_alignment": label,
            "attribution": "member-own", "confidence": confidence, "rationale": "r"}


class FakeCli:
    """classify_json stand-in: answers from a script keyed by the item's full text; counts calls."""

    def __init__(self, answer, fail_at=None, fail_with=None):
        self.answer, self.fail_at, self.fail_with, self.calls, self.users = answer, fail_at, fail_with, 0, []

    def __call__(self, system, user, schema, model="sonnet"):
        assert model == "haiku" and system == HR.SYSTEM and schema == HR.SCHEMA
        self.calls += 1
        self.users.append(user)
        if self.fail_at is not None and self.calls == self.fail_at:
            raise self.fail_with
        return self.answer(user), {"transport": "claude-cli"}


def write_gold(tmp_path):
    """87 held-out rows (labels cycle support/oppose/neutral), 5 train rows and 1 unlabelled test row that must be ignored."""
    rows = []
    for index in range(N):
        label = HR.LABELS[index % 3]
        rows.append({"split": "test", "human_label": label, "url": "https://gold.example.test/%d" % index,
                     "title": "gold %d" % index, "text_snippet": "%s snippet %d" % (label, index),
                     "member": "M", "date": "2026-01-01"})
    rows += [{"split": "train", "human_label": "support", "url": "https://gold.example.test/train%d" % i,
              "title": "train", "text_snippet": "train", "member": "M", "date": ""} for i in range(5)]
    rows.append({"split": "test", "human_label": "NA", "url": "https://gold.example.test/bad", "title": "bad",
                 "text_snippet": "bad", "member": "M", "date": ""})
    path = tmp_path / "cu_stance_label_set.csv"
    with io.open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["split", "human_label", "url", "title", "text_snippet", "member", "date"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def paths(tmp_path):
    return dict(gold_path=str(write_gold(tmp_path)), gate_path=str(tmp_path / "gate.json"),
                calls_path=str(tmp_path / "gate_calls.jsonl"))


def echo(user):
    """Answer with the gold label the snippet names."""
    return reading(user.split("Full text: ")[1].split(" snippet")[0])


def test_the_thresholds_are_named_constants_for_paul():
    assert HR.KAPPA_MIN == 0.60 and HR.ACCURACY_MIN == 0.75 and HR.DEFAULT_BUDGET == 60 and N == 87


def test_cohen_kappa_matches_hand_computation():
    gold = ["support"] * 4 + ["oppose"] * 4 + ["neutral"] * 2
    assert HR.cohen_kappa(gold, gold) == 1.0
    predicted = ["support"] * 3 + ["oppose"] + ["oppose"] * 3 + ["neutral"] + ["neutral"] * 2
    # observed 8/10; chance = (4*3 + 4*4 + 2*3)/100 = 0.34; kappa = (0.8 - 0.34)/0.66
    assert HR.cohen_kappa(gold, predicted) == pytest.approx((0.8 - 0.34) / 0.66)
    assert HR.cohen_kappa(["support"] * 3, ["support"] * 3) == 1.0


def test_the_sample_is_the_held_out_split_with_valid_human_labels_only(tmp_path):
    sample = HR.gold_sample(str(write_gold(tmp_path)))
    assert len(sample) == N and all(row["split"] == "test" for row in sample)
    assert all(row["human_label"] in HR.LABELS for row in sample)


def test_a_faithful_reader_passes_the_gate_and_is_admitted(tmp_path):
    cli = FakeCli(echo)
    gate = HR.run_gate(classify=cli, **paths(tmp_path))
    assert gate["status"] == "scored" and gate["passed"] is True
    assert gate["accuracy"] == 1.0 and gate["kappa"] == 1.0 and gate["n"] == N and cli.calls == N
    assert gate["rater_version"] == HR.RATER_VERSION and gate["model"] == "haiku"
    stored = json.loads(Path(paths(tmp_path)["gate_path"]).read_text(encoding="utf-8"))
    assert stored["kappa_min"] == 0.60 and stored["accuracy_min"] == 0.75
    HR._append_jsonl(str(tmp_path / "cache.jsonl"), {"v": HR.RATER_VERSION, "k": "abc", "out": reading("support")})
    slot, admitted, detail = HR.admitted_slot(str(tmp_path / "cache.jsonl"), str(tmp_path / "gate.json"),
                                              str(tmp_path / "cu_stance_label_set.csv"))
    assert admitted is True and slot == {"abc": reading("support")} and "kappa 1.000" in detail


def test_a_reader_below_the_thresholds_is_not_admitted_and_its_slot_stays_empty(tmp_path):
    cli = FakeCli(lambda user: reading("neutral"))                      # right on the 29 neutrals only
    gate = HR.run_gate(classify=cli, **paths(tmp_path))
    assert gate["passed"] is False and gate["kappa"] == pytest.approx(0.0) and gate["accuracy"] == pytest.approx(29 / 87)
    HR._append_jsonl(str(tmp_path / "cache.jsonl"), {"v": HR.RATER_VERSION, "k": "abc", "out": reading("support")})
    slot, admitted, detail = HR.admitted_slot(str(tmp_path / "cache.jsonl"), str(tmp_path / "gate.json"),
                                              str(tmp_path / "cu_stance_label_set.csv"))
    assert admitted is False and slot == {} and "kappa 0.000" in detail


def test_kappa_and_accuracy_must_both_clear_their_floor(tmp_path, monkeypatch):
    # every 4th reading wrong (21 of 87): accuracy 0.759 and a kappa near 0.64; raising either floor alone rejects it
    wrong = {"i": 0}

    def noisy(user):
        label = user.split("Full text: ")[1].split(" snippet")[0]
        wrong["i"] += 1
        return reading({"support": "oppose", "oppose": "neutral", "neutral": "support"}[label] if wrong["i"] % 4 == 0 else label)

    gate = HR.run_gate(classify=FakeCli(noisy), **paths(tmp_path))
    assert gate["passed"] is True and 0.75 <= gate["accuracy"] < 0.8 and gate["kappa"] >= 0.6
    where = (str(tmp_path / "gate.json"), str(tmp_path / "cu_stance_label_set.csv"))
    assert HR.gate_status(*where)[0] is True
    monkeypatch.setattr(HR, "ACCURACY_MIN", 0.90)               # Paul raises a floor: the STORED result is re-judged
    assert HR.gate_status(*where)[0] is False
    monkeypatch.setattr(HR, "ACCURACY_MIN", 0.75)
    monkeypatch.setattr(HR, "KAPPA_MIN", 0.95)
    assert HR.gate_status(*where)[0] is False


def test_unrelated_is_scored_as_neutral_against_the_three_label_gold(tmp_path):
    def unrelated_for_neutral(user):
        label = user.split("Full text: ")[1].split(" snippet")[0]
        return reading("unrelated" if label == "neutral" else label)

    gate = HR.run_gate(classify=FakeCli(unrelated_for_neutral), **paths(tmp_path))
    assert gate["accuracy"] == 1.0 and gate["passed"] is True


def test_the_gate_runs_once_per_rater_version(tmp_path, monkeypatch):
    cli = FakeCli(echo)
    HR.run_gate(classify=cli, **paths(tmp_path))
    again = HR.run_gate(classify=cli, **paths(tmp_path))
    assert again["status"] == "already_gated" and cli.calls == N
    monkeypatch.setattr(HR, "RATER_VERSION", "haiku-ftfix-v2-other")
    assert HR.gate_status(str(tmp_path / "gate.json"), str(tmp_path / "cu_stance_label_set.csv"))[0] is False
    HR.run_gate(classify=cli, **paths(tmp_path))
    assert cli.calls == 2 * N, "a new rater version is gated again"


def test_a_quota_stop_keeps_what_was_paid_for_and_the_next_run_resumes(tmp_path):
    quota = HR.ClassifierQuotaExhausted("spend limit reached")
    first = HR.run_gate(classify=FakeCli(echo, fail_at=41, fail_with=quota), **paths(tmp_path))
    assert first["status"] == "incomplete" and first["readings"] == 40 and "quota_exhausted" in first["stopped"]
    assert not Path(paths(tmp_path)["gate_path"]).exists(), "no gate record without every reading"
    cli = FakeCli(echo)
    second = HR.run_gate(classify=cli, **paths(tmp_path))
    assert second["status"] == "scored" and cli.calls == N - 40


def test_the_gate_budget_caps_new_calls(tmp_path):
    cli = FakeCli(echo)
    result = HR.run_gate(classify=cli, budget=10, **paths(tmp_path))
    assert result["status"] == "incomplete" and cli.calls == 10 and result["stopped"] == "budget"


def test_a_changed_gold_sample_is_refused_not_silently_rescored(tmp_path):
    gold = write_gold(tmp_path)
    text = gold.read_text(encoding="utf-8-sig").replace("test,support,https://gold.example.test/0,", "train,support,https://gold.example.test/0,", 1)
    gold.write_text(text, encoding="utf-8-sig")
    with pytest.raises(RuntimeError, match="gold sample has 86 rows"):
        HR.run_gate(classify=FakeCli(echo), gold_path=str(gold), gate_path=str(tmp_path / "g.json"),
                    calls_path=str(tmp_path / "c.jsonl"))


def test_rate_turns_a_limit_into_a_quota_stop_and_rejects_off_schema_output():
    def limited(*args, **kwargs):
        raise RuntimeError("Classifier failed: You've hit your usage limit")

    with pytest.raises(HR.ClassifierQuotaExhausted):
        HR.rate("u", limited)
    with pytest.raises(RuntimeError, match="boom"):
        HR.rate("u", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(ValueError, match="cu_alignment"):
        HR.rate("u", lambda *a, **k: ({**reading("support"), "cu_alignment": "maybe"}, {}))
    assert HR.rate("u", lambda *a, **k: ({**reading("oppose"), "extra": 1}, {})) == reading("oppose")


def pass_the_gate(tmp_path):
    HR.run_gate(classify=FakeCli(echo), **paths(tmp_path))
    return dict(cache_path=str(tmp_path / "cache.jsonl"), gate_path=str(tmp_path / "gate.json"),
                gold_path=str(tmp_path / "cu_stance_label_set.csv"))


def refresh_rows(count):
    return [{"url": "https://m.house.gov/r%d" % i, "title": "Release %d" % i, "member": "M", "party": "D",
             "date": "2026-07-01", "source": HR.REFRESH_SOURCE} for i in range(count)]


def test_fill_stops_at_the_call_budget(tmp_path):
    options = pass_the_gate(tmp_path)
    cli = FakeCli(lambda user: reading("support"))
    result = HR.run_fill(refresh_rows(5), {}, set(), classify=cli, budget=2, **options)
    assert result["attempted"] == 2 and result["rated"] == 2 and result["stopped"] == "budget"
    assert result["unrated_remaining"] == 3 and cli.calls == 2
    assert len(HR.load_slot(options["cache_path"])) == 2
    cli_two = FakeCli(lambda user: reading("support"))
    rest = HR.run_fill(refresh_rows(5), {}, set(), classify=cli_two, budget=60, **options)
    assert cli_two.calls == 3 and rest["rated"] == 3 and rest["stopped"] is None     # rows already read are not re-rated


def test_fill_stops_on_quota_and_after_repeated_errors(tmp_path):
    options = pass_the_gate(tmp_path)
    quota = FakeCli(lambda user: reading("support"), fail_at=2, fail_with=HR.ClassifierQuotaExhausted("limit"))
    result = HR.run_fill(refresh_rows(5), {}, set(), classify=quota, budget=60, **options)
    assert result["rated"] == 1 and result["stopped"] == "quota_exhausted" and quota.calls == 2
    broken = FakeCli(lambda user: (_ for _ in ()).throw(ValueError("bad json")))
    result = HR.run_fill(refresh_rows(8)[3:], {}, set(), classify=broken, budget=60, **options)
    assert result["stopped"] == "errors" and result["errors"] == HR.MAX_CONSECUTIVE_ERRORS and broken.calls == 3


def test_fill_makes_no_call_unless_the_gate_passed(tmp_path):
    cli = FakeCli(lambda user: reading("support"))
    result = HR.run_fill(refresh_rows(3), {}, set(), classify=cli, cache_path=str(tmp_path / "cache.jsonl"),
                         gate_path=str(tmp_path / "missing-gate.json"), gold_path=str(write_gold(tmp_path)))
    assert result["status"] == "gate_not_passed" and cli.calls == 0
    failing = FakeCli(lambda user: reading("neutral"))
    HR.run_gate(classify=failing, **paths(tmp_path))
    result = HR.run_fill(refresh_rows(3), {}, set(), classify=cli, cache_path=str(tmp_path / "cache.jsonl"),
                         gate_path=str(tmp_path / "gate.json"), gold_path=str(tmp_path / "cu_stance_label_set.csv"))
    assert result["status"] == "gate_not_passed" and cli.calls == 0


def test_fill_rates_only_refresh_rows_that_have_no_reading(tmp_path):
    options = pass_the_gate(tmp_path)
    rows = refresh_rows(3) + [{"url": "https://hist.example.test/h", "title": "H", "source": ""}]
    fable_keys = {HR.url_key(rows[0]["url"])}                                # a Fable reading already exists
    cli = FakeCli(lambda user: reading("oppose"))
    result = HR.run_fill(rows, {"https://m.house.gov/r1": "the full body text"}, fable_keys, classify=cli,
                         budget=60, **options)
    assert cli.calls == 2 and result["rated"] == 2
    assert "Full text: the full body text" in cli.users[0]                  # the body, else the headline
    assert "Full text: Release 2" in cli.users[1]
    assert set(HR.load_slot(options["cache_path"])) == {HR.url_key(rows[1]["url"]), HR.url_key(rows[2]["url"])}


def test_the_call_goes_through_the_cli_classifier_and_never_the_api():
    source = Path(HR.__file__).read_text(encoding="utf-8")
    assert "classifier_runtime" in source and "classify_json" in source
    assert "import anthropic" not in source and "ANTHROPIC_API_KEY" not in source
