# -*- coding: utf-8 -*-
"""Paper-exact Adam-trained single layer over raw frozen Wav2Vec embeddings.

Mestre and Ryan's speaker experiment mean-pools a 768-dimensional Wav2Vec representation for each
utterance, then trains one neural classification layer with Adam (learning rate .001),
cross-entropy, batch size 8, and early-stopping patience 3.  This candidate implements that fixed
contract without hyperparameter search.  Melange adds stricter source/event split isolation and
verification-only scoring: the caller supplies one claim, and no alternative identity is returned.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict

import numpy as np

SCHEMA = "b5-audio-neural-linear-head-v1"
STATUS = "RESEARCH_CANDIDATE_NOT_CONSUMED"
BACKEND = "wav2vec2-raw-mean-pooled-frozen"
SCORE_KIND = "claimed_class_probability"
TRIALS_SCHEMA = "b5-audio-identity-trials-v1"
MIN_CLASSES = 3
MIN_TRAIN_EVENTS_PER_MEMBER = 2
HYPERPARAMETERS = {
    "batch_size": 8,
    "early_stopping_patience": 3,
    "learning_rate": 0.001,
    "loss": "cross_entropy",
    "max_epochs": 200,
    "optimizer": "Adam",
    "seed": 0,
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
    vector = np.asarray(raw, dtype=np.float64).reshape(-1)
    if not vector.size:
        raise ValueError("embedding must be non-empty")
    if expected_dim is not None and vector.size != expected_dim:
        raise ValueError("embedding dimension %d != expected %d" %
                         (vector.size, expected_dim))
    if not np.all(np.isfinite(vector)):
        raise ValueError("embedding contains non-finite values")
    return vector


def _validated_rows(rows, split, expected_dim=None):
    if not isinstance(rows, list) or not rows:
        raise ValueError("%s rows must be a non-empty list" % split)
    clean = []
    seen_sources = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError("%s row %d is not an object" % (split, index))
        member = str(raw.get("member") or "").strip()
        event_id = str(raw.get("event_id") or "").strip()
        source_sha256 = _require_sha256(
            raw.get("source_sha256"), "%s row %d source_sha256" % (split, index))
        if not member or not event_id:
            raise ValueError("%s row %d requires member and event_id" % (split, index))
        if source_sha256 in seen_sources:
            raise ValueError("%s source %s is duplicated" % (split, source_sha256))
        seen_sources.add(source_sha256)
        vector = _embedding(raw.get("embedding"), expected_dim)
        expected_dim = vector.size if expected_dim is None else expected_dim
        clean.append({
            "member": member,
            "event_id": event_id,
            "source_sha256": source_sha256,
            "embedding": vector,
        })
    counts = Counter(row["member"] for row in clean)
    if len(counts) < MIN_CLASSES:
        raise ValueError("neural head requires at least %d member classes" % MIN_CLASSES)
    if len(set(counts.values())) != 1:
        raise ValueError("%s windows must be exactly balanced across member classes" % split)
    events = defaultdict(set)
    for row in clean:
        events[row["member"]].add(row["event_id"])
    if split == "train":
        thin = {member: len(value) for member, value in events.items()
                if len(value) < MIN_TRAIN_EVENTS_PER_MEMBER}
        if thin:
            raise ValueError("every member needs at least %d training events; got %r" %
                             (MIN_TRAIN_EVENTS_PER_MEMBER, thin))
    return clean, expected_dim


def _validate_train_validation(train_rows, validation_rows):
    train, dim = _validated_rows(train_rows, "train")
    validation, _ = _validated_rows(validation_rows, "validation", dim)
    train_classes = sorted({row["member"] for row in train})
    validation_classes = sorted({row["member"] for row in validation})
    if train_classes != validation_classes:
        raise ValueError("train and validation member classes must match exactly")
    train_events = {row["event_id"] for row in train}
    validation_events = {row["event_id"] for row in validation}
    event_overlap = sorted(train_events & validation_events)
    if event_overlap:
        raise ValueError("event leakage between train and validation: %r" % event_overlap[:3])
    train_sources = {row["source_sha256"] for row in train}
    validation_sources = {row["source_sha256"] for row in validation}
    source_overlap = sorted(train_sources & validation_sources)
    if source_overlap:
        raise ValueError("source leakage between train and validation: %r" % source_overlap[:3])
    return train, validation, train_classes, dim


def fit_neural_head(train_rows, validation_rows, training_sha256, split_manifest_sha256):
    """Fit the fixed one-layer neural model and return a deterministic JSON artifact."""
    training_sha256 = _require_sha256(training_sha256, "training_sha256")
    split_manifest_sha256 = _require_sha256(
        split_manifest_sha256, "split_manifest_sha256")
    train, validation, classes, dim = _validate_train_validation(
        train_rows, validation_rows)
    class_index = {member: index for index, member in enumerate(classes)}
    x_train = np.asarray([row["embedding"] for row in train], dtype=np.float32)
    y_train = np.asarray([class_index[row["member"]] for row in train], dtype=np.int64)
    x_validation = np.asarray(
        [row["embedding"] for row in validation], dtype=np.float32)
    y_validation = np.asarray(
        [class_index[row["member"]] for row in validation], dtype=np.int64)

    import torch

    torch.manual_seed(HYPERPARAMETERS["seed"])
    torch.use_deterministic_algorithms(True)
    layer = torch.nn.Linear(dim, len(classes))
    optimizer = torch.optim.Adam(
        layer.parameters(), lr=HYPERPARAMETERS["learning_rate"])
    loss_function = torch.nn.CrossEntropyLoss()
    xt = torch.from_numpy(x_train)
    yt = torch.from_numpy(y_train)
    xv = torch.from_numpy(x_validation)
    yv = torch.from_numpy(y_validation)
    generator = torch.Generator(device="cpu")
    generator.manual_seed(HYPERPARAMETERS["seed"])
    best_loss = float("inf")
    best_epoch = 0
    best_weight = None
    best_bias = None
    no_improvement = 0
    stopped_epoch = HYPERPARAMETERS["max_epochs"]
    for epoch in range(1, HYPERPARAMETERS["max_epochs"] + 1):
        layer.train()
        order = torch.randperm(len(train), generator=generator)
        for start in range(0, len(train), HYPERPARAMETERS["batch_size"]):
            batch = order[start:start + HYPERPARAMETERS["batch_size"]]
            optimizer.zero_grad(set_to_none=True)
            loss = loss_function(layer(xt[batch]), yt[batch])
            loss.backward()
            optimizer.step()
        layer.eval()
        with torch.no_grad():
            validation_loss = float(loss_function(layer(xv), yv).item())
        if validation_loss < best_loss - 1e-12:
            best_loss = validation_loss
            best_epoch = epoch
            best_weight = layer.weight.detach().cpu().clone()
            best_bias = layer.bias.detach().cpu().clone()
            no_improvement = 0
        else:
            no_improvement += 1
            if no_improvement >= HYPERPARAMETERS["early_stopping_patience"]:
                stopped_epoch = epoch
                break
    if best_weight is None or best_bias is None:
        raise RuntimeError("neural head produced no finite validation checkpoint")
    train_counts = Counter(row["member"] for row in train)
    validation_counts = Counter(row["member"] for row in validation)
    return {
        "schema": SCHEMA,
        "status": STATUS,
        "backend": BACKEND,
        "classes": classes,
        "weight": [[float(value) for value in row] for row in best_weight.numpy()],
        "bias": [float(value) for value in best_bias.numpy()],
        "training_sha256": training_sha256,
        "split_manifest_sha256": split_manifest_sha256,
        "training": {
            "class_counts": {member: train_counts[member] for member in classes},
            "validation_class_counts": {
                member: validation_counts[member] for member in classes},
            "feature_dim": dim,
            "hyperparameters": dict(HYPERPARAMETERS),
            "input_scaling": "none; raw mean-pooled Wav2Vec embedding",
            "best_epoch": best_epoch,
            "stopped_epoch": stopped_epoch,
            "best_validation_loss": best_loss,
            "model_selection": "validation loss only; test split untouched",
        },
        "decision_contract": {
            "operation": "score one caller-supplied claimed member",
            "score_kind": SCORE_KIND,
            "silent_relabel": False,
            "production_consumers": 0,
        },
    }


def score_claim(head, embedding, claimed_member):
    """Score exactly one caller-supplied claim without returning another identity."""
    if not isinstance(head, dict) or head.get("schema") != SCHEMA:
        raise ValueError("neural-head schema mismatch")
    if head.get("status") != STATUS:
        raise ValueError("neural-head artifact is not a research candidate")
    classes = [str(value) for value in head.get("classes") or []]
    claimed_member = str(claimed_member or "").strip()
    if claimed_member not in classes:
        raise ValueError("claimed member is absent from the neural head")
    weight = np.asarray(head.get("weight"), dtype=np.float64)
    bias = np.asarray(head.get("bias"), dtype=np.float64).reshape(-1)
    if weight.ndim != 2 or weight.shape[0] != len(classes) or bias.size != len(classes):
        raise ValueError("neural-head coefficient dimensions are malformed")
    vector = _embedding(embedding, weight.shape[1])
    logits = weight.dot(vector) + bias
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
    """Score explicit genuine/impostor claims into the generic calibration-trials schema."""
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
        trial = {
            "trial_id": trial_id,
            "corpus": corpus,
            "claimed_member": claimed_member,
            "label": label,
            "score": scored["claimed_probability"],
            "margin_over_best_other": scored["margin_over_best_other"],
            "sex": str(raw.get("sex") or "UNKNOWN").strip().upper() or "UNKNOWN",
            "caption_mismatch": bool(raw.get("caption_mismatch")),
        }
        for field in ("true_member", "event_id", "window_id", "split"):
            if raw.get(field) is not None:
                trial[field] = str(raw[field])
        trials.append(trial)
    return {
        "schema": TRIALS_SCHEMA,
        "status": STATUS,
        "score_kind": SCORE_KIND,
        "label_provenance": label_provenance,
        "head_sha256": _artifact_sha256(head),
        "trials": trials,
    }
