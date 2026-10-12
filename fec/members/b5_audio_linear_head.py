# -*- coding: utf-8 -*-
"""Frozen-Wav2Vec multinomial linear head for research-only speaker verification.

The audio-as-data design uses a single linear classification layer over frozen Wav2Vec embeddings.
This module keeps that method inside Melange's stricter identity contract: callers supply one
claimed member and receive evidence for that claim only.  The model never substitutes a different
member.  Training windows must be balanced across members and drawn from at least two events per
member.  The experiment manifest must keep train, calibration, and test events and sources
disjoint.

Artifacts are JSON, deterministic, SHA-pinned, and marked RESEARCH_CANDIDATE_NOT_CONSUMED.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict

import numpy as np

SCHEMA = "b5-audio-linear-head-v1"
STATUS = "RESEARCH_CANDIDATE_NOT_CONSUMED"
BACKEND = "wav2vec2-frozen-embedding"
TRIALS_SCHEMA = "b5-audio-identity-trials-v1"
SCORE_KIND = "claimed_class_probability"
ALLOWED_SPLITS = ("train", "calibration", "test")
MIN_CLASSES = 3
MIN_EVENTS_PER_MEMBER = 2
HYPERPARAMETERS = {
    "C": 1.0,
    "l1_ratio": 0.0,
    "max_iter": 2000,
    "solver": "lbfgs",
    "tol": 1e-8,
}


def _require_sha256(value, field):
    value = str(value or "").lower()
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError("%s must be a lowercase 64-character SHA-256" % field)
    return value


def _artifact_sha256(doc):
    blob = json.dumps(
        doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _embedding(raw, expected_dim=None):
    values = np.asarray(raw, dtype=np.float64).reshape(-1)
    if values.size == 0:
        raise ValueError("embedding must be non-empty")
    if expected_dim is not None and values.size != expected_dim:
        raise ValueError("embedding dimension %d != expected %d" %
                         (values.size, expected_dim))
    if not np.all(np.isfinite(values)):
        raise ValueError("embedding contains a non-finite value")
    return values


def _validated_training_rows(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError("training rows must be a non-empty list")
    out = []
    expected_dim = None
    seen_sources = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError("training row %d is not an object" % index)
        member = str(raw.get("member") or "").strip()
        event_id = str(raw.get("event_id") or "").strip()
        source_sha256 = _require_sha256(
            raw.get("source_sha256"), "training row %d source_sha256" % index)
        if not member or not event_id:
            raise ValueError("training row %d requires member and event_id" % index)
        if source_sha256 in seen_sources:
            raise ValueError("training source %s is duplicated" % source_sha256)
        seen_sources.add(source_sha256)
        vector = _embedding(raw.get("embedding"), expected_dim)
        expected_dim = vector.size if expected_dim is None else expected_dim
        out.append({
            "member": member,
            "event_id": event_id,
            "source_sha256": source_sha256,
            "embedding": vector,
        })
    counts = Counter(row["member"] for row in out)
    if len(counts) < MIN_CLASSES:
        raise ValueError("linear head requires at least %d member classes" % MIN_CLASSES)
    if len(set(counts.values())) != 1:
        raise ValueError("training windows must be exactly balanced across member classes")
    events = defaultdict(set)
    for row in out:
        events[row["member"]].add(row["event_id"])
    thin = {member: len(member_events) for member, member_events in events.items()
            if len(member_events) < MIN_EVENTS_PER_MEMBER}
    if thin:
        raise ValueError("every member needs at least %d distinct training events; got %r" %
                         (MIN_EVENTS_PER_MEMBER, thin))
    return out


def validate_source_disjoint_splits(rows):
    """Validate a complete event/source-disjoint train/calibration/test manifest."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("split manifest must be a non-empty list")
    members_by_split = defaultdict(set)
    split_counts = Counter()
    event_split = {}
    source_split = {}
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError("split row %d is not an object" % index)
        member = str(raw.get("member") or "").strip()
        split = str(raw.get("split") or "").strip().lower()
        event_id = str(raw.get("event_id") or "").strip()
        source_sha256 = _require_sha256(
            raw.get("source_sha256"), "split row %d source_sha256" % index)
        if not member or not event_id:
            raise ValueError("split row %d requires member and event_id" % index)
        if split not in ALLOWED_SPLITS:
            raise ValueError("split row %d has invalid split %r" % (index, split))
        prior_event_split = event_split.setdefault(event_id, split)
        if prior_event_split != split:
            raise ValueError("event %s leaks across %s and %s splits" %
                             (event_id, prior_event_split, split))
        if source_sha256 in source_split:
            raise ValueError("source %s is duplicated across the split manifest" % source_sha256)
        source_split[source_sha256] = split
        members_by_split[split].add(member)
        split_counts[split] += 1
    if set(members_by_split) != set(ALLOWED_SPLITS):
        raise ValueError("manifest requires train, calibration, and test splits")
    expected_members = set.union(*(members_by_split[split] for split in ALLOWED_SPLITS))
    incomplete = {
        member: [split for split in ALLOWED_SPLITS
                 if member not in members_by_split[split]]
        for member in sorted(expected_members)
    }
    incomplete = {member: missing for member, missing in incomplete.items() if missing}
    if incomplete:
        raise ValueError("every member must appear in all splits; missing %r" % incomplete)
    return {
        "members": sorted(expected_members),
        "split_counts": {split: split_counts[split] for split in sorted(ALLOWED_SPLITS)},
        "event_n": len(event_split),
        "source_n": len(source_split),
    }


def fit_linear_head(rows, training_sha256, split_manifest_sha256):
    """Fit the fixed multinomial logistic layer and return a deterministic JSON artifact."""
    training_sha256 = _require_sha256(training_sha256, "training_sha256")
    split_manifest_sha256 = _require_sha256(
        split_manifest_sha256, "split_manifest_sha256")
    clean = _validated_training_rows(rows)
    classes = sorted({row["member"] for row in clean})
    class_counts = Counter(row["member"] for row in clean)
    x = np.asarray([row["embedding"] for row in clean], dtype=np.float64)
    y = np.asarray([row["member"] for row in clean])
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-12] = 1.0
    standardized = (x - mean) / scale

    from sklearn import __version__ as sklearn_version
    from sklearn.linear_model import LogisticRegression

    model = LogisticRegression(
        C=HYPERPARAMETERS["C"],
        l1_ratio=HYPERPARAMETERS["l1_ratio"],
        solver=HYPERPARAMETERS["solver"],
        max_iter=HYPERPARAMETERS["max_iter"],
        tol=HYPERPARAMETERS["tol"],
        random_state=0,
    )
    model.fit(standardized, y)
    fitted_classes = [str(value) for value in model.classes_.tolist()]
    if fitted_classes != classes:
        raise RuntimeError("classifier class order %r != deterministic order %r" %
                           (fitted_classes, classes))
    if max(int(value) for value in model.n_iter_) >= HYPERPARAMETERS["max_iter"]:
        raise RuntimeError("linear head failed to converge before max_iter")

    return {
        "schema": SCHEMA,
        "status": STATUS,
        "backend": BACKEND,
        "classes": classes,
        "mean": [float(value) for value in mean],
        "scale": [float(value) for value in scale],
        "coef": [[float(value) for value in row] for row in model.coef_],
        "intercept": [float(value) for value in model.intercept_],
        "training_sha256": training_sha256,
        "split_manifest_sha256": split_manifest_sha256,
        "training": {
            "class_counts": {member: class_counts[member] for member in classes},
            "event_counts": {
                member: len({row["event_id"] for row in clean if row["member"] == member})
                for member in classes
            },
            "feature_dim": int(x.shape[1]),
            "hyperparameters": dict(HYPERPARAMETERS),
            "sklearn_version": sklearn_version,
            "n_iter": [int(value) for value in model.n_iter_],
            "class_weight": None,
            "feature_scaling_fit_on": "training split only",
            "model_selection": "none; one preregistered fixed linear head",
        },
        "decision_contract": {
            "operation": "score one caller-supplied claimed member",
            "score_kind": SCORE_KIND,
            "silent_relabel": False,
            "production_consumers": 0,
        },
    }


def score_claim(head, embedding, claimed_member):
    """Score exactly one supplied member claim without returning another member's identity."""
    if not isinstance(head, dict) or head.get("schema") != SCHEMA:
        raise ValueError("linear-head schema mismatch")
    if head.get("status") != STATUS:
        raise ValueError("linear-head artifact is not a research candidate")
    classes = [str(value) for value in head.get("classes") or []]
    claimed_member = str(claimed_member or "").strip()
    if claimed_member not in classes:
        raise ValueError("claimed member is absent from the linear head")
    mean = _embedding(head.get("mean"))
    scale = _embedding(head.get("scale"), mean.size)
    if np.any(scale <= 0):
        raise ValueError("linear-head scale must be positive")
    coef = np.asarray(head.get("coef"), dtype=np.float64)
    intercept = np.asarray(head.get("intercept"), dtype=np.float64).reshape(-1)
    if coef.shape != (len(classes), mean.size) or intercept.size != len(classes):
        raise ValueError("linear-head coefficient dimensions are malformed")
    vector = _embedding(embedding, mean.size)
    logits = coef.dot((vector - mean) / scale) + intercept
    logits = logits - float(np.max(logits))
    probabilities = np.exp(logits)
    probabilities = probabilities / float(np.sum(probabilities))
    claimed_index = classes.index(claimed_member)
    claimed_probability = float(probabilities[claimed_index])
    other_probability = max(
        float(value) for index, value in enumerate(probabilities) if index != claimed_index)
    return {
        "claimed_member": claimed_member,
        "claimed_probability": claimed_probability,
        "margin_over_best_other": claimed_probability - other_probability,
        "score_kind": SCORE_KIND,
    }


def build_claim_trials(head, rows, label_provenance):
    """Score supplied genuine/impostor claims into the generic calibration-trials contract."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("claim rows must be a non-empty list")
    label_provenance = str(label_provenance or "").strip()
    if not label_provenance:
        raise ValueError("label_provenance is required")
    seen = set()
    trials = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError("claim row %d is not an object" % index)
        trial_id = str(raw.get("trial_id") or "").strip()
        corpus = str(raw.get("corpus") or "").strip()
        claimed_member = str(raw.get("claimed_member") or "").strip()
        label = str(raw.get("label") or "").strip().lower()
        if not trial_id or trial_id in seen:
            raise ValueError("claim row %d has missing/duplicate trial_id" % index)
        seen.add(trial_id)
        if not corpus or not claimed_member:
            raise ValueError("claim row %d requires corpus and claimed_member" % index)
        if label not in ("genuine", "impostor"):
            raise ValueError("claim row %s label must be genuine or impostor" % trial_id)
        scored = score_claim(head, raw.get("embedding"), claimed_member)
        trials.append({
            "trial_id": trial_id,
            "corpus": corpus,
            "claimed_member": claimed_member,
            "label": label,
            "score": scored["claimed_probability"],
            "margin_over_best_other": scored["margin_over_best_other"],
            "sex": str(raw.get("sex") or "UNKNOWN").strip().upper() or "UNKNOWN",
            "caption_mismatch": bool(raw.get("caption_mismatch")),
        })
        for field in ("true_member", "event_id", "window_id", "split"):
            if raw.get(field) is not None:
                trials[-1][field] = str(raw[field])
    return {
        "schema": TRIALS_SCHEMA,
        "status": STATUS,
        "score_kind": SCORE_KIND,
        "label_provenance": label_provenance,
        "head_sha256": _artifact_sha256(head),
        "trials": trials,
    }
