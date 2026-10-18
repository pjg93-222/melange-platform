# -*- coding: utf-8 -*-
"""press_stance_stack.py and refresh rows (owner ruling D-219, 2026-10-15) and the debate-slot leak (D-221, 2026-10-15).

The stack is a script, so it runs in a subprocess on a tiny synthetic corpus under a private
MONEYBALL_ROOT / MONEYBALL_DRAFT.  Pins: a history row scores exactly as it did before the change;
a refresh row bypasses the meta-model (its Stage 5 label, stack_path='bypass') unless the Haiku kappa
gate passed AND a reading exists; Paul's hand overrides still win last.

D-221: the meta-model's DEBATE slot (training and scoring) is the RAW pre-overlay DEBATE reading, never Paul's overlay
label; the tier is computed from that raw slot; Paul's golds are applied after the stack and his hand overrides after
the golds, and label_source records which of the three decided each row.
"""
from __future__ import annotations

import csv
import hashlib
from collections import Counter
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from stance import press_haiku_rater as HR

STACK = FEC / "stance" / "press_stance_stack.py"
CLASSES = ("support", "oppose", "neutral", "unrelated")
SENTENCE = {"support": "welcome helpful boost for member lenders",
            "oppose": "harmful burden threatens member lenders",
            "neutral": "routine administrative update about office hours",
            "unrelated": "local festival parade weekend schedule"}
REFRESH = {"r-support": "support", "r-oppose": "oppose", "r-neutral": "neutral", "r-override": "support"}
SOURCE = "PRESS_REFRESH_HOST_ARTICLE"


def md5(url):
    return hashlib.md5(url.strip().encode()).hexdigest()


def fable(alignment):
    return {"target": "alpha topic %s" % alignment, "stance_toward_target": alignment,
            "target_cu_valence": 1, "cu_alignment": alignment, "attribution": "member-own",
            "confidence": "high", "rationale": "fixture"}


def write_csv(path, fields, rows, encoding="utf-8-sig"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with io.open(path, "w", encoding=encoding, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def tone(url, title, stance, bioguide, **extra):
    return {"member": "Member " + bioguide, "bioguide": bioguide, "date": "2026-07-01", "title": title,
            "url": url, "stance": stance, "stance_sign": {"support": 1, "oppose": -1}.get(stance, 0), **extra}


TONE_FIELDS = ["member", "bioguide", "chamber", "state", "party", "date", "title", "url", "categories",
               "relevance", "score", "score_graded", "score_label", "stance", "stance_sign", "p_support",
               "p_oppose", "p_neutral", "stance_conf", "n_words", "lsd_pos", "lsd_neg", "lsd_net",
               "lsd_net_norm", "lsd_charge", "stance_src", "press_value", "recency_weight", "source_run",
               "source", "body_fallback"]


def build_history(root, draft):
    """20 gold rows (5 per class, all with a Fable reading) + 4 more Fable rows + 2 history rows with none."""
    fables, golds, history = [], [], []
    for index in range(7):
        for label in CLASSES:
            url = "https://hist.example.test/%s-%d" % (label, index)
            history.append(tone(url, "%s headline %d" % (label.title(), index),
                                "neutral" if label == "unrelated" else label, "H%05d" % index))
            fables.append({"k": md5(url), "out": fable(label)})
            if index < 5:
                golds.append({"url": url, "title": history[-1]["title"], "label": label,
                              "text": "%s item %d" % (SENTENCE[label], index)})
    for name, stance in (("nofable-a", "support"), ("nofable-b", "oppose")):
        history.append(tone("https://hist.example.test/" + name, "Unread %s release" % name, stance, "H9999" + name[-1]))
    stance_dir = root / "data" / "stance"
    stance_dir.mkdir(parents=True, exist_ok=True)
    with io.open(stance_dir / "api_press_cache_fable.jsonl", "w", encoding="utf-8", newline="\n") as handle:
        handle.writelines(json.dumps(row) + "\n" for row in fables)
    write_csv(stance_dir / "cu_golden_labels.csv", ["url", "title", "label", "text"], golds)
    write_csv(draft / "cu_press_stance_tone.csv", TONE_FIELDS, history)


def add_refresh(root):
    """The refresh feed, receipt-bound exactly as press_stream.validate_refresh_receipt requires."""
    stance_dir = root / "data" / "stance"
    rows, texts = [], []
    for name, label in REFRESH.items():
        url = "https://member.example.house.gov/%s" % name
        rows.append(tone(url, "Refresh %s release" % name, label, "R00001", source=SOURCE,
                         stance_src="stage5_debate_ft_lsd_v1", press_value="0.02"))
        texts.append({"url": url, "text": "%s refresh %s" % (SENTENCE[label], name), "bioguide": "R00001"})
    write_csv(stance_dir / "press_refresh_tone.csv", TONE_FIELDS, rows)
    with io.open(stance_dir / "press_refresh_fulltext.jsonl", "w", encoding="utf-8", newline="\n") as handle:
        handle.writelines(json.dumps(row) + "\n" for row in texts)
    mentions = root / "data" / "press" / "official_mentions.jsonl"
    mentions.parent.mkdir(parents=True, exist_ok=True)
    mentions.write_text("", encoding="utf-8")
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    (stance_dir / "press_refresh_feed_receipt.json").write_text(json.dumps({
        "schema": "press-refresh-engine-feed-v1", "status": "ACTIVE_TYPED_SUPPLEMENT",
        "promotion_mode": "CUMULATIVE_ACCEPTED_RUN_LEDGER", "accepted_runs": [{"fixture": True}],
        "outputs_sha256": {"data/stance/press_refresh_tone.csv": sha(stance_dir / "press_refresh_tone.csv"),
                           "data/stance/press_refresh_fulltext.jsonl": sha(stance_dir / "press_refresh_fulltext.jsonl"),
                           "data/press/official_mentions.jsonl": sha(mentions)}}), encoding="utf-8")
    (stance_dir / "press_stance_overrides.json").write_text(json.dumps({"corrections": [
        {"url": "https://member.example.house.gov/r-override", "stance": "oppose", "note": "fixture"}]}),
        encoding="utf-8")
    return {name: "https://member.example.house.gov/%s" % name for name in REFRESH}


def run_stack(tmp_path, script=STACK, dump=None, expect_failure=False):
    root, draft = tmp_path / "root", tmp_path / "draft"
    env = {**os.environ, "MONEYBALL_ROOT": str(root), "MONEYBALL_DRAFT": str(draft), "PYTHONIOENCODING": "utf-8"}
    # runpy so the same driver can run the pre-change copy of the script (its __file__ is elsewhere).
    code = "import runpy,sys; sys.path.insert(0, %r); g = runpy.run_path(%r, run_name='__main__'); " % (str(FEC), str(script))
    if dump is not None:    # the meta-model's DEBATE one-hot columns, for training (golds) and scoring (corpus) rows
        code += ("import json; json.dump({'train': [r['url_raw'] for r in g['grecs']], 'train_slot': g['FB'][:, g['DEBATE_COLS']].tolist(), "
                 "'score': [r['url_raw'] for r in g['crecs']], 'score_slot': g['FBc'][:, g['DEBATE_COLS']].tolist()}, "
                 "open(%r, 'w'))" % str(dump))
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=600)
    if expect_failure:
        assert done.returncode != 0
        return done.stdout + done.stderr
    assert done.returncode == 0, done.stdout[-1500:] + done.stderr[-1500:]
    with io.open(root / "press_stance_final.csv", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return rows, done.stdout


def label_tier(rows):
    return {row["url"]: (row["stack_label"], row["tier"]) for row in rows if row["source"] == "HISTORICAL_PRESS"}


@pytest.fixture()
def history_only(tmp_path):
    build_history(tmp_path / "root", tmp_path / "draft")
    return tmp_path


# Produced by the pre-change press_stance_stack.py on build_history() (git HEAD copy, run once).
EXPECTED_HISTORY = {
    'https://hist.example.test/neutral-0': ('neutral', 'unanimous'),
    'https://hist.example.test/neutral-1': ('neutral', 'unanimous'),
    'https://hist.example.test/neutral-2': ('neutral', 'unanimous'),
    'https://hist.example.test/neutral-3': ('neutral', 'unanimous'),
    'https://hist.example.test/neutral-4': ('neutral', 'unanimous'),
    'https://hist.example.test/neutral-5': ('neutral', 'unanimous'),
    'https://hist.example.test/neutral-6': ('neutral', 'unanimous'),
    'https://hist.example.test/nofable-a': ('support', 'majority'),
    'https://hist.example.test/nofable-b': ('oppose', 'majority'),
    'https://hist.example.test/oppose-0': ('oppose', 'unanimous'),
    'https://hist.example.test/oppose-1': ('oppose', 'unanimous'),
    'https://hist.example.test/oppose-2': ('oppose', 'unanimous'),
    'https://hist.example.test/oppose-3': ('oppose', 'unanimous'),
    'https://hist.example.test/oppose-4': ('oppose', 'unanimous'),
    'https://hist.example.test/oppose-5': ('oppose', 'unanimous'),
    'https://hist.example.test/oppose-6': ('oppose', 'unanimous'),
    'https://hist.example.test/support-0': ('support', 'unanimous'),
    'https://hist.example.test/support-1': ('support', 'unanimous'),
    'https://hist.example.test/support-2': ('support', 'unanimous'),
    'https://hist.example.test/support-3': ('support', 'unanimous'),
    'https://hist.example.test/support-4': ('support', 'unanimous'),
    'https://hist.example.test/support-5': ('support', 'unanimous'),
    'https://hist.example.test/support-6': ('support', 'unanimous'),
    'https://hist.example.test/unrelated-0': ('unrelated', 'majority'),
    'https://hist.example.test/unrelated-1': ('unrelated', 'majority'),
    'https://hist.example.test/unrelated-2': ('unrelated', 'majority'),
    'https://hist.example.test/unrelated-3': ('unrelated', 'majority'),
    'https://hist.example.test/unrelated-4': ('unrelated', 'majority'),
    'https://hist.example.test/unrelated-5': ('unrelated', 'majority'),
    'https://hist.example.test/unrelated-6': ('unrelated', 'majority'),
}


def test_history_rows_score_exactly_as_before_the_change(history_only):
    rows, _stdout = run_stack(history_only)
    assert len(rows) == 7 * 4 + 2
    assert {row["stack_path"] for row in rows} == {"stack"}
    assert label_tier(rows) == EXPECTED_HISTORY


def test_refresh_rows_do_not_move_history_rows_and_bypass_without_a_gate(history_only):
    urls = add_refresh(history_only / "root")
    rows, stdout = run_stack(history_only)
    assert label_tier(rows) == EXPECTED_HISTORY, "adding refresh rows must not change a history row"
    refresh = {row["url"]: row for row in rows if row["source"] == SOURCE}
    assert set(refresh) == set(urls.values())
    for name, label in REFRESH.items():
        row = refresh[urls[name]]
        assert row["stack_path"] == "bypass" and row["tier"] == "review"
        assert row["stack_label"] == (label if name != "r-override" else "oppose")
        assert row["label_source"] == ("press_override" if name == "r-override" else "stack")
    assert refresh[urls["r-override"]]["debate_stance"] == "support"   # Paul's override beat the Stage 5 label, last
    assert "refresh reader slot: bypass" in stdout


def passing_gate(tmp_path, kappa_floor_met=True):
    """Run the real gate against a fake classifier on an 87-row gold file, in-process, under tmp_path."""
    draft, root = tmp_path / "draft", tmp_path / "root"
    rows = []
    for index in range(HR.GATE_SAMPLE_N):
        label = HR.LABELS[index % 3]
        rows.append({"split": "test", "human_label": label, "url": "https://gold.example.test/%d" % index,
                     "title": "gold %d" % index, "text_snippet": label + " text", "member": "M", "date": "2026-01-01"})
    write_csv(draft / "cu_stance_label_set.csv",
              ["split", "human_label", "url", "title", "text_snippet", "member", "date"], rows)
    answers = {row["text_snippet"].split()[0]: row["text_snippet"].split()[0] for row in rows}

    def classify(system, user, schema, model="sonnet"):
        text = user.split("Full text: ")[1].split("\n")[0]
        label = answers[text.split()[0]] if kappa_floor_met else "neutral"
        return fable(label), {}

    gate = HR.run_gate(classify=classify, gold_path=str(draft / "cu_stance_label_set.csv"),
                       gate_path=str(root / "data" / "stance" / "press_haiku_gate.json"),
                       calls_path=str(root / "data" / "stance" / "press_haiku_gate_calls.jsonl"))
    return gate


def haiku_reading(root, url, label):
    HR._append_jsonl(str(root / "data" / "stance" / "press_haiku_cache.jsonl"),
                     {"v": HR.RATER_VERSION, "k": md5(url), "out": fable(label)})


def test_refresh_rows_use_the_haiku_slot_only_after_the_gate_passes(history_only):
    urls = add_refresh(history_only / "root")
    gate = passing_gate(history_only)
    assert gate["passed"] is True and gate["kappa"] >= HR.KAPPA_MIN
    haiku_reading(history_only / "root", urls["r-support"], "support")
    haiku_reading(history_only / "root", urls["r-oppose"], "oppose")
    rows, stdout = run_stack(history_only)
    assert label_tier(rows) == EXPECTED_HISTORY
    refresh = {row["url"]: row for row in rows if row["source"] == SOURCE}
    assert refresh[urls["r-support"]]["stack_path"] == "stack_haiku"
    assert refresh[urls["r-support"]]["stack_label"] == "support"
    assert refresh[urls["r-oppose"]]["stack_path"] == "stack_haiku"
    assert refresh[urls["r-oppose"]]["stack_label"] == "oppose"
    # no reading yet for these two: bypass, never a guess
    assert refresh[urls["r-neutral"]]["stack_path"] == "bypass"
    assert refresh[urls["r-override"]]["stack_path"] == "bypass"
    assert refresh[urls["r-override"]]["stack_label"] == "oppose"
    assert "refresh reader slot: ADMITTED" in stdout


def test_a_failed_gate_keeps_every_refresh_row_on_the_bypass_even_with_readings(history_only):
    urls = add_refresh(history_only / "root")
    gate = passing_gate(history_only, kappa_floor_met=False)
    assert gate["passed"] is False
    for name, label in REFRESH.items():
        haiku_reading(history_only / "root", urls[name], label)
    rows, stdout = run_stack(history_only)
    assert {row["stack_path"] for row in rows if row["source"] == SOURCE} == {"bypass"}
    assert "refresh reader slot: bypass" in stdout


# ---- D-221: the debate-slot leak --------------------------------------------------------------------------------
HIST = "https://hist.example.test/"


def slot_of(vector):
    return CLASSES[vector.index(1.0)] if 1.0 in vector else ""


def mark_overlay(tmp_path, overlay, raw, golds=None, overrides=None):
    """Turn history rows into Paul-overlaid ones.  ``overlay``: {name: the label Paul's overlay wrote into the tone stance};
    ``raw``: {name: the raw DEBATE reading} for Draft Systems' cu_press_stance_raw.csv (None = no such file);
    ``golds``: {name: replacement gold label}; ``overrides``: {name: Paul's hand press label}."""
    root, draft = tmp_path / "root", tmp_path / "draft"
    tone_path = draft / "cu_press_stance_tone.csv"
    with io.open(tone_path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        name = row["url"][len(HIST):]
        if name in overlay:
            row.update(stance=overlay[name], stance_src="human_label")
    write_csv(tone_path, TONE_FIELDS, rows)
    if raw is not None:
        write_csv(draft / "cu_press_stance_raw.csv", ["id", "stance"],
                  [{"id": HIST + name, "stance": value} for name, value in raw.items()])
    stance_dir = root / "data" / "stance"
    if golds:
        path = stance_dir / "cu_golden_labels.csv"
        with io.open(path, encoding="utf-8-sig", newline="") as handle:
            table = list(csv.DictReader(handle))
        for row in table:
            row["label"] = golds.get(row["url"][len(HIST):], row["label"])
        write_csv(path, ["url", "title", "label", "text"], table)
    if overrides:
        (stance_dir / "press_stance_overrides.json").write_text(json.dumps({"corrections": [
            {"url": HIST + name, "stance": label, "note": "fixture"} for name, label in overrides.items()]}), encoding="utf-8")


@pytest.fixture()
def overlaid(history_only):
    """support-0 / oppose-0 / neutral-0 carry Paul's overlay label (== gold) over a DIFFERENT raw DEBATE reading;
    support-1 is marked overlaid but the raw file has no reading for it; oppose-1's raw row must be ignored (not overlaid)."""
    mark_overlay(history_only, {"support-0": "support", "oppose-0": "oppose", "neutral-0": "neutral", "support-1": "support"},
                 {"support-0": "neutral", "oppose-0": "support", "neutral-0": "oppose", "oppose-1": "neutral"})
    return history_only


def test_the_meta_model_trains_and_scores_on_the_raw_debate_not_the_overlay_label(overlaid, tmp_path):
    urls = add_refresh(overlaid / "root")
    dump = tmp_path / "slots.json"
    run_stack(overlaid, dump=dump)
    slots = json.loads(dump.read_text(encoding="utf-8"))
    train = {url: slot_of(vec) for url, vec in zip(slots["train"], slots["train_slot"])}
    score = {url: slot_of(vec) for url, vec in zip(slots["score"], slots["score_slot"])}
    assert set(train) == {HIST + "%s-%d" % (label, index) for label in CLASSES for index in range(5)}
    for table in (train, score):
        # an overlaid row: the RAW reading, so the target label is not sitting in a feature
        assert (table[HIST + "support-0"], table[HIST + "oppose-0"], table[HIST + "neutral-0"]) == ("neutral", "support", "oppose")
        assert table[HIST + "support-1"] == ""                  # marked overlaid, no raw reading: blank, never the label
        # an un-overlaid row keeps its own stance; the raw file is not consulted for it
        assert table[HIST + "oppose-1"] == "oppose" and table[HIST + "support-2"] == "support"
    # a refresh row's slot is its Stage 5 label (D-219); the overlay machinery never touches it
    assert {name: score[url] for name, url in urls.items()} == REFRESH


def test_the_tier_and_debate_column_use_the_raw_slot_and_the_label_is_the_gold(overlaid):
    rows, stdout = run_stack(overlaid)
    by_url = {row["url"]: row for row in rows}
    hit = by_url[HIST + "support-0"]
    assert hit["debate_stance"] == "neutral"                    # the raw DEBATE, not Paul's overlay label
    assert hit["stack_label"] == "support" and hit["label_source"] == "gold_760"
    assert hit["tier"] == "majority"                            # stack == Fable, raw DEBATE disagrees (the overlay slot made it 'unanimous')
    assert EXPECTED_HISTORY[HIST + "support-0"] == ("support", "unanimous")
    assert by_url[HIST + "support-1"]["debate_stance"] == "" and by_url[HIST + "support-1"]["tier"] == "majority"
    assert "overlay rows with no raw DEBATE reading: 1" in stdout
    assert by_url[HIST + "support-3"]["tier"] == "unanimous" and by_url[HIST + "support-3"]["debate_stance"] == "support"


def test_a_missing_raw_debate_file_refuses_rather_than_training_on_the_overlay_label(history_only):
    mark_overlay(history_only, {"support-0": "support"}, None)
    assert "raw pre-overlay DEBATE file missing" in run_stack(history_only, expect_failure=True)


def test_golds_are_applied_after_the_stack_and_do_not_move_the_tier(history_only):
    # support-2's gold is flipped to 'oppose': Fable, the readers and DEBATE all still say 'support', so the stack says
    # 'support' (tier unanimous, computed before the gold); Paul's gold decides the label afterwards.
    mark_overlay(history_only, {}, None, golds={"support-2": "oppose", "neutral-2": "unrelated"})
    rows, stdout = run_stack(history_only)
    by_url = {row["url"]: row for row in rows}
    flipped = by_url[HIST + "support-2"]
    assert (flipped["stack_label"], flipped["label_source"], flipped["tier"], flipped["stack_path"]) == ("oppose", "gold_760", "unanimous", "stack")
    assert by_url[HIST + "neutral-2"]["stack_label"] == "unrelated" and by_url[HIST + "neutral-2"]["label_source"] == "gold_760"
    # a gold row whose label the stack agreed with is still labelled by the gold; a non-gold row by the stack
    assert by_url[HIST + "oppose-0"]["label_source"] == "gold_760"
    assert by_url[HIST + "oppose-6"]["label_source"] == "stack" and by_url[HIST + "nofable-a"]["label_source"] == "stack"
    assert "'gold_760': 20" in stdout and "gold labels loaded: 20" in stdout     # 5 golds per class, every one in the corpus


def test_a_hand_override_still_beats_the_gold_and_the_stack_last(history_only):
    mark_overlay(history_only, {}, None, golds={"support-3": "oppose"},
                 overrides={"support-3": "neutral", "oppose-6": "support"})
    rows, _stdout = run_stack(history_only)
    by_url = {row["url"]: row for row in rows}
    gold_and_override = by_url[HIST + "support-3"]
    assert (gold_and_override["stack_label"], gold_and_override["label_source"]) == ("neutral", "press_override")
    assert gold_and_override["tier"] == "unanimous"            # the tier is the model's agreement, untouched by either hand label
    not_gold = by_url[HIST + "oppose-6"]
    assert (not_gold["stack_label"], not_gold["label_source"]) == ("support", "press_override")
    assert Counter(row["label_source"] for row in rows) == {"gold_760": 19, "press_override": 2, "stack": 9}


def test_overlay_rows_in_the_gold_set_get_the_gold_label_not_the_tone_overlay_label(history_only):
    # The tone file's overlay label is a copy that can lag the gold file; the gold file is the authority applied after the stack.
    mark_overlay(history_only, {"oppose-0": "support"}, {"oppose-0": "neutral"}, golds={"oppose-0": "oppose"})
    rows, _stdout = run_stack(history_only)
    row = {r["url"]: r for r in rows}[HIST + "oppose-0"]
    assert (row["stack_label"], row["label_source"], row["debate_stance"]) == ("oppose", "gold_760", "neutral")
