# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
"""Deterministic speaker diarization for the audio-identity layer.

audio-identity design 2026-07-16 (user-directed build).  Research-only; the CLI writes
.json.candidate artifacts consumed by NOTHING until user-ratified promotion.

Method: energy VAD (simple_vad) plus gavel-like broadband spike rejection (reject_spikes)
produce a speech mask; fixed windows (window_sec / hop_sec) whose samples are all speech are
embedded with a pluggable backend (b5_audio_embed contract: embed(samples, sr) -> unit vector);
window embeddings are clustered with sklearn AgglomerativeClustering on a precomputed cosine-
distance matrix (average linkage; a distance_threshold cut when n_speakers is None); grid-adjacent
same-cluster windows merge into segments.  Everything is drop-never-repair:

* a window whose cosine distance to its own cluster centroid and to the nearest OTHER centroid
  differ by less than overlap_margin is flagged overlap=True and EXCLUDED from confident
  segments -- overlapped speech is never reassigned to a better-matching cluster.  Excluded
  regions are still reported as {"overlap": true, "cluster": -1} records so downstream
  attribution can refuse units that touch them.
* merged confident segments shorter than MIN_SEGMENT_SEC (audio_rules; overridable per call for
  synthetic batteries) are dropped, then IDENTITY_TURN_TRIM_SEC is trimmed from each boundary.

Determinism: no randomized initialization anywhere (agglomerative clustering, spectral-test
embeddings, and numpy reductions are all deterministic); the seed argument is accepted for API
stability and reserved for future backends that need one.  The cosine ACCEPTANCE threshold used
by verification is NOT here and never a code literal -- it lives in the calibrated thresholds
artifact (b5_audio_identity_thresholds.json.candidate); the distance_threshold below is only the
clustering cut, not an identity decision.

simple_vad is a deliberately dependency-light RMS gate.  A silero-VAD model may later replace it
behind the SAME interface (samples, sr -> boolean speech mask) once that download is separately
user-approved; nothing else in this module would change.
"""
import argparse

import numpy as np

from audio_rules import IDENTITY_TURN_TRIM_SEC, MIN_SEGMENT_SEC

OVERLAP_MARGIN = 0.05          # centroid-distance ambiguity below this -> overlap window
SPIKE_FRAME_SEC = 0.05         # RMS frame for spike detection ("within 50 ms")
SPIKE_DB_ABOVE_MEDIAN = 12.0   # gavel surrogate: >= 12 dB above the running median
SPIKE_MAX_RUN_FRAMES = 4       # longer elevated runs are speech onsets, not impulses
SPIKE_MEDIAN_HALF_FRAMES = 10  # running median half-window (~1 s at 50 ms frames)


def _mono(samples):
    samples = np.asarray(samples, dtype=np.float64)
    if samples.ndim == 2:
        samples = samples.mean(axis=1)
    return samples.reshape(-1)


def _frame_rms_db(samples, frame_len):
    """Per-frame RMS in dBFS; the trailing partial frame is measured over its own length."""
    n = samples.size
    n_frames = int(np.ceil(n / float(frame_len)))
    db = np.empty(n_frames, dtype=np.float64)
    for i in range(n_frames):
        frame = samples[i * frame_len:(i + 1) * frame_len]
        rms = float(np.sqrt(np.mean(frame * frame))) if frame.size else 0.0
        db[i] = 20.0 * np.log10(rms + 1e-12)
    return db


def _frames_to_sample_mask(frame_mask, frame_len, n):
    return np.repeat(frame_mask, frame_len)[:n]


def simple_vad(samples, sr, rms_window_sec=0.05, threshold_db=-35.0):
    """Boolean per-sample speech mask: frame RMS at or above threshold_db (dBFS) counts as speech.

    Interface contract (kept stable so a user-approved silero-VAD can replace this later):
    (samples, sr, ...) -> numpy bool array of len(samples), True = speech.
    """
    samples = _mono(samples)
    frame_len = max(1, int(round(rms_window_sec * sr)))
    db = _frame_rms_db(samples, frame_len)
    return _frames_to_sample_mask(db >= float(threshold_db), frame_len, samples.size)


def reject_spikes(samples, sr):
    """Boolean per-sample mask of gavel-like broadband RMS spikes (True = spike = non-speech).

    A 50 ms frame is a spike when its RMS is at least SPIKE_DB_ABOVE_MEDIAN dB above the running
    median of neighboring frames AND the elevated run lasts at most SPIKE_MAX_RUN_FRAMES frames
    (impulses, not speech onsets after silence).
    """
    samples = _mono(samples)
    frame_len = max(1, int(round(SPIKE_FRAME_SEC * sr)))
    db = _frame_rms_db(samples, frame_len)
    n_frames = db.size
    spikes = np.zeros(n_frames, dtype=bool)
    for i in range(n_frames):
        lo = max(0, i - SPIKE_MEDIAN_HALF_FRAMES)
        hi = min(n_frames, i + SPIKE_MEDIAN_HALF_FRAMES + 1)
        if db[i] >= float(np.median(db[lo:hi])) + SPIKE_DB_ABOVE_MEDIAN:
            spikes[i] = True
    # drop-never-repair applies to impulses only: long elevated runs are level shifts, keep them
    i = 0
    while i < n_frames:
        if spikes[i]:
            j = i
            while j < n_frames and spikes[j]:
                j += 1
            if j - i > SPIKE_MAX_RUN_FRAMES:
                spikes[i:j] = False
            i = j
        else:
            i += 1
    return _frames_to_sample_mask(spikes, frame_len, samples.size)


def _unit_rows(matrix):
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def _cluster_labels(distance, n_speakers, distance_threshold):
    from sklearn.cluster import AgglomerativeClustering
    kwargs = {"linkage": "average"}
    if n_speakers is None:
        kwargs.update(n_clusters=None, distance_threshold=float(distance_threshold))
    else:
        kwargs.update(n_clusters=min(int(n_speakers), distance.shape[0]))
    try:
        model = AgglomerativeClustering(metric="precomputed", **kwargs)
    except TypeError:  # sklearn < 1.2 spelled the parameter "affinity"
        model = AgglomerativeClustering(affinity="precomputed", **kwargs)
    return model.fit_predict(distance)


def _merge_grid_runs(windows):
    """Merge grid-ADJACENT windows (index step 1, same cluster) into (start, end, cluster) runs.

    Any dropped grid slot (VAD gap, spike, overlap window) splits the run, so an excluded
    window's span is never bridged into a confident segment.
    """
    runs = []
    for grid_idx, start, end, cluster in windows:
        if runs and grid_idx == runs[-1][0] + 1 and cluster == runs[-1][3]:
            runs[-1] = (grid_idx, runs[-1][1], end, cluster)
        else:
            runs.append((grid_idx, start, end, cluster))
    return [(start, end, cluster) for _, start, end, cluster in runs]


def diarize(samples, sr, backend, window_sec=1.5, hop_sec=0.75, n_speakers=None, seed=7,
            distance_threshold=0.35, min_segment_sec=None, overlap_margin=OVERLAP_MARGIN):
    """Diarize mono audio into segment records per the frozen contract.

    Returns a list of {"start": float, "end": float, "cluster": int, "overlap": bool}, sorted by
    start time.  overlap=False records are confident single-speaker segments (min-length gated,
    boundary-trimmed); overlap=True records (cluster == -1) mark ambiguous-speaker regions that
    were EXCLUDED from confident segments (drop-never-repair).  `seed` is accepted for API
    stability; no code path uses randomness.  `distance_threshold` is the agglomerative cut used
    only when n_speakers is None.  `min_segment_sec` defaults to audio_rules.MIN_SEGMENT_SEC.
    """
    del seed  # deterministic by construction; reserved for future backends
    samples = _mono(samples)
    if min_segment_sec is None:
        min_segment_sec = MIN_SEGMENT_SEC
    win = max(1, int(round(window_sec * sr)))
    hop = max(1, int(round(hop_sec * sr)))

    speech = simple_vad(samples, sr) & ~reject_spikes(samples, sr)

    grid = []  # (grid_idx, start_sample) for windows made entirely of speech samples
    embeddings = []
    grid_idx = -1
    for start in range(0, samples.size - win + 1, hop):
        grid_idx += 1
        if not speech[start:start + win].all():
            continue
        grid.append((grid_idx, start))
        embeddings.append(backend.embed(samples[start:start + win], sr))
    if not grid:
        return []

    matrix = _unit_rows(np.asarray(embeddings, dtype=np.float64))
    if len(grid) == 1:
        labels = np.zeros(1, dtype=int)
    else:
        similarity = np.clip(matrix @ matrix.T, -1.0, 1.0)
        distance = 1.0 - similarity
        np.fill_diagonal(distance, 0.0)
        distance = (distance + distance.T) / 2.0
        labels = _cluster_labels(distance, n_speakers, distance_threshold)

    unique = sorted(set(int(lab) for lab in labels))
    centroids = {}
    for lab in unique:
        centroid = matrix[labels == lab].mean(axis=0)
        norm = float(np.linalg.norm(centroid))
        centroids[lab] = centroid / norm if norm else centroid

    confident_windows, overlap_windows = [], []
    for row, (grid_idx, start) in enumerate(grid):
        lab = int(labels[row])
        start_sec = start / float(sr)
        end_sec = (start + win) / float(sr)
        is_overlap = False
        if len(unique) > 1:
            d_own = 1.0 - float(matrix[row] @ centroids[lab])
            d_other = min(1.0 - float(matrix[row] @ centroids[other])
                          for other in unique if other != lab)
            is_overlap = (d_other - d_own) < float(overlap_margin)
        record = (grid_idx, start_sec, end_sec, lab)
        (overlap_windows if is_overlap else confident_windows).append(record)

    segments = []
    for start_sec, end_sec, lab in _merge_grid_runs(confident_windows):
        if end_sec - start_sec < float(min_segment_sec):
            continue
        start_sec += IDENTITY_TURN_TRIM_SEC
        end_sec -= IDENTITY_TURN_TRIM_SEC
        if end_sec <= start_sec:
            continue
        segments.append({"start": round(start_sec, 6), "end": round(end_sec, 6),
                         "cluster": lab, "overlap": False})
    for start_sec, end_sec, _lab in _merge_grid_runs(
            [(g, s, e, 0) for g, s, e, _ in overlap_windows]):
        segments.append({"start": round(start_sec, 6), "end": round(end_sec, 6),
                         "cluster": -1, "overlap": True})
    segments.sort(key=lambda s: (s["start"], s["end"], s["cluster"]))
    return segments


def _main(argv=None):
    parser = argparse.ArgumentParser(
        description="Diarize one audio file (research candidate output; consumed by nothing).")
    parser.add_argument("--wav", required=True, help="input audio file readable by soundfile")
    parser.add_argument("--backend", default="spectral-test",
                        help="embedding backend name (see b5_audio_embed.get_backend)")
    parser.add_argument("--model-dir", default=None, help="local model dir for the ecapa backend")
    parser.add_argument("--model-manifest", default=None,
                        help="hash manifest covering every local model file")
    parser.add_argument("--out", required=True,
                        help="explicit output path; must end in .json.candidate")
    parser.add_argument("--window-sec", type=float, default=1.5)
    parser.add_argument("--hop-sec", type=float, default=0.75)
    parser.add_argument("--n-speakers", type=int, default=None)
    parser.add_argument("--distance-threshold", type=float, default=0.35)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    if not args.out.endswith(".json.candidate"):
        parser.error("--out must end in .json.candidate (research-only artifact)")

    import soundfile
    from members.b5_audio_embed import get_backend
    from moneyball_core import dump_atomic

    samples, sr = soundfile.read(args.wav, dtype="float64", always_2d=False)
    backend = get_backend(args.backend, args.model_dir, args.model_manifest)
    segments = diarize(samples, sr, backend, window_sec=args.window_sec, hop_sec=args.hop_sec,
                       n_speakers=args.n_speakers, seed=args.seed,
                       distance_threshold=args.distance_threshold)
    payload = {
        "schema": "b5-audio-diarize-v1",
        "status": "RESEARCH_CANDIDATE_NOT_CONSUMED",
        "backend": backend.name,
        "wav": args.wav,
        "params": {"window_sec": args.window_sec, "hop_sec": args.hop_sec,
                   "n_speakers": args.n_speakers, "distance_threshold": args.distance_threshold,
                   "seed": args.seed, "min_segment_sec": MIN_SEGMENT_SEC,
                   "trim_sec": IDENTITY_TURN_TRIM_SEC, "overlap_margin": OVERLAP_MARGIN},
        "segments": segments,
    }
    dump_atomic(payload, args.out)
    confident_count = sum(1 for s in segments if not s["overlap"])
    print("diarize: %d confident segments, %d overlap regions -> %s"
          % (confident_count, len(segments) - confident_count, args.out))


if __name__ == "__main__":
    _main()
