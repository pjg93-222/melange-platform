# -*- coding: utf-8 -*-
"""build_press_engine_feed and Stage 5 (owner ruling D-219, 2026-10-15).

The feed runs the ratified Stage 5 through ``stance.press_stage5`` (faked here: a tiny DEBATE/LSD, no model, no R),
keeps a scored release that has no usable body by scoring its title, and records the scorer in its receipt.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import sys

import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from collect import build_press_engine_feed as feed
from stance import press_stage5 as S

BODY = " ".join(["credit unions serve members in every district"] * 8)      # 56 words, > 300 characters
SCORED_FIELDS = ["member", "bioguide", "url", "title", "date", "polarity", "score", "score_graded", "score_label",
                 "relevance", "categories", "word_count", "recency_weight", "body_fallback"]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_csv(path, fields, rows):
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def make_run(tmp_path, name, articles, completed_at="2099-01-01T00:00:00+00:00"):
    """A sealed press run.  ``articles``: (slug, title, body, date, tagged) -> one scored row (+ a host article if the body is non-empty)."""
    run = tmp_path / name
    run.mkdir()
    scored, hosts = [], []
    for slug, title, body, date, tagged in articles:
        url = "https://member.house.gov/news/%s" % slug
        base = {"member": "Member " + slug, "bioguide": "B" + slug.upper(), "url": url, "title": title}
        scored.append({**base, "date": date, "polarity": "1", "score": "1", "score_graded": "0.6",
                       "score_label": "Positive", "relevance": "credit-union mention",
                       "categories": "General Credit Union", "word_count": str(len(body.split())),
                       "recency_weight": "0.9", "body_fallback": "title" if tagged else ""})
        if body:
            hosts.append({**base, "body": body})
    write_csv(run / "press_delta_scored.csv", SCORED_FIELDS, scored)
    write_csv(run / "press_delta_host_articles.csv", ["member", "bioguide", "url", "title", "body"], hosts)
    write_csv(run / "press_delta_article_mentions.csv", ["member", "bioguide", "url", "title", "body", "date_final"], [])
    (run / "press_network_retry_receipt.json").write_text(
        json.dumps({"completed_at": completed_at, "pending_body_urls": 0}), encoding="utf-8")
    names = ("press_delta_scored.csv", "press_delta_host_articles.csv", "press_delta_article_mentions.csv")
    intake = {"schema": "collection-intake-receipt-v1", "status": "ACCEPTED", "generated_at": completed_at,
              "inputs": {"press_delta_root": str(run.resolve()),
                         "press_retry_receipt_sha256": sha(run / "press_network_retry_receipt.json"),
                         "press_source_sha256": {name: sha(run / name) for name in names}},
              "press_delta": {"live_network_retry_receipt_valid": True}}
    intake_path = tmp_path / (name + ".intake.json")
    intake_path.write_text(json.dumps(intake), encoding="utf-8")
    return run, intake_path


def outputs(tmp_path, tag="out"):
    out = tmp_path / tag
    out.mkdir(exist_ok=True)
    return [out / name for name in ("tone.csv", "fulltext.jsonl", "mentions.jsonl", "receipt.json")]


class Fakes:
    def __init__(self):
        self.debate_batches = []

    def debate(self, items, model_dir):
        self.debate_batches.append([text for _id, text in items])
        return {item_id: dict(zip(S.DEBATE_FIELDS, ("oppose", -1, 0.05, 0.9, 0.05, 0.9) if "oppose" in text
                                  else ("support", 1, 0.9, 0.05, 0.05, 0.9))) for item_id, text in items}

    def lsd(self, items):
        return {item_id: {"n_words": len(text.split()), "lsd_pos": 0, "lsd_neg": 2, "lsd_net": -2,
                          "lsd_net_norm": round(-2 / max(1, len(text.split())), 5), "lsd_charge": 0.05}
                for item_id, text in items}


def scorer(tmp_path, fakes, **options):
    model = tmp_path / "draft" / "debate_finetuned"
    model.mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "tokenizer.json"):
        (model / name).write_text("{}", encoding="utf-8")
    (model / "model.safetensors").write_bytes(b"weights")
    return S.make_scorer(cache_path=str(tmp_path / "stage5_cache.jsonl"), env={"CU_S5_DEBATE_MODEL": str(model)},
                         expected_model_dir=str(model), overlay_path=str(tmp_path / "none.csv"),
                         draft_dir=str(tmp_path / "draft"), debate_fn=fakes.debate, lsd_fn=fakes.lsd, **options)


def read_tone(path):
    with io.open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_library_default_leaves_the_rules_columns_alone_and_says_so(tmp_path):
    run, intake = make_run(tmp_path, "press_refresh_20990101", [("a", "Title A", BODY, "2026-07-01", False)])
    tone, fulltext, mentions, receipt_path = paths = outputs(tmp_path)
    receipt = feed.build(run, *paths, intake)
    row = read_tone(tone)[0]
    assert row["stance_src"] == "press_refresh_rules_v1" and row["source"] == "PRESS_REFRESH_HOST_ARTICLE"
    assert receipt["stage5"] == {"enabled": False, "scorer": "press_refresh_rules_v1"}
    assert receipt["counts"]["tone_rows"] == 1 and receipt["title_fallback_rows"] == 0


def test_stage5_replaces_the_rules_columns_and_the_receipt_binds_the_published_bytes(tmp_path):
    fakes = Fakes()
    run, intake = make_run(tmp_path, "press_refresh_20990101", [("a", "Title A", BODY + " we oppose", "2026-07-01", False)])
    tone, fulltext, mentions, receipt_path = paths = outputs(tmp_path)
    receipt = feed.build(run, *paths, intake, stage5=scorer(tmp_path, fakes))
    row = read_tone(tone)[0]
    assert row["stance_src"] == S.STANCE_SRC and row["stance"] == "oppose" and row["stance_sign"] == "-1"
    assert float(row["press_value"]) < 0 and abs(float(row["press_value"])) < 0.2
    assert row["score_graded"] == "-0.6" and row["score_label"] == "Negative"
    assert row["p_oppose"] == "0.9" and row["lsd_net"] == "-2"
    block = receipt["stage5"]
    assert block["enabled"] is True and block["scorer"] == S.STANCE_SRC
    assert block["rows_scored"] == 1 and block["rows_cached"] == 0 and block["rows_pending"] == 0
    assert len(block["model_dir_sha256"]) == 64 and block["model_dir"].endswith("debate_finetuned")
    published = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert published["outputs_sha256"][feed._rel(tone)] == sha(tone)         # Stage 5 ran BEFORE the bytes were hashed
    # a rebuild serves the row from the cache: scored once
    again = feed.build(run, *outputs(tmp_path, "again"), intake, stage5=scorer(tmp_path, fakes))
    assert again["stage5"]["rows_scored"] == 0 and again["stage5"]["rows_cached"] == 1
    assert len(fakes.debate_batches) == 1


def test_a_scored_release_with_no_usable_body_is_scored_on_its_title_not_refused(tmp_path):
    fakes = Fakes()
    run, intake = make_run(tmp_path, "press_refresh_20990101", [
        ("full", "Full body title", BODY, "2026-07-01", False),
        ("shell", "Credit union shell title oppose", "too short to be a body", "2026-07-02", False),
        ("none", "Credit union title only", "", "", True)])                    # undated, tagged by R, no host row at all
    tone, fulltext, mentions, receipt_path = paths = outputs(tmp_path)
    receipt = feed.build(run, *paths, intake, stage5=scorer(tmp_path, fakes))
    rows = {row["title"]: row for row in read_tone(tone)}
    assert set(rows) == {"Full body title", "Credit union shell title oppose", "Credit union title only"}
    assert rows["Full body title"]["body_fallback"] == ""
    assert rows["Credit union shell title oppose"]["body_fallback"] == "title"
    assert rows["Credit union title only"]["body_fallback"] == "title" and rows["Credit union title only"]["date"] == ""
    assert receipt["title_fallback_rows"] == 2 and receipt["stage5"]["title_fallback_rows"] == 2
    # the title is the scored text; neither fallback row has a fulltext entry (the history has none either)
    assert sorted(fakes.debate_batches[0]) == sorted([BODY, "Credit union shell title oppose", "Credit union title only"])
    assert [json.loads(line)["url"].rsplit("/", 1)[1] for line in fulltext.read_text(encoding="utf-8").splitlines()] == ["full"]
    assert rows["Credit union shell title oppose"]["stance"] == "oppose"
    assert rows["Credit union shell title oppose"]["press_value"] == "-0.03"   # no 300-character body: the floor intensity


def test_without_stage5_a_bodyless_release_still_publishes_as_a_title_fallback_row(tmp_path):
    run, intake = make_run(tmp_path, "press_refresh_20990101", [("none", "Title only", "", "2026-07-01", False)])
    tone, _fulltext, _mentions, receipt_path = paths = outputs(tmp_path)
    receipt = feed.build(run, *paths, intake)
    assert read_tone(tone)[0]["body_fallback"] == "title" and receipt["title_fallback_rows"] == 1


def test_cumulative_promotion_runs_stage5_once_over_the_merged_ledger(tmp_path):
    fakes = Fakes()
    run1, intake1 = make_run(tmp_path, "press_refresh_20990101", [("a", "A title", BODY, "2026-07-01", False)])
    run2, intake2 = make_run(tmp_path, "press_refresh_20990102", [
        ("a", "A title again", BODY + " oppose", "2026-07-01", False), ("b", "B title", BODY, "2026-07-02", False)],
        completed_at="2099-01-02T00:00:00+00:00")
    archive = tmp_path / "accepted"
    archive.mkdir()
    (archive / "press_refresh_20990101.json").write_text(intake1.read_text(encoding="utf-8"), encoding="utf-8")
    tone, fulltext, mentions, receipt_path = outputs(tmp_path)
    receipt = feed.build_cumulative(tone, fulltext, mentions, receipt_path, intake2, archive,
                                    stage5=scorer(tmp_path, fakes))
    rows = read_tone(tone)
    assert len(rows) == 2 and {row["stance_src"] for row in rows} == {S.STANCE_SRC}
    assert len(fakes.debate_batches) == 1 and len(fakes.debate_batches[0]) == 2     # not once per accepted run
    assert receipt["counts"] == {"accepted_runs": 2, "tone_rows": 2, "fulltext_rows": 2, "official_mentions": 0}
    assert receipt["stage5"]["rows_scored"] == 2 and receipt["stage5"]["enabled"] is True
    assert receipt["outputs_sha256"][feed._rel(tone)] == sha(tone)


def test_the_batch_bound_withholds_rows_and_the_receipt_says_how_many(tmp_path):
    fakes = Fakes()
    run, intake = make_run(tmp_path, "press_refresh_20990101", [
        (slug, "Title " + slug, BODY + " " + slug, "2026-07-01", False) for slug in ("a", "b", "c")])
    tone, *_rest = paths = outputs(tmp_path)
    receipt = feed.build(run, *paths, intake, stage5=scorer(tmp_path, fakes, batch_limit=2))
    assert len(read_tone(tone)) == 2 and receipt["counts"]["tone_rows"] == 2
    assert receipt["stage5"]["rows_pending"] == 1 and receipt["stage5"]["batch_limit"] == 2
    assert all(row["stance_src"] == S.STANCE_SRC for row in read_tone(tone))


def test_cli_runs_stage5_by_default_and_refuses_rather_than_use_the_base_model(tmp_path, monkeypatch):
    assert feed.parse_args([]).no_stage5 is False and feed.parse_args(["--no-stage5"]).no_stage5 is True
    assert feed.parse_args([]).stage5_batch == 400
    run, intake = make_run(tmp_path, "press_refresh_20990101", [("a", "Title A", BODY, "2026-07-01", False)])
    # a valid live-network receipt is required by --source-root; reuse the freshness suite's sealing recipe
    from datetime import datetime, timezone
    for name in feed.REQUIRED_RUN_FILES[1:]:
        if not (run / name).exists():
            (run / name).write_bytes(b"retained fixture content")
    (run / "press_delta_pending.csv").write_text("bioguide,url,date,collection_status\n")
    receipt = {"mode": "live_network_retry", "completed_at": datetime.now(timezone.utc).isoformat(), "status": "PASS",
               "pending_body_urls": 0, "pending_body_urls_sha256": sha(run / "press_delta_pending.csv"),
               "fulltext_csv_sha256": sha(run / "press_delta_fulltext.csv"),
               "fulltext_rds_sha256": sha(run / "press_delta_fulltext.rds")}
    (run / "press_network_retry_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    document = json.loads(intake.read_text(encoding="utf-8"))
    document["inputs"]["press_retry_receipt_sha256"] = sha(run / "press_network_retry_receipt.json")
    intake.write_text(json.dumps(document), encoding="utf-8")
    tone, fulltext, mentions, receipt_path = outputs(tmp_path)
    for path in (tone, fulltext, mentions, receipt_path):
        path.write_text("previous generation", encoding="utf-8")
    argv = ["--source-root", str(run), "--intake-receipt", str(intake), "--tone-output", str(tone),
            "--fulltext-output", str(fulltext), "--mentions-output", str(mentions), "--receipt-output", str(receipt_path)]
    monkeypatch.delenv("CU_S5_DEBATE_MODEL", raising=False)
    # An unset variable now means <DRAFT>/debate_finetuned; point DRAFT at an empty directory so this stays a refusal
    # (and the real model on this machine is never loaded).
    monkeypatch.setattr(S, "DRAFT", str(tmp_path / "draft_without_the_model"))
    with pytest.raises(S.Stage5Refused, match="CU_S5_DEBATE_MODEL is not set"):
        feed.main(argv)
    assert [path.read_text(encoding="utf-8") for path in (tone, fulltext, mentions, receipt_path)] == ["previous generation"] * 4
    assert feed.main(argv + ["--no-stage5"]) == 0
    assert read_tone(tone)[0]["stance_src"] == "press_refresh_rules_v1"
