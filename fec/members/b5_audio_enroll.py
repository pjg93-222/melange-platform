# -*- coding: utf-8 -*-
"""Build the speaker-enrollment bank from a reviewed clip manifest (research candidate).

audio-identity design 2026-07-16 (user-directed build).  A member enrolls only with at least
ENROLL_MIN_CLIPS surviving clips drawn from at least ENROLL_MIN_EVENTS distinct events totalling at
least ENROLL_MIN_SEC seconds, and every surviving clip must pass a leave-one-clip-out integrity
gate: its embedding must reach theta_enroll cosine against the centroid of the member's REMAINING
clips, otherwise it is quarantined (worst offender first, one per round) and the member is dropped
if the survivors fall below the floors.  The bank embedding is the unit-normalized mean of the
surviving clip embeddings.  Everything is deterministic - no RNG anywhere.

Identity evidence strings are caller-supplied hard signals (chyron/roll-call regex hits, reviewer
confirmations); they are NEVER LLM labels.  This module only records them and hard-refuses empty
evidence.  theta_enroll is NEVER a code literal or free CLI parameter: the CLI loads it from the
passing b5_audio_identity_thresholds.json.candidate and records that artifact's SHA-256.

Output is a .json.candidate artifact consumed by NOTHING until user ratification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_rules import ENROLL_MIN_CLIPS, ENROLL_MIN_EVENTS, ENROLL_MIN_SEC  # noqa: E402
from members.b5_audio_embed import cosine, get_backend  # noqa: E402

SCHEMA = "b5-audio-enroll-bank-v1"
STATUS = "RESEARCH_CANDIDATE_NOT_CONSUMED"
REFUSE_CLIPS = "TOO_FEW_CLIPS"
REFUSE_EVENTS = "TOO_FEW_EVENTS"
REFUSE_SECONDS = "TOO_FEW_SECONDS"
REFUSE_LOO = "LOO_ATTRITION_BELOW_FLOORS"


def _unit(vector):
    vector = np.asarray(vector, dtype=np.float64)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


def _sha256_bytes(blob):
    return hashlib.sha256(blob).hexdigest()


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_clip(member, index, row, backend):
    """Normalize one manifest clip: samples + metadata + provenance hash. Fail-closed."""
    if not isinstance(row, dict):
        raise ValueError("%s clip %d: not an object" % (member, index))
    evidence = str(row.get("identity_evidence") or "").strip()
    if not evidence:
        raise ValueError(
            "%s clip %d: empty identity_evidence; enrollment requires a caller-supplied "
            "hard identity signal (regex/chyron/reviewer), never an LLM label" % (member, index))
    event_id = str(row.get("event_id") or "").strip()
    if not event_id:
        raise ValueError("%s clip %d: event_id missing" % (member, index))
    sr = int(row.get("sr") or 0)
    path = str(row.get("path") or "")
    if row.get("samples") is not None:
        samples = np.asarray(row["samples"], dtype=np.float64).reshape(-1)
        if sr <= 0:
            raise ValueError("%s clip %d: sr required with in-memory samples" % (member, index))
        sha = _sha256_bytes(samples.tobytes())
        if not path:
            path = "<memory:%s:%d>" % (member, index)
    elif path:
        import soundfile as sf  # lazy: only needed for path-based manifests
        samples, file_sr = sf.read(path, dtype="float64", always_2d=False)
        samples = np.asarray(samples, dtype=np.float64)
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        sr = sr or int(file_sr)
        if int(file_sr) != sr:
            raise ValueError("%s clip %d: manifest sr %d != file sr %d"
                             % (member, index, sr, int(file_sr)))
        sha = _sha256_file(path)
    else:
        raise ValueError("%s clip %d: needs 'samples' or 'path'" % (member, index))
    seconds = row.get("seconds")
    seconds = float(seconds) if seconds is not None else samples.size / float(sr)
    if seconds <= 0:
        raise ValueError("%s clip %d: non-positive seconds" % (member, index))
    embedding = backend.embed(samples, sr)
    meta = {"path": path, "event_id": event_id, "seconds": seconds,
            "identity_evidence": evidence, "sha256": sha}
    return meta, embedding


def _floors_ok(metas):
    if len(metas) < ENROLL_MIN_CLIPS:
        return REFUSE_CLIPS
    if len({meta["event_id"] for meta in metas}) < ENROLL_MIN_EVENTS:
        return REFUSE_EVENTS
    if sum(meta["seconds"] for meta in metas) < ENROLL_MIN_SEC:
        return REFUSE_SECONDS
    return None


def _loo_gate(metas, embeddings, theta_enroll):
    """Leave-one-clip-out integrity gate. Returns (survivor_indices, quarantined_rows)."""
    survivors = list(range(len(metas)))
    quarantined = []
    while len(survivors) >= ENROLL_MIN_CLIPS:
        worst_index, worst_cos = None, None
        for i in survivors:
            rest = [embeddings[j] for j in survivors if j != i]
            centroid = _unit(np.mean(np.asarray(rest), axis=0))
            c = cosine(embeddings[i], centroid)
            if c < theta_enroll and (worst_cos is None or c < worst_cos):
                worst_index, worst_cos = i, c
        if worst_index is None:
            break  # every survivor passes
        survivors.remove(worst_index)
        quarantined.append({
            "clip": metas[worst_index],
            "reason": "leave-one-out cosine %.4f < theta_enroll %.4f vs centroid of "
                      "remaining clips" % (worst_cos, theta_enroll),
        })
    return survivors, quarantined


def build_bank(clips_by_member, backend, theta_enroll, thresholds_sha256=None):
    """Build the enrollment bank. Deterministic; fail-closed on malformed clips."""
    theta_enroll = float(theta_enroll)
    members, refused, quarantined_all = {}, {}, []
    for member in sorted(clips_by_member):
        rows = clips_by_member[member] or []
        metas, embeddings = [], []
        for index, row in enumerate(rows):
            meta, embedding = _load_clip(member, index, row, backend)
            metas.append(meta)
            embeddings.append(embedding)
        reason = _floors_ok(metas)
        if reason is not None:
            refused[member] = reason
            continue
        survivors, quarantined = _loo_gate(metas, embeddings, theta_enroll)
        for row in quarantined:
            row["member"] = member
            quarantined_all.append(row)
        kept_metas = [metas[i] for i in survivors]
        reason = _floors_ok(kept_metas)
        if reason is not None:
            refused[member] = "%s (%s after quarantine)" % (REFUSE_LOO, reason)
            continue
        centroid = _unit(np.mean(np.asarray([embeddings[i] for i in survivors]), axis=0))
        members[member] = {
            "embedding": [float(v) for v in centroid],
            "clips": kept_metas,
            "total_sec": float(sum(meta["seconds"] for meta in kept_metas)),
            "events": len({meta["event_id"] for meta in kept_metas}),
            "ratified": [],
        }
    return {"schema": SCHEMA, "status": STATUS, "backend": backend.name,
            "theta_enroll": theta_enroll, "members": members,
            "thresholds_sha256": thresholds_sha256,
            "refused": refused, "quarantined": quarantined_all}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build the speaker-enrollment bank (research candidate; consumed by nothing).")
    parser.add_argument("--manifest", required=True,
                        help="JSON: {member: [{path|samples, sr, event_id, seconds, identity_evidence}]}")
    parser.add_argument("--backend", required=True, choices=["spectral-test", "wav2vec2", "ecapa"])
    parser.add_argument("--model-dir", default=None,
                        help="local model directory (required by real backends; never downloaded)")
    parser.add_argument("--model-manifest", default=None,
                        help="hash manifest covering every local model file; required by real backends")
    try:
        from paths import ROOT
    except ImportError:
        ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser.add_argument(
        "--thresholds",
        default=os.path.join(ROOT, "b5_audio_identity_thresholds.json.candidate"),
        help="passing calibrated thresholds artifact; enrollment refuses without it")
    parser.add_argument("--out", required=True,
                        help="explicit output path; must end in .json.candidate")
    args = parser.parse_args(argv)
    if not args.out.endswith(".json.candidate"):
        raise SystemExit("REFUSED: --out must end in .json.candidate (research artifact, "
                         "consumed by nothing until ratified): %s" % args.out)
    from moneyball_core import dump_atomic
    from members.b5_audio_calibrate import sha256_file
    from members.b5_audio_verify2 import load_thresholds
    thresholds = load_thresholds(args.thresholds)
    backend = get_backend(args.backend, args.model_dir, args.model_manifest)
    with open(args.manifest, encoding="utf-8") as handle:
        clips_by_member = json.load(handle)
    bank = build_bank(
        clips_by_member, backend, thresholds["theta_enroll"],
        thresholds_sha256=sha256_file(args.thresholds))
    dump_atomic(bank, args.out, indent=1)
    print("enroll bank: %d member(s) enrolled, %d refused, %d clip(s) quarantined"
          % (len(bank["members"]), len(bank["refused"]), len(bank["quarantined"])))
    print("wrote %s" % args.out)


if __name__ == "__main__":
    main()
