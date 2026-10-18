# -*- coding: utf-8 -*-
"""Stage 5 wrapper for the press refresh feed (owner ruling D-219, 2026-10-15).

Everything heavy is faked: a tiny DEBATE function (or a fake sidecar script for the subprocess seam) and,
for the LSD seam, a two-word-list dictionary run through Draft Systems' own stage5_lsd.R.  Nothing here
loads a model or reads real press data.
"""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from collect.build_press_engine_feed import TONE_FIELDS
from stance import press_stage5 as S

HISTORICAL_COLUMNS = [name for name in TONE_FIELDS if name not in ("source_run", "source", "body_fallback")]
LONG = " ".join(["credit unions serve members in every district"] * 8)      # >= 300 characters, >= 20 words
SHORT = " ".join(["short release about credit unions"] * 5)                 # >= 20 words, < 300 characters


def make_model(tmp_path, name="debate_finetuned", weights=b"weights-v1"):
    model = tmp_path / "draft" / name
    model.mkdir(parents=True, exist_ok=True)
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "tokenizer.json").write_text("{}", encoding="utf-8")
    (model / "model.safetensors").write_bytes(weights)
    (model / ".Rhistory").write_text("", encoding="utf-8")
    return model


class Fakes:
    """A tiny DEBATE and LSD; every call is recorded."""

    def __init__(self):
        self.debate_calls, self.lsd_calls = [], []

    def debate(self, items, model_dir):
        self.debate_calls.append((list(items), Path(model_dir)))
        out = {}
        for item_id, text in items:
            if "oppose" in text:
                out[item_id] = ("oppose", -1, 0.05, 0.9, 0.05, 0.9)
            elif "support" in text:
                out[item_id] = ("support", 1, 0.9, 0.05, 0.05, 0.9)
            else:
                out[item_id] = ("neutral", 0, 0.1, 0.1, 0.8, 0.8)
        return {key: dict(zip(S.DEBATE_FIELDS, value)) for key, value in out.items()}

    def lsd(self, items):
        self.lsd_calls.append(list(items))
        out = {}
        for item_id, text in items:
            words = len(text.split())
            good, bad = text.count("great"), text.count("awful")
            net = good - bad
            out[item_id] = {"n_words": words, "lsd_pos": good, "lsd_neg": bad, "lsd_net": net,
                            "lsd_net_norm": round(net / words, 5) if words else 0.0,
                            "lsd_charge": round((good + bad) / words, 5) if words else 0.0}
        return out


def row(name, title="Release title", **extra):
    return {**dict.fromkeys(TONE_FIELDS, ""), "member": "Member " + name, "bioguide": "B%05d" % (sum(map(ord, name)) % 100000), "chamber": "House",
            "url": "https://member.house.gov/news/%s" % name, "title": title, "date": "2026-07-01",
            "score_graded": "0.6", "score_label": "Positive", "stance": "support", "stance_sign": "1",
            "stance_src": "press_refresh_rules_v1", "press_value": "0.6", "source": "PRESS_REFRESH_HOST_ARTICLE",
            "categories": "General Credit Union", "relevance": "credit-union mention", "score": "1",
            "recency_weight": "0.9", "source_run": "press_refresh_20990101", **extra}


def body_of(tone, text):
    return {"url": tone["url"], "bioguide": tone["bioguide"], "text": text}


def run(tmp_path, tone_rows, texts, fakes, model=None, **options):
    model = model or make_model(tmp_path)
    kwargs = dict(cache_path=str(tmp_path / "cache.jsonl"), env={"CU_S5_DEBATE_MODEL": str(model)},
                  expected_model_dir=str(model), overlay_path=str(tmp_path / "no-overlay.csv"),
                  draft_dir=str(tmp_path / "draft"), debate_fn=fakes.debate, lsd_fn=fakes.lsd)
    kwargs.update(options)
    return S.apply(tone_rows, [body_of(r, t) for r, t in zip(tone_rows, texts) if t], **kwargs)


def test_rows_carry_the_historical_columns_in_historical_units(tmp_path):
    fakes = Fakes()
    oppose = row("a", stance="support")
    neutral = row("b")
    rows, report = run(tmp_path, [oppose, neutral],
                       [LONG + " we oppose this and it is awful awful awful", LONG + " routine update great"], fakes)
    by_url = {r["url"]: r for r in rows}
    first, second = by_url[oppose["url"]], by_url[neutral["url"]]
    assert all(name in first for name in HISTORICAL_COLUMNS)
    assert first["stance"] == "oppose" and first["stance_sign"] == "-1" and first["stance_src"] == S.STANCE_SRC
    # press_value = final_sign x |lsd_net_norm|: signed, tiny (a fraction of a word), same units as the history file
    net_norm = float(first["lsd_net_norm"])
    assert net_norm < 0 and float(first["press_value"]) == pytest.approx(-abs(net_norm), abs=1e-6)
    assert -0.2 < float(first["press_value"]) < 0
    # score_graded / score_label rewritten to the FINAL sign (legacy was +0.6 "Positive")
    assert first["score_graded"] == "-0.6" and first["score_label"] == "Negative"
    assert first["p_oppose"] == "0.9" and first["stance_conf"] == "0.9"
    assert first["n_words"] == str(len((LONG + " we oppose this and it is awful awful awful").split()))
    # DEBATE neutral -> sign 0, press_value 0, graded 0 / "Neutral"
    assert second["stance"] == "neutral" and second["stance_sign"] == "0"
    assert second["press_value"] == "0" and second["score_graded"] == "0" and second["score_label"] == "Neutral"
    assert report["scorer"] == S.STANCE_SRC and report["rows_scored"] == 2 and report["rows_cached"] == 0
    assert report["rows_pending"] == 0 and report["priority"] == "below_normal"


def test_a_row_without_a_300_character_body_gets_the_floor_intensity_like_the_history(tmp_path):
    fakes = Fakes()
    short, titled = row("c"), row("d", title="Title only we support credit unions")
    rows, report = run(tmp_path, [short, titled], [SHORT + " we support it great great great", ""], fakes)
    by_url = {r["url"]: r for r in rows}
    # run_stage5.R:136-143: has_text needs a 300-character body; otherwise intensity is BILL_W
    assert by_url[short["url"]]["press_value"] == "0.03" and by_url[short["url"]]["body_fallback"] == ""
    assert by_url[titled["url"]]["press_value"] == "0.03" and by_url[titled["url"]]["body_fallback"] == "title"
    # no body at all: the TITLE is what DEBATE and LSD scored (run_stage5.R:72-73)
    assert sorted(dict(fakes.debate_calls[0][0]).values()) == sorted([SHORT + " we support it great great great", titled["title"]])
    assert sorted(dict(fakes.lsd_calls[0]).values()) == sorted([SHORT + " we support it great great great", titled["title"]])
    assert report["title_fallback_rows"] == 1


def test_the_human_overlay_beats_debate_exactly_as_run_stage5_applies_it(tmp_path):
    fakes = Fakes()
    labelled, unlabelled = row("e"), row("f")
    overlay = tmp_path / "labels.csv"
    overlay.write_text("url,human_label,split\n%s,Oppose,test\n%s,NA,train\n%s,support,train\n" % (
        labelled["url"], unlabelled["url"], labelled["url"]), encoding="utf-8")
    rows, report = run(tmp_path, [labelled, unlabelled], [LONG + " we support this", LONG + " we support that"],
                       fakes, overlay_path=str(overlay))
    by_url = {r["url"]: r for r in rows}
    hit = by_url[labelled["url"]]
    assert hit["stance"] == "oppose" and hit["stance_sign"] == "-1" and hit["stance_src"] == "human_label"
    assert float(hit["press_value"]) <= 0 and hit["score_graded"] == "-0.6"      # first occurrence wins
    miss = by_url[unlabelled["url"]]
    assert miss["stance"] == "support" and miss["stance_src"] == S.STANCE_SRC    # an 'NA' label is not valid
    assert report["human_overlay_rows"] == 1


@pytest.mark.parametrize("case", ["base-model", "other-dir", "no-weights", "missing-dir"])
def test_it_refuses_a_set_env_unless_it_names_the_local_fine_tuned_directory(tmp_path, case):
    fakes = Fakes()
    model = make_model(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    env = {"base-model": {"CU_S5_DEBATE_MODEL": "mlburnham/Political_DEBATE_base_v1.0"},
           "other-dir": {"CU_S5_DEBATE_MODEL": str(other)}, "no-weights": {"CU_S5_DEBATE_MODEL": str(model)},
           "missing-dir": {"CU_S5_DEBATE_MODEL": str(tmp_path / "nope")}}[case]
    if case == "no-weights":
        (model / "model.safetensors").unlink()
    with pytest.raises(S.Stage5Refused):
        run(tmp_path, [row("g")], [LONG], fakes, model=model, env=env)
    assert fakes.debate_calls == [] and fakes.lsd_calls == []                  # refused before any scoring
    assert not (tmp_path / "cache.jsonl").exists()


def test_an_unset_env_defaults_to_debate_finetuned_under_the_draft_systems_root(tmp_path, monkeypatch):
    # Scheduled runners do not carry CU_S5_DEBATE_MODEL (owner ruling D-219, Paul 2026-10-15).
    model = make_model(tmp_path)
    monkeypatch.setattr(S, "DRAFT", str(tmp_path / "draft"))
    assert S.resolve_model_dir({}) == model.resolve()
    assert S.resolve_model_dir({"CU_S5_DEBATE_MODEL": "  "}) == model.resolve()      # blank is unset
    fakes = Fakes()
    rows, report = run(tmp_path, [row("g")], [LONG + " we support it"], fakes, model=model, env={},
                       expected_model_dir=None)
    assert [r["stance_src"] for r in rows] == [S.STANCE_SRC] and report["model_dir"] == model.resolve().as_posix()
    assert [call[1].resolve() for call in fakes.debate_calls] == [model.resolve()]


def test_an_unset_env_with_no_default_directory_is_refused_before_any_scoring(tmp_path, monkeypatch):
    fakes = Fakes()
    monkeypatch.setattr(S, "DRAFT", str(tmp_path / "draft_without_the_model"))
    with pytest.raises(S.Stage5Refused, match="CU_S5_DEBATE_MODEL is not set.*does not exist"):
        run(tmp_path, [row("g")], [LONG], fakes, model=tmp_path / "unused", env={}, expected_model_dir=None)
    assert fakes.debate_calls == [] and fakes.lsd_calls == []
    assert not (tmp_path / "cache.jsonl").exists()


@pytest.mark.parametrize("missing", ["model.safetensors", "config.json", "tokenizer.json"])
def test_an_unset_env_with_an_incomplete_default_directory_is_refused(tmp_path, monkeypatch, missing):
    fakes = Fakes()
    model = make_model(tmp_path)
    (model / missing).unlink()
    monkeypatch.setattr(S, "DRAFT", str(tmp_path / "draft"))
    with pytest.raises(S.Stage5Refused, match="not a complete model directory"):
        run(tmp_path, [row("g")], [LONG], fakes, model=model, env={}, expected_model_dir=None)
    assert fakes.debate_calls == []


def test_a_set_env_naming_another_directory_is_not_rescued_by_a_valid_default(tmp_path, monkeypatch):
    fakes = Fakes()
    model = make_model(tmp_path)
    other = make_model(tmp_path, name="debate_finetuned_base")
    monkeypatch.setattr(S, "DRAFT", str(tmp_path / "draft"))
    with pytest.raises(S.Stage5Refused, match="does not resolve to the local fine-tuned model"):
        run(tmp_path, [row("g")], [LONG], fakes, model=model, env={"CU_S5_DEBATE_MODEL": str(other)},
            expected_model_dir=None)
    assert fakes.debate_calls == []


def test_each_row_is_scored_once_and_a_changed_text_or_model_rescored(tmp_path):
    fakes = Fakes()
    rows_in = [row("h"), row("i"), row("j")]
    texts = [LONG + " one", LONG + " two", LONG + " three"]
    _, first = run(tmp_path, rows_in, texts, fakes)
    assert first["rows_scored"] == 3 and len(fakes.debate_calls) == 1
    rows, second = run(tmp_path, rows_in, texts, fakes)
    assert second["rows_scored"] == 0 and second["rows_cached"] == 3 and len(fakes.debate_calls) == 1
    assert len(rows) == 3
    texts[1] = LONG + " two, edited"                                           # the source text hash changed
    _, third = run(tmp_path, rows_in, texts, fakes)
    assert third["rows_scored"] == 1 and third["rows_cached"] == 2
    assert [text for _id, text in fakes.debate_calls[-1][0]] == [texts[1]]
    model = make_model(tmp_path, weights=b"weights-v2")                          # a different model directory
    _, fourth = run(tmp_path, rows_in, texts, fakes, model=model)
    assert fourth["rows_scored"] == 3 and fourth["model_dir_sha256"] != first["model_dir_sha256"]
    lines = (tmp_path / "cache.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 + 1 + 3


def test_the_model_hash_ignores_hidden_files_and_tracks_the_weights(tmp_path):
    model = make_model(tmp_path)
    before = S.model_dir_sha256(model)
    (model / ".Rhistory").write_text("noise", encoding="utf-8")
    assert S.model_dir_sha256(model) == before
    (model / "model.safetensors").write_bytes(b"weights-v3")
    assert S.model_dir_sha256(model) != before


def test_a_batch_bound_withholds_the_rest_and_the_next_invocation_picks_them_up(tmp_path):
    fakes = Fakes()
    rows_in = [row("k%d" % index) for index in range(5)]
    texts = [LONG + " row %d" % index for index in range(5)]
    first_rows, first = run(tmp_path, rows_in, texts, fakes, batch_limit=2)
    assert [r["url"] for r in first_rows] == [r["url"] for r in rows_in[:2]]
    assert first["rows_scored"] == 2 and first["rows_pending"] == 3
    assert all(r["stance_src"] == S.STANCE_SRC for r in first_rows)            # never another scorer's label
    second_rows, second = run(tmp_path, rows_in, texts, fakes, batch_limit=2)
    assert len(second_rows) == 4 and second["rows_scored"] == 2 and second["rows_cached"] == 2
    third_rows, third = run(tmp_path, rows_in, texts, fakes, batch_limit=2)
    assert len(third_rows) == 5 and third["rows_pending"] == 0 and len(fakes.debate_calls) == 3


FAKE_SIDECAR = '''
import argparse, csv, ctypes, json, os, sys
parser = argparse.ArgumentParser()
for flag in ("--in", "--out", "--model", "--batch", "--max_chars"):
    parser.add_argument(flag)
args = parser.parse_args()
if os.name == "nt":
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetPriorityClass.argtypes = [ctypes.c_void_p]
    priority = hex(kernel.GetPriorityClass(kernel.GetCurrentProcess()))
else:
    priority = os.nice(0)
with open(os.environ["FAKE_SIDECAR_LOG"], "w", encoding="utf-8") as handle:
    json.dump({"model": args.model, "batch": args.batch, "max_chars": args.max_chars,
               "offline": os.environ.get("HF_HUB_OFFLINE"), "priority": priority}, handle)
if os.environ.get("FAKE_SIDECAR_FAIL"):
    sys.exit(3)
rows = list(csv.DictReader(open(args.__dict__["in"], encoding="utf-8", newline="")))
with open(args.out, "w", newline="", encoding="utf-8") as handle:
    writer = csv.writer(handle)
    writer.writerow(["id", "stance", "stance_sign", "p_support", "p_oppose", "p_neutral", "stance_conf"])
    for item in rows:
        writer.writerow([item["id"], "oppose", -1, 0.1, 0.8, 0.1, 0.8])
'''


def test_the_debate_sidecar_is_called_with_the_local_model_offline_and_at_below_normal_priority(tmp_path, monkeypatch):
    model = make_model(tmp_path)
    script = tmp_path / "fake_stage5_debate.py"
    script.write_text(FAKE_SIDECAR, encoding="utf-8")
    log = tmp_path / "sidecar.json"
    monkeypatch.setenv("FAKE_SIDECAR_LOG", str(log))
    out = S.run_debate([("0", "first, text"), ("1", "second\nmultiline")], model, draft_dir=str(tmp_path / "draft"),
                       python=sys.executable, script=str(script))
    assert out["1"] == {"stance": "oppose", "stance_sign": -1, "p_support": 0.1, "p_oppose": 0.8,
                        "p_neutral": 0.1, "stance_conf": 0.8}
    seen = json.loads(log.read_text(encoding="utf-8"))
    assert seen["model"] == str(model) and seen["max_chars"] == "3000" and seen["batch"] == "16"
    assert seen["offline"] == "1"
    if os.name == "nt":
        assert seen["priority"] == "0x4000"                                    # BELOW_NORMAL_PRIORITY_CLASS
    else:
        assert seen["priority"] >= 10
    monkeypatch.setenv("FAKE_SIDECAR_FAIL", "1")
    with pytest.raises(S.Stage5Failed, match="rc 3"):
        S.run_debate([("0", "x")], model, draft_dir=str(tmp_path / "draft"), python=sys.executable, script=str(script))


def test_the_sidecar_must_exist_instead_of_falling_back(tmp_path):
    with pytest.raises(S.Stage5Failed, match="not found"):
        S.run_debate([("0", "x")], make_model(tmp_path), draft_dir=str(tmp_path / "draft"))


DRAFT_LSD = Path(S.DRAFT) / "stage5_lsd.R"


@pytest.mark.skipif(not DRAFT_LSD.is_file(), reason="Draft Systems stage5_lsd.R is not on this machine")
def test_lsd_runs_the_draft_systems_function_over_a_fake_dictionary(tmp_path):
    from collect.refresh_press_stream import find_rscript
    try:
        rscript = find_rscript()
    except RuntimeError:
        pytest.skip("Rscript is not installed")
    dictionary = tmp_path / "lsd"
    dictionary.mkdir()
    expression = ('saveRDS(list(positive=c("support*","good"), negative=c("harm*","bad"), '
                  'neg_positive=c("not good"), neg_negative=c("not bad")), "lsd_dict.rds")')
    made = subprocess.run([rscript, "-e", expression], cwd=dictionary, capture_output=True, text=True, timeout=120)
    if made.returncode:
        pytest.skip("Rscript could not write the fake dictionary: " + made.stderr[-200:])
    items = [("a", "We support this good bill. This is not good and harmful."), ("b", "We support the good bill"),
             ("c", ""), ("d", "Office hours")]
    try:
        out = S.run_lsd(items, lsd_dir=str(dictionary), script=str(DRAFT_LSD), rscript=rscript)
    except S.Stage5Failed as exc:
        if "stringi" in str(exc):
            pytest.skip("stringi is not installed for this R")
        raise
    # "not good" is a negated positive (counts once, as negative); the bare 'good' inside it is not counted again
    assert (out["a"]["lsd_pos"], out["a"]["lsd_neg"], out["a"]["lsd_net"]) == (2, 2, 0)
    assert (out["b"]["lsd_pos"], out["b"]["lsd_neg"], out["b"]["lsd_net"]) == (2, 0, 2)
    assert out["b"]["lsd_net_norm"] == pytest.approx(round(2 / out["b"]["n_words"], 5))
    assert out["c"]["n_words"] == 0 and out["c"]["lsd_net_norm"] == 0.0
    assert out["d"]["lsd_pos"] == out["d"]["lsd_neg"] == 0
