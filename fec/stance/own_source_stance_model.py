# -*- coding: utf-8 -*-
"""Shared learned stance stack for speeches, interviews, hearings, and Bluesky.

The first cut of these channels used one sparse registry/cue heuristic.  This module ports the
production tweet/press architecture without adding API spend:

* LOCAL reader: word+character TF-IDF trained on the four own-source gold queues.
* TRANSFER reader: target-window TF-IDF trained on the existing tweet/press gold plus the own-source
  gold available in each training fold.
* RULE reader: the existing registry two-hop / target-scoped cue read.
* META reader: balanced logistic regression over honest out-of-fold base-reader probabilities and
  channel/context features.

Production precedence is deliberately simple and auditable:

    hand gold (content-hash id) > learned stack > reader-agreement tier

Gold is never used as a post-hoc feature or silently rewritten.  It is applied LAST to the final
sidecar label and receives tier ``gold``.  Non-gold rows receive unanimous/majority/review tiers from
the three independent base readers.  Hearing review rows remain excluded downstream by
``media_rules.hearing_tier_weight``.

Evaluation is grouped by channel/member/date and exact normalized text, so passages from the same
appearance and verified cross-posts cannot straddle train/test.  Auxiliary tweet/press rows matching
held-out text are also removed from that fold.
"""
from __future__ import annotations

import collections
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from dataclasses import dataclass

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import FeatureUnion, Pipeline, make_pipeline

FEC_BOOTSTRAP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if FEC_BOOTSTRAP not in sys.path:
    sys.path.insert(0, FEC_BOOTSTRAP)

from paths import ROOT
from lane_file_io import safe_to_unpickle, write_sha256_sidecar
import config_overlay as CO
import stance.media_rules as MR

CLASSES = ("support", "oppose", "neutral")
CHANNELS = ("speech", "interview", "hearing", "bluesky")
SCHEMA = "own-source-stance-stack-v2"
CACHE = os.path.join(ROOT, "data", "stance", "own_source_stance_model.joblib")
REPORT = os.path.join(ROOT, "data", "stance", "own_source_stance_accuracy_report.json")

CONFIG = {
    "speech": {
        "source": "speeches_cu.csv",
        "gold": "data/stance/speech_gold_queue.csv",
        "output": "speech_stance_final.csv",
        "description": "floor speeches",
    },
    "interview": {
        "source": "data/stance/interviews_cu.csv",
        "gold": "data/stance/interview_gold_queue.csv",
        "output": "data/stance/interview_stance_final.csv",
        "description": "interviews",
    },
    "hearing": {
        "source": "data/stance/hearings_cu.csv",
        "gold": "data/stance/hearing_gold_queue.csv",
        "output": "data/stance/hearing_stance_final.csv",
        "description": "hearing rows",
    },
    "bluesky": {
        "source": "data/stance/bluesky_cu.csv",
        "gold": "data/stance/bluesky_gold_queue.csv",
        "output": "data/stance/bluesky_stance_final.csv",
        "description": "Bluesky posts",
    },
}

REGISTRY = CO.merged_rows(
    "data/stance/cu_valence_registry.json", "entries", ("key",), data_root=ROOT
)

_ANCHOR_RX = re.compile(
    r"cfpb|consumer financial protection|interchange|swipe fee|credit card competition|durbin|"
    r"overdraft|junk fee|rate cap",
    re.I,
)
_ATTACK_RX = [
    re.compile(p, re.I)
    for p in (
        r"\brein (?:it |them )?in\b",
        r"\bdefund",
        r"\bgut\b",
        r"\babolish",
        r"\boverreach",
        r"\bunaccountable\b",
        r"\brogue\b",
    )
]
_DEFEND_RX = [
    re.compile(p, re.I)
    for p in (
        r"\bprotect",
        r"\bdefend",
        r"\bsave\b",
        r"\bstrengthen",
        r"\bexpand",
        r"\bgutting\b",
        r"\battack on\b",
    )
]


@dataclass
class Example:
    channel: str
    row_id: str
    member: str
    date: str
    categories: str
    registry_target: str
    registry_valence: float
    text: str
    label: str = ""
    attribution_flag: str = ""


def row_id(bioguide, date, text):
    return "x" + hashlib.md5(
        ("%s|%s|%s" % (bioguide or "", date or "", text or "")).encode("utf-8", "replace")
    ).hexdigest()[:12]


def normalized_text(text):
    text = re.sub(r"https?://\S+", " ", (text or "").lower())
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def registry_match(text):
    blob = (text or "").lower()
    for entry in REGISTRY:
        if any(match in blob for match in entry["match"]):
            return entry["key"], float(entry["valence"])
    return "", 0.0


def compose(stance, valence):
    stance = (stance or "").lower()
    if stance == "neutral" or valence == 0:
        return "neutral"
    if stance == "support":
        return "support" if valence > 0 else "oppose"
    if stance == "oppose":
        return "oppose" if valence > 0 else "support"
    return "neutral"


def rule_label(text):
    """The pre-existing registry/cue reader, retained as one ensemble input rather than the model."""
    key, valence = registry_match(text)
    directional = (
        key.startswith("cfpb")
        or key in ("interchange", "rate_cap", "overdraft", "junk_fees")
        or valence == -1
    )
    if not key or not directional:
        return "neutral"
    anchors = [match.start() for match in _ANCHOR_RX.finditer(text or "")]
    if not anchors:
        return "neutral"
    windows = " ".join((text or "")[max(0, a - 120) : a + 120] for a in anchors)
    windows = re.sub(
        r"(?:consumer )?financial protection( bureau| board| agency)?",
        " cfpb ",
        windows,
        flags=re.I,
    )
    attack = any(rx.search(windows) for rx in _ATTACK_RX)
    defend = any(rx.search(windows) for rx in _DEFEND_RX)
    toward = "oppose" if attack and not defend else ("support" if defend and not attack else "neutral")
    if key.startswith("cfpb") and toward in ("support", "oppose"):
        return compose(toward, -1)
    return compose(toward, valence)


def _read_csv(path):
    with io.open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def source_examples(channel):
    cfg = CONFIG[channel]
    rows = _read_csv(os.path.join(ROOT, cfg["source"]))
    out = []
    for row in rows:
        text = row.get("text") or ""
        key, valence = registry_match(text)
        out.append(
            Example(
                channel=channel,
                row_id=row_id(row.get("bioguide"), row.get("date"), text),
                member=(row.get("member") or "").strip(),
                date=(row.get("date") or "").strip(),
                categories=(row.get("categories") or "").strip(),
                registry_target=key,
                registry_valence=valence,
                text=text,
            )
        )
    return out


def filled_gold_rows(channel):
    """Filled, valid hand labels keyed by their content-hash id."""
    rows = _read_csv(os.path.join(ROOT, CONFIG[channel]["gold"]))
    return {
        (row.get("id") or "").strip(): row
        for row in rows
        if (row.get("id") or "").strip()
        and (row.get("gold_stance") or "").strip().lower() in CLASSES
    }


def gold_map(channel):
    return {
        (row.get("id") or "").strip(): (row.get("gold_stance") or "").strip().lower()
        for row in filled_gold_rows(channel).values()
    }


ADJUDICATED_RETIRED = {
    "x3d57678f72eb": (
        "hearing",
        "category 'CU-Bank Competitive Dynamics' vetoed by the COMPETITIVE context guard (391a4a8b5)",
        "ratified by Paul 2026-08-25 via interactive approval",
    ),
    "xc9c69e3250ca": (
        "hearing",
        "category 'CU-Bank Competitive Dynamics' vetoed by the COMPETITIVE context guard (391a4a8b5)",
        "ratified by Paul 2026-08-25 via interactive approval",
    ),
    "x0ed867384910": (
        "bluesky",
        "road-interchange false positive killed by the _ROAD_INTERCHANGE clause (391a4a8b5)",
        "ratified by Paul 2026-08-25 via interactive approval",
    ),
}
"""Hand labels Paul has personally ruled retired, keyed by content-hash id.

Each value is ``(channel, reason, ratification)``.  These three rows stopped binding because the
2026-07-16 precision-first category router (commit 391a4a8b5) withdrew their categories, so the
extractors no longer emit the passage into the channel's ``*_cu.csv`` source:

* ``x3d57678f72eb`` - hearing, B001282 Andy Barr 2022-04-27, gold_stance=support.
* ``xc9c69e3250ca`` - hearing, W000812 Ann Wagner 2025-02-05, gold_stance=support.
  Both declare only ``CU-Bank Competitive Dynamics``, which ``media_rules._category_matches`` now
  vetoes without credit-union or big-bank context.  The passage text is byte-identical in source.
* ``x0ed867384910`` - bluesky, S001200 Darren Soto 2025-07-25, gold_stance=neutral.  A road
  interchange ribbon-cutting, killed by the ``_ROAD_INTERCHANGE`` clause of the same commit.

Scope and change control:

* The fail-loud orphan contract is UNCHANGED for every id not listed here.  An unlisted gold row
  that stops binding still raises in ``own_gold_examples`` and still returns 3 from ``run_channel``.
* Listed rows are exempted from the abort and are NOT trained on, exactly like
  ``retired_precision_orphan`` rows.  The gold label is neither rewritten nor weakened.
* Honest both ways: if a listed id binds again, the exemption does not apply, a stale-ledger notice
  is printed, and the row trains normally.
* ADDING AN ENTRY REQUIRES PAUL'S EXPLICIT RATIFICATION.  This is a user-gated adjudication, not a
  model judgement: no agent may add, widen, or repurpose an entry to clear a failing guard.
"""


def retired_precision_orphan(channel, row):
    """Allow only neutral speech gold retired by the precision-first category router.

    Directional gold and all other channels remain fail-loud. This preserves negative training rows
    in the labeling file without forcing known road/rail uses of "interchange" back into the CU corpus.
    """
    if channel != "speech" or (row.get("gold_stance") or "").strip().lower() != "neutral":
        return False
    declared = {c.strip() for c in (row.get("categories") or "").split(";") if c.strip()}
    if not declared:
        return False
    routed = set(MR.route_raw_categories(row.get("text_snippet") or row.get("text") or ""))
    return not bool(declared & routed)


def gold_orphans(channel, source_ids):
    rows = filled_gold_rows(channel)
    orphan_ids = set(rows) - set(source_ids)
    retired = {rid for rid in orphan_ids if retired_precision_orphan(channel, rows[rid])}
    for rid in sorted(ADJUDICATED_RETIRED):
        ledger_channel, reason, _ratification = ADJUDICATED_RETIRED[rid]
        if ledger_channel != channel or rid not in rows:
            continue
        if rid in orphan_ids:
            retired.add(rid)
            print("adjudicated-retired gold row skipped: %s (%s; %s)" % (rid, channel, reason))
        else:
            print(
                "!! adjudicated-retired id %s binds again -- ledger entry may be stale; "
                "re-adjudicate" % rid
            )
    return rows, retired, orphan_ids - retired


def own_gold_examples():
    examples = []
    for channel in CHANNELS:
        source = source_examples(channel)
        by_id = {example.row_id: example for example in source}
        rows, retired, blocking = gold_orphans(channel, by_id)
        if blocking:
            raise RuntimeError(
                "%s gold labels no longer bind to source rows: %s" % (channel, sorted(blocking)[:8])
            )
        for rid, row in rows.items():
            if rid in retired:
                continue
            label = (row.get("gold_stance") or "").strip().lower()
            example = by_id[rid]
            example.label = label
            examples.append(example)
    return examples


def auxiliary_examples():
    """Existing tweet/press human gold used only by the transfer reader."""
    out = []
    tweet_path = os.path.join(ROOT, "data", "stance", "tweet_gold_queue.csv")
    if os.path.exists(tweet_path):
        for row in _read_csv(tweet_path):
            label = (row.get("gold_cu_alignment") or "").strip().lower()
            text = (row.get("text") or "").strip()
            if label not in CLASSES or not text:
                continue
            key, valence = registry_match(text)
            out.append(
                Example(
                    channel="tweet",
                    row_id=(row.get("id") or "").strip(),
                    member=(row.get("member") or "").strip(),
                    date=(row.get("date") or "").strip(),
                    categories=(row.get("slices") or "").strip(),
                    registry_target=key,
                    registry_valence=valence,
                    text=text,
                    label=label,
                )
            )
    press_path = os.path.join(ROOT, "data", "stance", "cu_golden_labels.csv")
    if os.path.exists(press_path):
        for row in _read_csv(press_path):
            label = (row.get("label") or "").strip().lower()
            text = ((row.get("title") or "") + ". " + (row.get("text") or "")).strip(". ")
            if label not in CLASSES or not text:
                continue
            key, valence = registry_match(
                (row.get("target") or "") + " " + text
            )
            out.append(
                Example(
                    channel="press",
                    row_id=(row.get("url") or "").strip(),
                    member=(row.get("member") or "").strip(),
                    date=(row.get("date") or "").strip(),
                    categories=(row.get("source") or "").strip(),
                    registry_target=(row.get("target") or key).strip(),
                    registry_valence=valence,
                    text=text,
                    label=label,
                )
            )
    return out


def full_text(example):
    return (example.text or ".")[:6000]


def context_text(example):
    text = example.text or ""
    anchors = [match.start() for match in _ANCHOR_RX.finditer(text)]
    if anchors:
        body = " ".join(text[max(0, a - 350) : a + 350] for a in anchors[:6])
    else:
        body = text[:1600]
    return (
        "channel: %s. categories: %s. target: %s. %s"
        % (example.channel, example.categories, example.registry_target or "none", body)
    )[:5000]


def local_reader():
    return Pipeline(
        [
            (
                "features",
                FeatureUnion(
                    [
                        (
                            "word",
                            TfidfVectorizer(
                                ngram_range=(1, 2), min_df=2, sublinear_tf=True, max_features=45000
                            ),
                        ),
                        (
                            "char",
                            TfidfVectorizer(
                                analyzer="char_wb",
                                ngram_range=(3, 5),
                                min_df=2,
                                sublinear_tf=True,
                                max_features=45000,
                            ),
                        ),
                    ]
                ),
            ),
            (
                "model",
                LogisticRegression(C=4.0, max_iter=3000, class_weight="balanced"),
            ),
        ]
    )


def transfer_reader():
    return make_pipeline(
        TfidfVectorizer(
            ngram_range=(1, 2), min_df=2, sublinear_tf=True, max_features=60000
        ),
        LogisticRegression(C=4.0, max_iter=3000, class_weight="balanced"),
    )


def _probabilities(model, texts):
    result = np.zeros((len(texts), len(CLASSES)))
    raw = model.predict_proba(texts)
    for index, label in enumerate(model.classes_):
        if label in CLASSES:
            result[:, CLASSES.index(label)] = raw[:, index]
    return result


def _onehot(label, domain):
    return [1.0 if label == value else 0.0 for value in domain]


def fixed_features(examples):
    rows = []
    for example in examples:
        text = example.text or ""
        rows.append(
            _onehot(rule_label(text), CLASSES)
            + _onehot(example.channel, CHANNELS)
            + [
                float(example.registry_valence),
                1.0 if _ANCHOR_RX.search(text) else 0.0,
                math.log1p(len(text)) / 10.0,
                min(8, len([x for x in example.categories.split(";") if x.strip()])) / 8.0,
                1.0 if example.attribution_flag else 0.0,
            ]
        )
    return np.asarray(rows, dtype=float)


def meta_features(examples, local_probability, transfer_probability):
    """Reader probabilities plus channel-specific interactions.

    A pooled linear meta-reader otherwise has to give the rule/transfer readers one global weight,
    even though their utility differs sharply between long hearings, floor speeches, and short social
    posts.  These interactions let one fixed, auditable meta model learn channel-specific reader
    weights without fitting four fragile small-sample models.
    """
    fixed = fixed_features(examples)
    reader_block = np.hstack((local_probability, transfer_probability, fixed[:, : len(CLASSES)]))
    interactions = []
    for channel in CHANNELS:
        mask = np.asarray(
            [1.0 if example.channel == channel else 0.0 for example in examples]
        ).reshape(-1, 1)
        interactions.append(reader_block * mask)
    return np.hstack((local_probability, transfer_probability, fixed, *interactions))


def group_keys(examples):
    normalized_counts = collections.Counter(normalized_text(example.text) for example in examples)
    groups = []
    for example in examples:
        normalized = normalized_text(example.text)
        if normalized and normalized_counts[normalized] > 1:
            groups.append("text:" + hashlib.sha1(normalized.encode("utf-8")).hexdigest())
        else:
            groups.append(
                "event:%s|%s|%s" % (example.channel, example.member, example.date[:10])
            )
    return np.asarray(groups)


def metrics(labels, predictions):
    labels = list(labels)
    predictions = list(predictions)
    tp = collections.Counter()
    fp = collections.Counter()
    fn = collections.Counter()
    for gold, predicted in zip(labels, predictions):
        if gold == predicted:
            tp[gold] += 1
        else:
            fp[predicted] += 1
            fn[gold] += 1
    f1 = {
        label: 2 * tp[label] / max(1, 2 * tp[label] + fp[label] + fn[label])
        for label in CLASSES
    }
    return {
        "n": len(labels),
        "accuracy": sum(a == b for a, b in zip(labels, predictions)) / max(1, len(labels)),
        "macro_f1": sum(f1.values()) / len(CLASSES),
        "per_class_f1": f1,
        "gold_distribution": dict(collections.Counter(labels)),
        "prediction_distribution": dict(collections.Counter(predictions)),
    }


def reader_tier(rule_read, local_read, transfer_read):
    reads = (rule_read, local_read, transfer_read)
    counts = collections.Counter(reads)
    if len(counts) == 1:
        return "unanimous"
    if counts.most_common(1)[0][1] >= 2:
        return "majority"
    return "review"


def input_fingerprint():
    names = ["data/stance/cu_valence_registry.json", "data/stance/tweet_gold_queue.csv", "data/stance/cu_golden_labels.csv"]
    for channel in CHANNELS:
        names.extend((CONFIG[channel]["source"], CONFIG[channel]["gold"]))
    digest = hashlib.sha256(SCHEMA.encode("ascii"))
    for name in names:
        paths = (CO.candidate_paths(name, data_root=ROOT)
                 if name == "data/stance/cu_valence_registry.json"
                 else [os.path.join(ROOT, name)])
        for path in paths:
            digest.update(path.encode("utf-8"))
            if not os.path.exists(path):
                digest.update(b"MISSING")
                continue
            with open(path, "rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
    return digest.hexdigest()


def train_bundle():
    own = own_gold_examples()
    auxiliary = auxiliary_examples()
    labels = np.asarray([example.label for example in own])
    groups = group_keys(own)
    splitter = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=7)
    folds = list(splitter.split(np.arange(len(own)), labels, groups))

    local_oof = np.zeros((len(own), len(CLASSES)))
    transfer_oof = np.zeros((len(own), len(CLASSES)))
    aux_norm = np.asarray([normalized_text(example.text) for example in auxiliary])
    for train_index, test_index in folds:
        local = local_reader()
        local.fit([full_text(own[i]) for i in train_index], labels[train_index])
        local_oof[test_index] = _probabilities(
            local, [full_text(own[i]) for i in test_index]
        )

        held_text = {normalized_text(own[i].text) for i in test_index}
        fold_aux = [
            example
            for index, example in enumerate(auxiliary)
            if not aux_norm[index] or aux_norm[index] not in held_text
        ]
        transfer_train = [own[i] for i in train_index] + fold_aux
        transfer_labels = [example.label for example in transfer_train]
        transfer = transfer_reader()
        transfer.fit([context_text(example) for example in transfer_train], transfer_labels)
        transfer_oof[test_index] = _probabilities(
            transfer, [context_text(own[i]) for i in test_index]
        )

    features = meta_features(own, local_oof, transfer_oof)
    meta_oof = np.empty(len(own), dtype=object)
    meta_probability = np.zeros((len(own), len(CLASSES)))
    for train_index, test_index in folds:
        meta = LogisticRegression(C=1.0, max_iter=3000, class_weight="balanced")
        meta.fit(features[train_index], labels[train_index])
        meta_oof[test_index] = meta.predict(features[test_index])
        meta_probability[test_index] = _probabilities(meta, features[test_index])

    local_predictions = np.asarray(CLASSES)[np.argmax(local_oof, axis=1)]
    transfer_predictions = np.asarray(CLASSES)[np.argmax(transfer_oof, axis=1)]
    rule_predictions = np.asarray([rule_label(example.text) for example in own])
    average_predictions = np.asarray(CLASSES)[
        np.argmax(0.70 * local_oof + 0.30 * transfer_oof, axis=1)
    ]
    reader_tiers = np.asarray(
        [
            reader_tier(rule_predictions[i], local_predictions[i], transfer_predictions[i])
            for i in range(len(own))
        ]
    )
    report = {
        "schema": SCHEMA,
        "evaluation": "5-fold StratifiedGroupKFold OOF on own-source gold; exact text and member/date events grouped",
        "gold_rows": len(own),
        "auxiliary_rows": len(auxiliary),
        "stack": metrics(labels, meta_oof),
        "local_reader": metrics(labels, local_predictions),
        "transfer_reader": metrics(labels, transfer_predictions),
        "rule_reader": metrics(labels, rule_predictions),
        "weighted_reader_average": metrics(labels, average_predictions),
        "tiers": {},
        "channels": {},
    }
    for tier in ("unanimous", "majority", "review"):
        index = np.where(reader_tiers == tier)[0]
        report["tiers"][tier] = metrics(labels[index], meta_oof[index])
    for channel in CHANNELS:
        index = [i for i, example in enumerate(own) if example.channel == channel]
        channel_report = {
            "stack": metrics(labels[index], meta_oof[index]),
            "local_reader": metrics(labels[index], local_predictions[index]),
            "transfer_reader": metrics(labels[index], transfer_predictions[index]),
            "rule_reader": metrics(labels[index], rule_predictions[index]),
            "weighted_reader_average": metrics(labels[index], average_predictions[index]),
            "tiers": {},
        }
        for tier in ("unanimous", "majority", "review"):
            tier_index = [i for i in index if reader_tiers[i] == tier]
            channel_report["tiers"][tier] = metrics(labels[tier_index], meta_oof[tier_index])
        report["channels"][channel] = channel_report

    local = local_reader()
    local.fit([full_text(example) for example in own], labels)
    transfer_train = own + auxiliary
    transfer = transfer_reader()
    transfer.fit(
        [context_text(example) for example in transfer_train],
        [example.label for example in transfer_train],
    )
    meta = LogisticRegression(C=1.0, max_iter=3000, class_weight="balanced")
    meta.fit(features, labels)
    return {
        "schema": SCHEMA,
        "fingerprint": input_fingerprint(),
        "classes": CLASSES,
        "local": local,
        "transfer": transfer,
        "meta": meta,
        "report": report,
    }


def _atomic_json(path, document):
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


def _sha256_file(path):
    digest=hashlib.sha256()
    with open(path,'rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''): digest.update(block)
    return digest.hexdigest()


def load_or_train(force=False):
    fingerprint = input_fingerprint()
    # joblib.load unpickles, which runs code: a sidecar mismatch skips the load and retrains instead.
    if not force and os.path.exists(CACHE) and safe_to_unpickle(CACHE):
        try:
            bundle = joblib.load(CACHE)
            if bundle.get("schema") == SCHEMA and bundle.get("fingerprint") == fingerprint:
                _atomic_json(REPORT, bundle["report"])
                return bundle
        except Exception as exc:
            print("own-source stance cache unreadable; retraining:", exc)
    bundle = train_bundle()
    tmp = CACHE + ".tmp"
    joblib.dump(bundle, tmp)
    os.replace(tmp, CACHE)
    write_sha256_sidecar(CACHE)
    _atomic_json(REPORT, bundle["report"])
    return bundle


def predict(bundle, examples):
    local_prob = _probabilities(bundle["local"], [full_text(example) for example in examples])
    transfer_prob = _probabilities(
        bundle["transfer"], [context_text(example) for example in examples]
    )
    features = meta_features(examples, local_prob, transfer_prob)
    stack = bundle["meta"].predict(features)
    local = np.asarray(CLASSES)[np.argmax(local_prob, axis=1)]
    transfer = np.asarray(CLASSES)[np.argmax(transfer_prob, axis=1)]
    rule = np.asarray([rule_label(example.text) for example in examples])
    return stack, local, transfer, rule


def run_channel(channel, force_train=False):
    if channel not in CONFIG:
        raise ValueError("unknown own-source stance channel %r" % channel)
    cfg = CONFIG[channel]
    source_path = os.path.join(ROOT, cfg["source"])
    if not os.path.exists(source_path):
        print("%s absent (%s) - nothing to score, skipping." % (cfg["source"], source_path))
        return 0

    gold_path = os.path.join(ROOT, cfg["gold"])
    source_receipt_path = os.path.splitext(source_path)[0] + '.receipt.json'
    input_hashes_before = {
        source_path: _sha256_file(source_path),
        gold_path: _sha256_file(gold_path),
    }
    source_receipt_sha_before = (
        _sha256_file(source_receipt_path) if os.path.exists(source_receipt_path) else None
    )
    if channel == 'interview':
        if source_receipt_sha_before is None:
            raise RuntimeError('interview extract receipt is required before stance scoring')
        with io.open(source_receipt_path, encoding='utf-8') as handle:
            source_receipt = json.load(handle)
        if ((source_receipt.get('extract') or {}).get('output_sha256')
                != input_hashes_before[source_path]):
            raise RuntimeError('interview extract is not hash-bound to its source receipt')

    examples = source_examples(channel)
    gold = gold_map(channel)
    source_ids = {example.row_id for example in examples}
    _gold_rows, retired, blocking = gold_orphans(channel, source_ids)
    for rid in retired:
        gold.pop(rid, None)
    if retired:
        # The two retirement mechanisms are reported separately: the summary line must not
        # describe an adjudicated hearing/bluesky row as a neutral precision-negative speech row.
        adjudicated = sorted(rid for rid in retired if rid in ADJUDICATED_RETIRED)
        precision = sorted(rid for rid in retired if rid not in ADJUDICATED_RETIRED)
        if precision:
            print("retired %d neutral precision-negative speech gold rows: %s" %
                  (len(precision), precision[:8]))
        if adjudicated:
            print("retired %d adjudicated gold rows (see ADJUDICATED_RETIRED): %s" %
                  (len(adjudicated), adjudicated[:8]))
    if blocking and os.environ.get("CU_GOLD_ORPHAN_OK", "0") != "1":
        print(
            "!! GOLD ORPHANS: %d of %d hand labels no longer bind: %s"
            % (len(blocking), len(gold), sorted(blocking)[:8])
        )
        return 3

    bundle = load_or_train(force_train)
    stack, local, transfer, rule = predict(bundle, examples)
    source_rows = _read_csv(source_path)
    if len(source_rows) != len(examples) or len(stack) != len(examples):
        raise RuntimeError('own-source row cardinality changed while scoring')
    input_hashes_after = {
        source_path: _sha256_file(source_path),
        gold_path: _sha256_file(gold_path),
    }
    if input_hashes_after != input_hashes_before:
        raise RuntimeError('own-source inputs changed while stance scoring was running')
    source_receipt_sha_after = (
        _sha256_file(source_receipt_path) if os.path.exists(source_receipt_path) else None
    )
    if source_receipt_sha_after != source_receipt_sha_before:
        raise RuntimeError('own-source extract receipt changed while stance scoring was running')
    if bundle.get('fingerprint') != input_fingerprint():
        raise RuntimeError('own-source model inputs changed while stance scoring was running')
    output = []
    tiers = collections.Counter()
    labels = collections.Counter()
    gold_count = 0
    for index, (example, source_row) in enumerate(zip(examples, source_rows)):
        model_label = str(stack[index])
        tier = reader_tier(str(rule[index]), str(local[index]), str(transfer[index]))
        final_label = gold.get(example.row_id, model_label)
        gold_applied = example.row_id in gold
        if gold_applied:
            tier = "gold"
            gold_count += 1
        tiers[tier] += 1
        labels[final_label] += 1
        output.append(
            {
                "bioguide": (source_row.get("bioguide") or "").strip(),
                "member": (source_row.get("member") or "").strip(),
                "date": (source_row.get("date") or "").strip(),
                "categories": (source_row.get("categories") or "").strip(),
                "stance": final_label,
                "tier": tier,
                "model_stance": model_label,
                "rule_read": str(rule[index]),
                "local_read": str(local[index]),
                "transfer_read": str(transfer[index]),
                "gold_applied": "1" if gold_applied else "0",
            }
        )
    out_path = os.path.join(ROOT, cfg["output"])
    tmp = out_path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(output[0].keys()))
        writer.writeheader()
        writer.writerows(output)
    os.replace(tmp, out_path)
    receipt={
        'schema':'own-source-channel-score-receipt-v1','status':'COMPLETE_HASH_BOUND',
        'generated_at':datetime.now(timezone.utc).isoformat(),'channel':channel,
        'model_fingerprint':bundle.get('fingerprint'),'rows':len(output),
        'inputs_sha256':{
            os.path.relpath(source_path,ROOT).replace('\\','/'):input_hashes_before[source_path],
            os.path.relpath(gold_path,ROOT).replace('\\','/'):input_hashes_before[gold_path],
        },
        'source_receipt':(os.path.relpath(source_receipt_path,ROOT).replace('\\','/')
                          if os.path.exists(source_receipt_path) else None),
        'source_receipt_sha256':source_receipt_sha_before,
        'output':os.path.relpath(out_path,ROOT).replace('\\','/'),
        'output_sha256':_sha256_file(out_path),
    }
    _atomic_json(os.path.splitext(out_path)[0]+'.receipt.json',receipt)
    print(
        "scored %d %s -> %s | gold %d | labels %s | tiers %s"
        % (
            len(output),
            cfg["description"],
            cfg["output"],
            gold_count,
            dict(labels),
            dict(tiers),
        )
    )
    return 0


def print_report(report):
    stack = report["stack"]
    print(
        "OWN-SOURCE STACK honest OOF on %d golds: acc=%.3f macroF1=%.3f"
        % (stack["n"], stack["accuracy"], stack["macro_f1"])
    )
    for reader in ("local_reader", "transfer_reader", "rule_reader"):
        row = report[reader]
        print("  %-15s acc=%.3f macroF1=%.3f" % (reader, row["accuracy"], row["macro_f1"]))
    for channel in CHANNELS:
        row = report["channels"][channel]["stack"]
        print(
            "  %-15s n=%-3d acc=%.3f macroF1=%.3f"
            % (channel, row["n"], row["accuracy"], row["macro_f1"])
        )


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    force = "--fresh" in argv
    bundle = load_or_train(force)
    print_report(bundle["report"])
    requested = [channel for channel in CHANNELS if "--" + channel in argv]
    if "--score-all" in argv:
        requested = list(CHANNELS)
    for channel in requested:
        code = run_channel(channel)
        if code:
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
