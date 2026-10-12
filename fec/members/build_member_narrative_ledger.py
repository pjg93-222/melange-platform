# -*- coding: utf-8 -*-
"""Build the evidence ledger used to audit and revise member approach briefs.

The ledger is deliberately display-aligned and fail-closed:

* Big Five estimates, qualified public-language reads, hearing behavior, and
  cross-party behavior remain separate constructs even when they disagree.
* Cross-party cooperation comes only from the share-based v2 measure.  The key
  is absent for uncovered members; the volume-entangled v1 value is never used.
* Speech ranks come only from the canonical descriptive-speech artifact and
  only when its >=3-interview-clip gate produced a percentile.  Floor ranks,
  pooled-sex pitch ranks, strain ranks, and locally recomputed ranks are barred.
* Percentiles for desk behavioral fields use the desk's exact
  ``bisect_left/(n-1)`` convention.

No API calls are made.  The output is a generated/ignored JSON artifact.
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fec.speech import longform_automated_authority as auto


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "data" / "members" / "member_narrative_evidence_v4.json"
SPEECH_CHANNELS = ("pause", "pace", "volume_dyn", "pitch_var")
ALL_SPEECH_CHANNELS = SPEECH_CHANNELS + ("strain", "f0_mean")
# Two audio layers exist per member and they are COMPLEMENTARY, never substitutes
# (Paul, 2026-09-07: "this data is not meant to override each other but be
# complementary").  They are measured on different scales and must never share a
# comparison column on any surface:
#   raw_clip_means  -- clip-weighted means over accepted descriptive clips
#   within_source_z -- z scores standardized WITHIN the legacy eGeMAPSv02 source
DESCRIPTIVE_AUDIO_SCALE = "raw_clip_means"
VOICEPRINT_AUDIO_SCALE = "within_source_z"
# House-floor audio is LABELED, never stripped (Paul's ruling 2026-09-07).  The
# pooled baseline is a clip-weighted mean, so the floor clip share IS the weight
# the floor pool carries in every channel value below: above 0.5 the floor pool,
# not the interview pool, determines the member's value on every channel, and at
# 1.0 the baseline is floor and nothing else.
FLOOR_SETTING = "house_floor"
FLOOR_DOMINANT_SHARE = 0.5
PUBLIC_SENSIBILITY_CATEGORIES = {
    "quotes_values", "faith", "inspiration", "formative", "family",
    "personal_routine", "books_media", "places_travel", "other",
}
LONGFORM_FEATURES = ROOT / "data/speech/member_longform_turn_features_v1.json"
LONGFORM_FEATURE_RECEIPT = ROOT / "data/speech/member_longform_turn_features_v1.receipt.json"
INTERACTION_DEFINITIONS = {
    "wpt_mean": "mean words per observed member turn",
    "wpt_p90": "90th percentile of observed member-turn word counts",
    "gini": "inequality of this member's turn word counts; not share of the conversation",
    "cutoff": "transcript-ending punctuation pattern; not an observed interruption",
    "burst": "runs of short member turns with other speakers omitted; not contested back-and-forth",
    "defense": "explicit floor-defense language",
    "discmark": "discourse-marker/filler language",
}


def load(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_longform_delivery(path: Path, receipt_path: Path) -> dict:
    """Admit measured delivery separately; never upgrade a candidate's authority."""
    from fec.speech import build_member_longform_turn_features as measurements

    if not path.exists() and not receipt_path.exists():
        return {}
    if not path.is_file() or not receipt_path.is_file():
        raise ValueError("longform delivery artifact and receipt must both exist")
    receipt = load(receipt_path)
    if (receipt.get("schema") != "member-longform-turn-features-receipt-v1"
            or receipt.get("status") != "PASS_MEASUREMENT_ONLY"):
        raise ValueError("longform delivery receipt is not a measurement receipt")
    if Path(str(receipt.get("artifact") or "")).resolve() != path.resolve():
        raise ValueError("longform delivery receipt path mismatch")
    if receipt.get("artifact_sha256") != sha256(path):
        raise ValueError("longform delivery artifact hash mismatch")
    inputs = receipt.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("longform delivery receipt lacks input lineage")
    for item in inputs:
        source = Path(str(item.get("path") or ""))
        if not source.is_file() or item.get("sha256") != sha256(source):
            raise ValueError("longform delivery input missing or changed: " + str(source))
    document = load(path)
    if (document.get("schema") != "member-longform-turn-features-v1"
            or document.get("status") != "MEASURED_DELIVERY_NO_AUTOMATIC_PROFILE_PROMOTION"):
        raise ValueError("longform delivery artifact contract mismatch")
    if document.get("inputs") != inputs:
        raise ValueError("longform delivery document/receipt input lineage mismatch")
    if (document.get("contract") != measurements.CONTRACT
            or receipt.get("contract") != measurements.CONTRACT
            or document.get("feature_columns") != measurements.FEATURE_COLUMNS
            or document.get("feature_definitions") != measurements.FEATURE_DEFINITIONS):
        raise ValueError("longform delivery measurement definitions or authority contract mismatch")
    turns = document.get("turns")
    if not isinstance(turns, list):
        raise ValueError("longform delivery turns must be a list")
    seen_turns = set()
    for turn in turns:
        if not isinstance(turn, dict):
            raise ValueError("longform delivery invalid turn row")
        digest = measurements.row_hash({key: value for key, value in turn.items() if key != "row_sha256"})
        if not turn.get("row_sha256") or turn["row_sha256"] != digest:
            raise ValueError("longform delivery turn row hash mismatch")
        turn_id = turn.get("turn_id")
        if (not isinstance(turn_id, str) or not turn_id or turn_id in seen_turns
                or any(not isinstance(turn.get(key), str) or not turn[key]
                       for key in ("member", "source_id", "source_audio_sha256"))):
            raise ValueError("longform delivery missing or duplicate turn/source identity")
        seen_turns.add(turn_id)
        if any(type(turn.get(key)) is not bool for key in (
            "profile_measurement_eligible", "baseline_measurement_eligible", "quality_hold"
        )):
            raise ValueError("longform delivery authority flags must be explicit booleans")
        values = turn.get("features")
        if (not isinstance(values, dict) or set(values) != set(measurements.FEATURE_COLUMNS)
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) for value in values.values())):
            raise ValueError("longform delivery invalid feature values")
        duration = turn.get("measured_duration_seconds")
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(duration) or duration <= 0):
            raise ValueError("longform delivery invalid measured duration")
        qualified = turn.get("profile_measurement_eligible") is True
        if qualified and turn.get("authority") not in {"HASH_BOUND_NAMED_HUMAN_REVIEW", auto.AUTHORITY}:
            raise ValueError("candidate longform delivery cannot gain profile authority")
        if qualified:
            coverage = turn.get("turn_coverage") or {}
            reviewer_field, date_field = auto.provenance_fields(turn.get("authority") == auto.AUTHORITY)
            if (turn["quality_hold"] or not str(turn.get(reviewer_field) or "").strip()
                    or not turn.get("candidate_turn_binding_sha256")
                    or coverage.get("turn_binding_sha256") != turn["candidate_turn_binding_sha256"]
                    or coverage.get("continuity_gate_passed") is not True
                    or coverage.get("interior_coverage_complete") is not True):
                raise ValueError("longform delivery qualified turn lacks human/coverage provenance")
            try:
                reviewed_at = dt.datetime.fromisoformat(str(turn.get(date_field) or "").replace("Z", "+00:00"))
                if reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
                    raise ValueError("missing review timezone")
            except ValueError as exc:
                raise ValueError("longform delivery qualified turn lacks valid review date") from exc
        if turn.get("baseline_measurement_eligible") is True and (
            not qualified or turn.get("source_era") not in {
                "PRE_CURRENT_LEADERSHIP_ROLE", "NOT_CURRENT_FORMAL_LEADERSHIP"
            }
        ):
            raise ValueError("longform baseline lacks qualified source-era evidence")
    counts = {
        "turns": len(turns), "members": len({turn["member"] for turn in turns}),
        "sources": len({(turn["member"], turn["source_id"], turn["source_audio_sha256"]) for turn in turns}),
        "minutes": round(sum(turn["measured_duration_seconds"] for turn in turns) / 60, 6),
        "qualified_turns": sum(turn["profile_measurement_eligible"] for turn in turns),
        "baseline_eligible_turns": sum(turn["baseline_measurement_eligible"] for turn in turns),
        "candidate_only_turns": sum(not turn["profile_measurement_eligible"] for turn in turns),
        "quality_held_turns": sum(turn["quality_hold"] for turn in turns),
    }
    if document.get("counts") != counts or receipt.get("counts") != counts:
        raise ValueError("longform delivery document/receipt counts mismatch")
    if document.get("members") != measurements.aggregate(turns):
        raise ValueError("longform delivery member aggregates disagree with validated turns")
    return document


def finite(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def longform_semantic_authority(turn: dict, measured_turns: list[dict]) -> dict:
    """Bridge semantic authority only through an exact, qualified measured row.

The historical status string is retained as provenance, never interpreted as
new named human review. The measured artifact has already passed its separate
receipt/physical-input/authority checks before this bridge is called.
"""
    candidates = [item for item in measured_turns if all(item.get(key) == turn.get(key)
                  for key in ("member", "source_id", "turn_id"))]
    limits = []
    feature = candidates[0] if len(candidates) == 1 else {}
    authority = str(feature.get("authority") or "MEASURED_REVIEW_AUTHORITY_UNRESOLVED")
    if len(candidates) != 1:
        limits.append("missing or ambiguous exact measured-turn linkage")
    else:
        if (feature.get("profile_measurement_eligible") is not True
                or feature.get("authority") not in {"HASH_BOUND_NAMED_HUMAN_REVIEW", auto.AUTHORITY}
                or feature.get("quality_hold") is not False):
            limits.extend(feature.get("qualification_limits") or ["measured turn is candidate-only or quality-held"])
        for key in ("source_audio_sha256", "source_transcript_sha256", "turn_audio_sha256"):
            if not turn.get(key) or feature.get(key) != turn.get(key):
                limits.append("materialized/measured hash mismatch: " + key)
        for key in ("start_sec", "end_sec"):
            if finite(turn.get(key)) is None or finite(turn.get(key)) != finite(feature.get(key)):
                limits.append("materialized/measured boundary mismatch: " + key)
        payload = json.dumps({key: value for key, value in turn.items() if key != "row_sha256"},
                             ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        original_sha = hashlib.sha256(payload).hexdigest()
        if (not turn.get("row_sha256") or turn["row_sha256"] != original_sha
                or feature.get("input_turn_row_sha256") != original_sha):
            limits.append("materialized semantic row is not the exact measured input row")
        for key in (*auto.provenance_fields(feature.get("authority") == auto.AUTHORITY), "candidate_turn_binding_sha256"):
            if not turn.get(key) or feature.get(key) != turn.get(key):
                limits.append("named review/turn binding mismatch: " + key)
        coverage = turn.get("turn_coverage") or {}
        if (not turn.get("review_row_sha256") or feature.get("turn_coverage") != coverage
                or coverage.get("continuity_gate_passed") is not True
                or coverage.get("interior_coverage_complete") is not True
                or coverage.get("turn_binding_sha256") != turn.get("candidate_turn_binding_sha256")):
            limits.append("complete-turn review/coverage provenance is unresolved")
        if feature.get("source_era") != turn.get("source_era"):
            limits.append("materialized/measured source era mismatch")
    qualified = not limits
    return {
        "semantic_authority": (authority if qualified else
                               "QUALIFIED_FEATURE_LINK_NOT_ADMITTED" if authority in {"HASH_BOUND_NAMED_HUMAN_REVIEW", auto.AUTHORITY} else authority),
        "measured_feature_authority": feature.get("authority"),
        "semantic_review_qualified": qualified,
        "baseline_semantic_eligible": qualified and feature.get("baseline_measurement_eligible") is True,
        "semantic_authority_limits": limits,
        "measured_feature_row_sha256": feature.get("row_sha256"),
        "semantic_claim_status": ("AUTOMATED_VERIFIED_SOURCE_BOUND" if authority == auto.AUTHORITY else "REVIEWED_SOURCE_BOUND") if qualified else "PROVISIONAL_NOT_HUMAN_QUALIFIED",
    }


def partition_longform_semantics(turns: list[dict]) -> dict:
    """Do not mix retained historical claims with qualified reviewed findings."""
    reviewed, baseline, role_conditioned, provisional = [], [], [], []
    for turn in turns:
        for finding in turn.get("semantic_findings") or []:
            if not isinstance(finding, dict):
                continue
            item = {**finding, "turn_id": turn.get("turn_id"), "source_id": turn.get("source_id"),
                    "source_era": turn.get("source_era"), "semantic_authority": turn.get("semantic_authority"),
                    "semantic_claim_status": turn.get("semantic_claim_status"),
                    "measured_feature_row_sha256": turn.get("measured_feature_row_sha256")}
            if turn.get("semantic_review_qualified") is True:
                reviewed.append(item)
                if turn.get("baseline_semantic_eligible") is True:
                    baseline.append(item)
                if turn.get("source_era") == "CURRENT_ROLE_CONDITIONED":
                    role_conditioned.append(item)
            else:
                provisional.append({**item, "human_review_qualified": False,
                                    "baseline_semantic_eligible": False,
                                    "qualification_limits": turn.get("semantic_authority_limits") or []})
    return {"reviewed_semantic_findings": reviewed, "baseline_reviewed_semantic_findings": baseline,
            "role_conditioned_reviewed_semantic_findings": role_conditioned,
            "provisional_semantic_findings": provisional}


def desk_rank(values):
    """Return the exact percentile convention used by make_member_desk.py."""
    pool = sorted(value for value in (finite(v) for v in values) if value is not None)
    denominator = max(1, len(pool) - 1)

    def rank(value):
        value = finite(value)
        if value is None or not pool:
            return None
        return round(bisect.bisect_left(pool, value) / denominator, 3)

    rank.n = len(pool)
    return rank


def band(percentile, *, definition=None, scope=None, polarity=None):
    percentile = finite(percentile)
    if percentile is None:
        return None
    if percentile >= 0.90:
        label, neutral = "among the highest in the chamber", False
    elif percentile >= 0.78:
        label, neutral = "well above most", False
    elif percentile >= 0.58:
        label, neutral = "above most", False
    elif percentile >= 0.42:
        label, neutral = "middling", True
    elif percentile >= 0.22:
        label, neutral = "below most", False
    elif percentile >= 0.10:
        label, neutral = "well below most", False
    else:
        label, neutral = "among the lowest in the chamber", False
    result = {"pct": round(percentile, 3), "label": label, "neutral": neutral}
    if definition:
        result["definition"] = definition
    if scope:
        result["scope"] = scope
    if polarity:
        result["polarity"] = polarity
    return result


def pressure_note(multiplier):
    multiplier = finite(multiplier)
    if multiplier is None:
        return None
    if 0.97 <= multiplier <= 1.03:
        return "pressure returns about the same as for the typical covered member"
    if multiplier < 1:
        return "pressure returns about %d%% less than for the typical covered member; a discount, not immunity" % round((1 - multiplier) * 100)
    return "pressure returns about %d%% more than for the typical covered member" % round((multiplier - 1) * 100)


def coverage_strength(clips, episodes):
    clips = int(clips or 0)
    episodes = int(episodes or 0)
    if clips >= 8 and episodes >= 3:
        return "strong_repeated"
    if clips >= 4 and episodes >= 2:
        return "moderate_repeated"
    if clips >= 2:
        return "thin_descriptive"
    return "single_observation"


def floor_composition(settings, total_clips):
    """How much of the pooled baseline is House-floor audio.

    Floor microphones and the floor speaking format differ materially from
    interview settings, so a floor-heavy baseline is comparable to other floor
    recordings and not to interview delivery.  The producer LABELS that rather
    than removing floor clips, so the pooled value the surfaces already show
    keeps its meaning and gains its caveat.
    """
    total_clips = int(total_clips or 0)
    floor_clips = int((settings.get(FLOOR_SETTING) or {}).get("clips") or 0)
    share = (floor_clips / total_clips) if total_clips else 0.0
    if share >= 1.0:
        composition = "floor_only"
    elif share > FLOOR_DOMINANT_SHARE:
        composition = "floor_dominant"
    elif floor_clips:
        composition = "floor_present"
    else:
        composition = "no_floor"
    return {
        "floor_clips": floor_clips,
        "total_clips": total_clips,
        "share": round(share, 6),
        "composition": composition,
        "scope": (
            "clip share of this pooled baseline drawn from House-floor recordings; floor "
            "microphones and speaking format differ materially from interview settings, so "
            "floor-heavy values compare to other floor recordings and not to interview delivery"
        ),
    }


def general_audio_from_settings(speech_record):
    """Combine every accepted setting block into one clip-weighted baseline."""
    settings = speech_record.get("by_setting") or {}
    total_clips = sum(int(block.get("clips") or 0) for block in settings.values())
    if not total_clips:
        return None
    episodes = sum(int(block.get("episodes") or 0) for block in settings.values())
    channels = {}
    for channel in ALL_SPEECH_CHANNELS:
        weighted = [(finite(block.get(channel + "_mean")), int(block.get("clips") or 0))
                    for block in settings.values()]
        weighted = [(value, count) for value, count in weighted if value is not None and count > 0]
        if weighted:
            channels[channel] = round(sum(value * count for value, count in weighted) /
                                      sum(count for _value, count in weighted), 6)
    if not channels:
        return None
    return {
        "source": "speech_descriptive_v1 accepted clips across every recorded setting",
        "clips": total_clips,
        "episodes": episodes,
        "minutes": finite((speech_record.get("coverage") or {}).get("minutes")),
        "coverage_strength": coverage_strength(total_clips, episodes),
        "channels": channels,
        # Additive fields (2026-09-07).  Every key above keeps its exact previous
        # value; these two say what scale the values are on and how much of the
        # pool is floor audio, so a renderer can never present this layer beside
        # the within-source z layer as if the two were the same measurement.
        "layer": "descriptive_clip_baseline",
        "scale": DESCRIPTIVE_AUDIO_SCALE,
        "floor_composition": floor_composition(settings, total_clips),
    }


def cache_audio_profile(name, cache, verified_members):
    """The verified-voiceprint layer: duration-weighted eGeMAPSv02 over identity-verified audio.

    This is a SECOND, INDEPENDENT layer, not a fallback (Paul, 2026-09-07).  It is
    emitted for every member who has it, including the members who also carry the
    descriptive clip layer.  Its values are z scores standardized within the legacy
    cache's own source and are therefore NOT comparable to the descriptive layer's
    raw clip means -- ``scale`` says so on the record, and every consumer must keep
    the two in separate blocks.
    """
    if name not in verified_members or name not in set(cache.get("members") or []):
        return None
    feature_names = cache.get("feature_names") or []
    values = (cache.get("z") or {}).get(name)
    if not isinstance(values, list) or len(values) != len(feature_names):
        return None
    feature_map = dict(zip(feature_names, values))
    aliases = {
        "pause": "MeanUnvoicedSegmentLength",
        "pace": "VoicedSegmentsPerSec",
        "volume_dyn": "loudness_sma3_stddevNorm",
        "pitch_var": "F0semitoneFrom27.5Hz_sma3nz_stddevNorm",
        "strain": "jitterLocal_sma3nz_amean",
        "f0_mean": "F0semitoneFrom27.5Hz_sma3nz_amean",
    }
    channels = {alias: finite(feature_map.get(feature)) for alias, feature in aliases.items()}
    channels = {alias: value for alias, value in channels.items() if value is not None}
    if not channels:
        return None
    seconds = finite((cache.get("seconds") or {}).get(name))
    return {
        "source": "verified general audio profile with legacy duration-weighted eGeMAPSv02 cache",
        "seconds": seconds,
        "minutes": round(seconds / 60.0, 3) if seconds is not None else None,
        "coverage_strength": "general_profile" if seconds and seconds >= 180 else "thin_descriptive",
        "standardized_channels": channels,
        "scope": "general all-accepted-audio baseline; source-standardized values, not setting-specific raw measurements",
        "layer": "verified_voiceprint_baseline",
        "scale": VOICEPRINT_AUDIO_SCALE,
        "comparability": (
            "z scores standardized within this cache's own source; comparable across the "
            "members measured from it and NOT comparable to the clip-based raw means shown "
            "for the descriptive layer"
        ),
        "clip_segmented": False,
    }


def leadership_source_era(member, published, leadership_roles):
    """Classify verified interview evidence relative to the current role."""
    role = leadership_roles.get(member)
    if not role:
        return "NOT_CURRENT_FORMAL_LEADERSHIP"
    if not published:
        return "UNKNOWN_REQUIRES_REVIEW"
    try:
        observed = dt.datetime.fromisoformat(str(published).replace("Z", "+00:00"))
        cutoff = dt.datetime.fromisoformat(
            str(role["current_role_assumed_on"]).replace("Z", "+00:00")
        )
    except (TypeError, ValueError, KeyError):
        return "UNKNOWN_REQUIRES_REVIEW"
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=dt.timezone.utc)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=dt.timezone.utc)
    return (
        "PRE_CURRENT_LEADERSHIP_ROLE"
        if observed < cutoff else "CURRENT_ROLE_CONDITIONED"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--longform-features", type=Path, default=LONGFORM_FEATURES)
    parser.add_argument("--longform-feature-receipt", type=Path, default=LONGFORM_FEATURE_RECEIPT)
    parser.add_argument("--roster-turns", type=Path)
    parser.add_argument("--roster-turn-receipt", type=Path)
    args = parser.parse_args()

    paths = {
        "basis": ROOT / "data" / "members" / "profile_basis_v2.json",
        "desk": ROOT / "member_desk_bundle.json",
        "congestion": ROOT / "data" / "engine" / "congestion.json",
        "speech": ROOT / "data" / "speech" / "speech_descriptive_v1.json",
        "member_interviews": ROOT / "data" / "speech" / "member_interview_descriptive_v1.json",
        "member_interview_turns": ROOT / "data" / "speech" / "member_interview_long_turns_v1.jsonl",
        "member_interview_turn_receipt": ROOT / "data" / "speech" / "member_interview_long_turns_v1.receipt.json",
        "leadership_roles": ROOT / "data" / "speech" / "member_leadership_role_cutoffs_v1.json",
        "leadership_prerole_sources": ROOT / "data" / "speech" / "member_leadership_prerole_reviewed_sources_v1.jsonl",
        "leadership_prerole_acquisition": ROOT / "data" / "speech" / "member_leadership_prerole_acquisition_v1.jsonl",
        "leadership_prerole_turns": ROOT / "data" / "speech" / "member_leadership_prerole_long_turns_v1.jsonl",
        "leadership_prerole_turn_receipt": ROOT / "data" / "speech" / "member_leadership_prerole_long_turns_v1.receipt.json",
        "speech_census": ROOT / "data" / "speech" / "congress_audio_profile_census_full_v1.jsonl",
        "audio_cache": ROOT / "data" / "members" / "b5_audio_cache.json",
        "interaction": ROOT / "data" / "lobbying" / "interaction_features.json",
        "personal_texture": ROOT / "data" / "members" / "member_personal_texture.json",
        "hearing": ROOT / "data" / "stance" / "hearing_antagonism.json",
        "cooperation": ROOT / "coop_v2_candidate.json",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise SystemExit("missing required input(s): " + ", ".join(missing))
    optional_paths = {
        "roster_longform_turns": args.roster_turns or ROOT / "data" / "speech" / "member_longform_reviewed_turns_v1.jsonl",
        "roster_longform_turn_receipt": args.roster_turn_receipt or ROOT / "data" / "speech" / "member_longform_reviewed_turns_v1.receipt.json",
    }
    if any(path.is_file() for path in optional_paths.values()) and not all(
        path.is_file() for path in optional_paths.values()
    ):
        raise SystemExit("roster-wide reviewed turns and receipt must appear together")
    if bool(args.roster_turns) != bool(args.roster_turn_receipt):
        parser.error("roster turns and receipt must be supplied together")
    if args.roster_turns and not all(path.is_file() for path in optional_paths.values()):
        parser.error("explicit roster turn inputs are missing")
    longform_delivery_doc = load_longform_delivery(args.longform_features, args.longform_feature_receipt)
    longform_delivery = longform_delivery_doc.get("members") or {}
    if longform_delivery_doc:
        optional_paths.update({
            "longform_delivery": args.longform_features,
            "longform_delivery_receipt": args.longform_feature_receipt,
        })

    basis_doc = load(paths["basis"])
    if basis_doc.get("schema") != "profile-basis-v2" or basis_doc.get("status") != "ACTIVE_CONSTRUCT_GUARDED":
        raise SystemExit("profile_basis_v2.json is not the active construct-guarded artifact")
    basis = basis_doc.get("members") or {}
    desk_doc = load(paths["desk"])
    desk = {row["name"]: row for row in desk_doc.get("members") or []}
    congestion = {row["member"]: row for row in load(paths["congestion"])}
    speech = (load(paths["speech"]).get("members") or {})
    interview_doc = load(paths["member_interviews"])
    if (interview_doc.get("schema") != "member-interview-descriptive-v1" or
            interview_doc.get("status") != "DISPLAY_ONLY_NO_ENGINE_WIRING"):
        raise SystemExit("member interview artifact is not the display-only verified artifact")
    member_interviews = interview_doc.get("members") or {}
    leadership_roles = (load(paths["leadership_roles"]).get("members") or {})
    leadership_prerole_sources = {
        row["member"]: row for row in load_jsonl(paths["leadership_prerole_sources"])
    }
    leadership_prerole_acquisition = {
        row["member"]: row for row in load_jsonl(paths["leadership_prerole_acquisition"])
    }
    if set(leadership_prerole_sources) != set(leadership_roles):
        raise SystemExit("leadership pre-role source ledger does not match the current leadership roster")
    interview_sources_by_member = {}
    interview_sources_by_id = {}
    for row in interview_doc.get("verified_sources") or []:
        source_record = {
            "source_id": row.get("source_id"),
            "title": row.get("title"),
            "channel": row.get("channel"),
            "published": row.get("published"),
            "url": row.get("url"),
            "requested_url": row.get("requested_url"),
            "discovery": row.get("discovery"),
            "accepted_windows": row.get("accepted_windows"),
            "identity_method": row.get("identity_method"),
            "source_era": leadership_source_era(
                str(row["member"]), row.get("published"), leadership_roles
            ),
        }
        interview_sources_by_member.setdefault(str(row["member"]), []).append(source_record)
        interview_sources_by_id[(str(row["member"]), str(row.get("source_id")))] = source_record

    leadership_turn_receipt = load(paths["leadership_prerole_turn_receipt"])
    if (leadership_turn_receipt.get("schema") != "member-leadership-prerole-long-turn-receipt-v1" or
            leadership_turn_receipt.get("status") != "PASS"):
        raise SystemExit("leadership pre-role turn receipt is not PASS")
    if leadership_turn_receipt.get("artifact_sha256") != sha256(paths["leadership_prerole_turns"]):
        raise SystemExit("leadership pre-role turn receipt is stale")
    leadership_turn_rows = load_jsonl(paths["leadership_prerole_turns"])
    for row in leadership_turn_rows:
        if (row.get("schema") != "member-interview-long-turn-v1" or
                row.get("status") != "HUMAN_BOUNDARY_REVIEWED_SOURCE_IDENTITY_VERIFIED" or
                row.get("source_era") != "PRE_CURRENT_LEADERSHIP_ROLE"):
            raise SystemExit("leadership pre-role turn artifact contains an inadmissible row")
        source_record = {
            "source_id": row.get("source_id"),
            "title": row.get("title"),
            "channel": row.get("channel"),
            "published": row.get("published"),
            "recorded_at": row.get("recorded_at"),
            "url": row.get("url"),
            "accepted_windows": row.get("source_identity_anchor_windows"),
            "identity_method": row.get("source_identity_method"),
            "source_era": row.get("source_era"),
        }
        member = str(row["member"])
        source_id = str(row.get("source_id"))
        if (member, source_id) not in interview_sources_by_id:
            interview_sources_by_member.setdefault(member, []).append(source_record)
            interview_sources_by_id[(member, source_id)] = source_record
    interview_turn_receipt = load(paths["member_interview_turn_receipt"])
    if (interview_turn_receipt.get("schema") != "member-interview-long-turn-receipt-v1" or
            interview_turn_receipt.get("status") != "PASS"):
        raise SystemExit("member interview turn receipt is not PASS")
    if interview_turn_receipt.get("artifact_sha256") != sha256(paths["member_interview_turns"]):
        raise SystemExit("member interview turn receipt is stale")
    roster_turn_rows = []
    if all(path.is_file() for path in optional_paths.values()):
        roster_turn_receipt = load(optional_paths["roster_longform_turn_receipt"])
        if (roster_turn_receipt.get("schema") != "member-longform-reviewed-turn-receipt-v1" or
                roster_turn_receipt.get("status") != "PASS"):
            raise SystemExit("roster-wide reviewed turn receipt is not PASS")
        if roster_turn_receipt.get("artifact_sha256") != sha256(optional_paths["roster_longform_turns"]):
            raise SystemExit("roster-wide reviewed turn receipt is stale")
        roster_turn_rows = load_jsonl(optional_paths["roster_longform_turns"])
        for row in roster_turn_rows:
            if (row.get("schema") != "member-interview-long-turn-v1" or
                    row.get("status") not in {"HUMAN_BOUNDARY_REVIEWED_SOURCE_IDENTITY_VERIFIED", auto.STATUS}):
                raise SystemExit("roster-wide reviewed turn artifact contains an inadmissible row")
            member = str(row["member"])
            if member in leadership_roles and row.get("source_era") != "PRE_CURRENT_LEADERSHIP_ROLE":
                raise SystemExit("current leader roster-wide turn is not pre-current-role")
            source_record = {
                "source_id": row.get("source_id"),
                "title": row.get("title"),
                "channel": row.get("channel"),
                "published": row.get("published"),
                "recorded_at": row.get("recorded_at"),
                "url": row.get("url"),
                "accepted_windows": row.get("source_identity_anchor_windows"),
                "identity_method": row.get("source_identity_method"),
                "source_era": row.get("source_era"),
            }
            source_id = str(row.get("source_id"))
            if (member, source_id) not in interview_sources_by_id:
                interview_sources_by_member.setdefault(member, []).append(source_record)
                interview_sources_by_id[(member, source_id)] = source_record
    interview_turns_by_member = {}
    turn_rows = load_jsonl(paths["member_interview_turns"]) + leadership_turn_rows + roster_turn_rows
    seen_turn_evidence = set()
    for row in turn_rows:
        if (row.get("schema") != "member-interview-long-turn-v1" or
                row.get("status") not in {"HUMAN_BOUNDARY_REVIEWED_SOURCE_IDENTITY_VERIFIED", auto.STATUS}):
            raise SystemExit("member interview turn artifact contains an inadmissible row")
        evidence_key = (
            str(row.get("member")), str(row.get("source_id")),
            round(float(row.get("start_sec") or 0.0), 3),
            round(float(row.get("end_sec") or 0.0), 3),
        )
        if evidence_key in seen_turn_evidence:
            continue
        seen_turn_evidence.add(evidence_key)
        source_record = interview_sources_by_id.get(
            (str(row["member"]), str(row.get("source_id")))
        ) or {}
        interview_turns_by_member.setdefault(str(row["member"]), []).append({
            **longform_semantic_authority(row, longform_delivery_doc.get("turns") or []),
            "member": row.get("member"),
            "turn_id": row.get("turn_id"),
            "source_id": row.get("source_id"),
            "title": row.get("title"),
            "channel": row.get("channel"),
            "url": row.get("url"),
            "start_sec": row.get("start_sec"),
            "end_sec": row.get("end_sec"),
            "duration_seconds": row.get("duration_seconds"),
            "word_count": row.get("word_count"),
            "transcript_text": row.get("transcript_text"),
            "boundary_review": row.get("boundary_review"),
            "semantic_findings": row.get("semantic_findings") or [],
            "source_identity_method": row.get("source_identity_method"),
            "identity_anchor_windows_inside_turn": row.get("identity_anchor_windows_inside_turn"),
            "turn_audio_wav": row.get("turn_audio_wav"),
            "turn_audio_sha256": row.get("turn_audio_sha256"),
            "source_audio_sha256": row.get("source_audio_sha256"),
            "source_transcript_sha256": row.get("source_transcript_sha256"),
            "materialized_turn_row_sha256": row.get("row_sha256"),
            "source_era": row.get("source_era") or source_record.get("source_era", "UNKNOWN_REQUIRES_REVIEW"),
        })
    speech_census = {row["member"]: row for row in load_jsonl(paths["speech_census"])}
    verified_audio_members = {name for name, row in speech_census.items()
                              if row.get("verified_audio_profile") is True}
    audio_cache = load(paths["audio_cache"])
    interaction = (load(paths["interaction"]).get("members") or {})
    personal_texture = (load(paths["personal_texture"]).get("members") or {})
    hearing = (load(paths["hearing"]).get("members") or {})
    cooperation = (load(paths["cooperation"]).get("members") or {})

    if set(basis) != set(desk) or set(basis) != set(congestion):
        raise SystemExit("basis, desk bundle, and congestion roster keys do not match exactly")

    def facet_values(key):
        return [((row.get("personality_facets") or {}).get(key) or {}).get("z") for row in basis.values()]

    qualified_keys = {
        "openness_issue_breadth": "Openness_text_issue_breadth",
        "conscientiousness_diligence": "Conscientiousness_text_diligence",
        "agreeableness_low_antagonism": "Agreeableness_low_antagonism",
        "neuroticism_affective_content": "Neuroticism_affective_content",
    }
    qualified_ranks = {out_key: desk_rank(facet_values(source_key)) for out_key, source_key in qualified_keys.items()}

    behavior_ranks = {
        "cross_party_cooperation": desk_rank((cooperation.get(name) or {}).get("cooperation_v2_z") for name in basis),
        "policy_breadth": desk_rank((row.get("behavioral_record") or {}).get("breadth_z") for row in basis.values()),
        "legislative_diligence": desk_rank((row.get("behavioral_record") or {}).get("diligence_z") for row in basis.values()),
        "conversational_dominance": desk_rank((row.get("observed_conversation") or {}).get("dominance_z") for row in basis.values()),
        "words_per_turn": desk_rank((row.get("observed_conversation") or {}).get("expressiveness_z") for row in basis.values()),
    }
    hearing_rank = desk_rank(row.get("antagonism_z") for row in hearing.values())
    interaction_ranks = {
        key: desk_rank(row.get(key) for row in interaction.values()
                       if int(row.get("turns") or 0) >= 30)
        for key in ("wpt_mean", "wpt_p90", "gini", "cutoff", "burst", "defense", "discmark")
    }
    general_audio = {name: general_audio_from_settings(row) for name, row in speech.items()}
    general_audio = {name: row for name, row in general_audio.items() if row}
    general_audio_ranks = {
        channel: desk_rank((row.get("channels") or {}).get(channel) for row in general_audio.values())
        for channel in SPEECH_CHANNELS
    }

    records = {}
    general_audio_count = 0
    descriptive_audio_count = 0
    voiceprint_audio_count = 0
    both_audio_layer_count = 0
    voiceprint_only_count = 0
    no_audio_layer_count = 0
    floor_only_count = 0
    floor_dominant_count = 0
    setting_audio_count = 0
    repeated_defensive_count = 0
    public_sensibility_count = 0
    topic_association_count = 0
    longform_interview_count = 0
    longform_turn_count = 0
    for name, source in sorted(basis.items()):
        profile_facets = source.get("personality_facets") or {}
        traits = {}
        for trait in ("Openness", "Conscientiousness", "Extraversion", "Agreeableness", "Neuroticism"):
            node = profile_facets.get(trait) or {}
            traits[trait] = {
                "band": band(node.get("percentile")),
                "status": node.get("status"),
                "scope": node.get("scope"),
                "construct_boundary": "broad Big Five estimate; independent of similarly named behavior and public-language facets",
            }

        qualified = {}
        for out_key, source_key in qualified_keys.items():
            node = profile_facets.get(source_key) or {}
            qualified[out_key] = {
                "band": band(qualified_ranks[out_key](node.get("z"))),
                "scope": node.get("scope"),
                "construct_boundary": "qualified public-language read; not a substitute for the broad Big Five trait or observed behavior",
            }

        behavioral = source.get("behavioral_record") or {}
        observed = source.get("observed_conversation") or {}
        behavior = {
            "policy_breadth": band(
                behavior_ranks["policy_breadth"](behavioral.get("breadth_z")),
                definition="number of distinct issue sections with any legislative role",
                scope="tracked bills archive; observed legislative portfolio, not private curiosity or broad Openness",
            ),
            "legislative_diligence": band(
                behavior_ranks["legislative_diligence"](behavioral.get("diligence_z")),
                definition="mean of available within-chamber roll-call participation and bill follow-through components",
                scope="observed legislative execution; not broad Conscientiousness and not generic work ethic",
            ),
            "conversational_dominance": band(
                behavior_ranks["conversational_dominance"](observed.get("dominance_z")),
                definition="observed floor-taking and floor-defense role in covered conversations",
                scope="conversation behavior; not Extraversion or formal leadership",
            ),
            "words_per_turn": band(
                behavior_ranks["words_per_turn"](observed.get("expressiveness_z")),
                definition="observed answer-length/expressiveness role in covered conversations",
                scope="turn length; not enthusiasm, energy, or Extraversion",
            ),
        }
        cooperation_value = (cooperation.get(name) or {}).get("cooperation_v2_z")
        if cooperation_value is not None:
            behavior = {
                "cross_party_cooperation": band(
                    behavior_ranks["cross_party_cooperation"](cooperation_value),
                    definition="mean of within-chamber cross-party tie-share and directional other-party cosponsorship-share z scores; both components required",
                    scope="share-based behavior in the tracked bills/ties data; not bill volume, friendliness, or Agreeableness",
                ),
                **behavior,
            }

        hearing_node = hearing.get(name)
        hearing_block = None
        if hearing_node and finite(hearing_node.get("antagonism_z")) is not None:
            hearing_block = {
                "band": band(
                    hearing_rank(hearing_node.get("antagonism_z")),
                    definition="observed hearing antagonism",
                    scope="covered hearing behavior; independent of broad Agreeableness, interview-language low-antagonism, and cross-party cooperation",
                    polarity="higher means more antagonistic",
                ),
                "present": True,
            }

        speech_node = speech.get(name) or {}
        acoustic = general_audio.get(name)
        if acoustic:
            acoustic = dict(acoustic)
            acoustic["channels"] = {
                channel: {
                    "value": value,
                    "band": (band(general_audio_ranks[channel](value))
                             if channel in SPEECH_CHANNELS else None),
                    "interpretation": ({
                        "pause": "mean unvoiced-segment length; not hesitation by itself",
                        "pace": "voiced segments per second; not words per minute",
                        "volume_dyn": "normalized loudness variability; not absolute loudness",
                        "pitch_var": "normalized F0 variability; not emotion or personality",
                        "strain": "local jitter ratio; non-clinical and not psychological strain",
                        "f0_mean": "mean fundamental frequency; no gender, emotion, or personality inference",
                    }[channel]),
                }
                for channel, value in acoustic["channels"].items()
            }
            acoustic["scope"] = (
                "general member baseline across all accepted clips and settings; thin coverage "
                "reduces claim strength but never fails the member"
            )
        else:
            acoustic = None
        # COMPLEMENTARY, NOT OVERRIDING (Paul, 2026-09-07).  Before this the base
        # voiceprint layer was an ELSE branch, so 332 members who carry both layers
        # published only the descriptive one and the 74 who carry only the voiceprint
        # published it under the descriptive layer's key -- which made three consumers
        # render them as "0 clips" with an empty table, i.e. as having no audio at all.
        # Each layer is now computed independently and published under its own key, and
        # a layer with no data is None so a renderer falls to an honest empty state
        # instead of an empty-but-truthy object.
        voiceprint = cache_audio_profile(name, audio_cache, verified_audio_members)
        if acoustic is not None:
            descriptive_audio_count += 1
            composition = (acoustic.get("floor_composition") or {}).get("composition")
            if composition == "floor_only":
                floor_only_count += 1
            elif composition == "floor_dominant":
                floor_dominant_count += 1
        if voiceprint is not None:
            voiceprint_audio_count += 1
        if acoustic is not None or voiceprint is not None:
            # Unchanged meaning: members carrying at least one general audio layer.
            general_audio_count += 1
        if acoustic is not None and voiceprint is not None:
            both_audio_layer_count += 1
        elif voiceprint is not None:
            voiceprint_only_count += 1
        elif acoustic is None:
            no_audio_layer_count += 1

        settings = {}
        for setting, block in sorted((speech_node.get("by_setting") or {}).items()):
            clips = int(block.get("clips") or 0)
            episodes = int(block.get("episodes") or 0)
            channels = {channel: finite(block.get(channel + "_mean"))
                        for channel in ALL_SPEECH_CHANNELS}
            channels = {channel: value for channel, value in channels.items() if value is not None}
            if not clips or not channels:
                continue
            settings[setting] = {
                "clips": clips,
                "episodes": episodes,
                "minutes": finite(block.get("minutes")),
                "coverage_strength": coverage_strength(clips, episodes),
                "channels": channels,
                "scope": "setting-specific accepted clips; descriptive association, not a causal setting effect",
            }
        if settings:
            setting_audio_count += 1

        # Topic language is admitted only after repetition across independent episodes. The current
        # subject layer is sparse, so this intentionally yields no claim for most members.
        topic_associations = []
        for topic, block in sorted((speech_node.get("by_subject") or {}).items()):
            clips = int(block.get("clips") or 0)
            episodes = int(block.get("episodes") or 0)
            if clips < 4 or episodes < 2:
                continue
            topic_associations.append({
                "topic": topic,
                "clips": clips,
                "episodes": episodes,
                "channels": {channel: finite(block.get(channel + "_mean"))
                             for channel in ALL_SPEECH_CHANNELS
                             if finite(block.get(channel + "_mean")) is not None},
                "scope": "repeated public topic association; not proof of a psychological trigger or cause",
            })
        if topic_associations:
            topic_association_count += 1

        interaction_node = interaction.get(name) or {}
        interaction_profile = None
        if int(interaction_node.get("turns") or 0) >= 30:
            interaction_profile = {
                "turns": int(interaction_node.get("turns") or 0),
                "independent_programs": int(interaction_node.get("n_programs") or 0),
                "measures": {
                    key: band(
                        interaction_ranks[key](interaction_node.get(key)),
                        definition=INTERACTION_DEFINITIONS[key],
                        scope="observed interaction structure; not a private motive or emotion",
                    )
                    for key in interaction_ranks if interaction_node.get(key) is not None
                },
            }
        repeated_defensive = None
        defense_band = ((interaction_profile or {}).get("measures") or {}).get("defense")
        if (interaction_profile and interaction_profile["independent_programs"] >= 2 and
                defense_band and finite(defense_band.get("pct")) >= 0.58):
            repeated_defensive_count += 1
            repeated_defensive = {
                "band": defense_band,
                "independent_programs": interaction_profile["independent_programs"],
                "turns": interaction_profile["turns"],
                "scope": "repeated observed floor-defense language; not proof of private defensiveness",
            }

        sensibilities = []
        for item in (personal_texture.get(name) or {}).get("items") or []:
            if (item.get("narrative_eligible") is not True or
                    item.get("confidence") != "high" or
                    item.get("category") not in PUBLIC_SENSIBILITY_CATEGORIES):
                continue
            sensibilities.append({
                "category": item.get("category"),
                "claim": item.get("claim"),
                "public_evidence_excerpt": str(item.get("evidence") or "")[:600],
                "source": item.get("source"),
                "date": item.get("date"),
                "locator": item.get("locator") or item.get("url"),
                "scope": "publicly expressed sensibility/context; no claim about private belief or motive",
            })
        sensibilities = sensibilities[:5]
        if sensibilities:
            public_sensibility_count += 1

        communication = source.get("communication_style") or {}
        facets = {}
        for facet_name, node in (communication.get("facets") or {}).items():
            facets[facet_name] = {
                "band": band((node or {}).get("percentile")),
                "grade": (node or {}).get("grade"),
                "scope": "observed public-language communication facet; not a broad Big Five trait",
            }

        desk_psy = (desk[name].get("psy") or {})
        ext = desk_psy.get("ext") or {}
        agency = finite(desk_psy.get("agency_pct"))
        communion = finite(desk_psy.get("communion_pct"))
        appeal_dimensions = []
        if agency is not None and agency >= 0.60:
            appeal_dimensions.append("assertive, outcome-led, or immediate framing")
        if communion is not None and communion >= 0.60:
            appeal_dimensions.append("communicative, community, or human-impact framing")
        if agency is not None and communion is not None and agency <= 0.40 and communion <= 0.40:
            appeal_dimensions.append("technical, factual, and written-evidence framing")
        appeal_profile = {
            "agency_percentile": agency,
            "communion_percentile": communion,
            "assertive_vs_communicative_frame": desk_psy.get("approach"),
            "supported_dimensions": appeal_dimensions,
            "additional_dimensions": (
                "ethos, pathos, logos/facts, ideology, community, and immediacy may be named only "
                "when the public sensibilities or repeated response evidence in this packet supports them"
            ),
            "scope": "demonstrated framing fit, not a claim about private motivation",
        }
        interview_node = member_interviews.get(name)
        full_turns = interview_turns_by_member.get(name) or []
        longform_interview = None
        if interview_node or full_turns:
            longform_interview_count += 1
            longform_turn_count += len(full_turns)
            source_records = interview_sources_by_member.get(name) or []
            accepted_windows = sum(int(row.get("accepted_windows") or 0) for row in source_records)
            semantic_groups = partition_longform_semantics(full_turns)
            longform_interview = {
                "sources": len(source_records),
                "identity_acoustic_windows": accepted_windows,
                "identity_acoustic_minutes": round(accepted_windows * 8.0 / 60.0, 3),
                "descriptive_acoustic_channels": (interview_node or {}).get("features") or {},
                "source_records": source_records,
                "full_turn_count": len(full_turns),
                "full_turn_minutes": round(sum(float(turn.get("duration_seconds") or 0.0)
                                               for turn in full_turns) / 60.0, 3),
                "longest_turn_seconds": max((float(turn.get("duration_seconds") or 0.0)
                                             for turn in full_turns), default=0.0),
                "full_turns": full_turns,
                **semantic_groups,
                "qualified_semantic_turn_count": sum(turn.get("semantic_review_qualified") is True for turn in full_turns),
                "provisional_semantic_turn_count": sum(turn.get("semantic_review_qualified") is not True for turn in full_turns),
                "leadership_source_rule": (
                    "for a current formal leader, pre-current-role evidence supplies the behavioral baseline; "
                    "later evidence is labeled current-role-conditioned and may describe current practice but "
                    "must not alone establish a stable baseline"
                ),
                "scope": (
                    "exact short windows establish identity and descriptive acoustics only; complete reviewed "
                    "speaker turns are the semantic units and may last seconds or many minutes; neither moves "
                    "Big Five, emotion, motive, stance, allocation, or rank evidence. Historical semantic "
                    "claims without exact qualified measured-turn linkage remain explicitly provisional."
                ),
            }
        speech_profile = {
            "status": "PASS",
            "coverage_rule": (
                "every member has a word/interaction baseline; every accepted audio clip is combined "
                "into the general baseline and retained separately by setting; thin coverage limits "
                "claim strength rather than failing the member"
            ),
            "general_word_habits": {
                "communication_facets": facets,
                "raw_text_observations": communication.get("raw_text") or {},
                "scope": "attributed public words and observed turn structure",
            },
            "interaction_habits": interaction_profile,
            # The two audio layers are siblings, each null when the member does not
            # carry it.  general_audio_habits keeps exactly the shape and values it
            # had for descriptive members; it no longer doubles as a home for the
            # voiceprint layer, which now has its own key.
            "general_audio_habits": acoustic,
            "verified_voiceprint_baseline": voiceprint,
            "audio_layer_rule": (
                "the clip baseline and the verified voiceprint are complementary measurements "
                "on different scales; publish whichever a member has, label each with its scale, "
                "and never place raw clip means and within-source z scores in one comparison"
            ),
            "by_setting": settings,
            "longform_interview_evidence": longform_interview,
            "longform_measured_delivery": longform_delivery.get(name),
            "setting_contrasts": speech_node.get("setting_contrasts") or [],
            "repeated_defensive_reactions": repeated_defensive,
            "publicly_expressed_sensibilities": sensibilities,
            "motivating_appeals": appeal_profile,
            "antagonistic_language_and_behavior": {
                "qualified_interview_language": qualified.get("agreeableness_low_antagonism"),
                "hearing_behavior": hearing_block,
                "scope": "include only when non-neutral or otherwise relevant; expression/behavior, not private anger",
            },
            "supported_topic_associations": topic_associations,
            "topic_rule": (
                "topic triggers require at least four observations across two independent episodes; "
                "report as an association, never a proven cause or private sensitivity"
            ),
        }
        record = {
            "name": name,
            "chamber": congestion[name].get("chamber"),
            "construct_rule": "Every field is independently true within its stated scope. Agreement is not required, and disagreement must not be averaged away or treated as an error.",
            "traits": traits,
            "qualified_reads": qualified,
            "behavior": {key: value for key, value in behavior.items() if value is not None},
            "speech_profile": speech_profile,
            "visibility": {
                "observed": band((source.get("public_role_exposure") or {}).get("observed_visibility_percentile")),
                "role_adjusted": band((source.get("public_role_exposure") or {}).get("role_adjusted_percentile")),
                "scope": "visibility relative to formal role; may reflect newsworthiness or strategy and does not establish attention-seeking or Extraversion",
            },
            "hearing_antagonism": hearing_block,
            "receptivity": {
                "pressure_note": pressure_note(desk_psy.get("pressure_mult")),
                "susc_read": desk_psy.get("susc_read"),
                "scope": "covered responsiveness estimate; a tilt, not a guarantee or instruction to abandon an ask",
            },
            "effectiveness": {key: ext.get(key) for key in ("do_pct", "les_pct", "ft", "kb_pct") if ext.get(key) is not None},
        }
        if name in leadership_roles:
            selected_prerole = leadership_prerole_sources[name]
            acquired_prerole = leadership_prerole_acquisition.get(name) or {}
            source_status = selected_prerole.get("status")
            identity_verified_prerole_turns = [
                turn for turn in (interview_turns_by_member.get(name) or [])
                if turn.get("source_era") == "PRE_CURRENT_LEADERSHIP_ROLE"
            ]
            promoted_prerole_turns = [
                turn for turn in identity_verified_prerole_turns
                if turn.get("semantic_review_qualified") is True
                and turn.get("baseline_semantic_eligible") is True
            ]
            record["leadership_audio_source_policy"] = {
                "current_role": leadership_roles[name].get("current_role"),
                "current_role_assumed_on": leadership_roles[name].get("current_role_assumed_on"),
                "baseline_rule": "verified pre-current-role audio only",
                "post_role_rule": "current-role-conditioned; may inform current tactics but cannot alone establish a stable baseline",
                "pre_role_source": ({
                    "status": (
                        "HUMAN_REVIEWED_SOURCE_IDENTITY_VERIFIED"
                        if promoted_prerole_turns
                        else "SOURCE_IDENTITY_VERIFIED_SEMANTIC_REVIEW_PENDING"
                        if identity_verified_prerole_turns
                        else acquired_prerole.get("status")
                    ),
                    "acquisition_status": acquired_prerole.get("status"),
                    "title": selected_prerole.get("title"),
                    "channel": selected_prerole.get("channel"),
                    "recorded_at": selected_prerole.get("recorded_at"),
                    "reference_url": selected_prerole.get("reference_url"),
                    "measured_minutes": (
                        round(float(acquired_prerole.get("measured_duration_seconds")) / 60.0, 3)
                        if acquired_prerole.get("measured_duration_seconds") is not None else None
                    ),
                    "wav_sha256": acquired_prerole.get("wav_sha256"),
                    "identity_authority": bool(identity_verified_prerole_turns),
                    "candidate_turn_count": len(identity_verified_prerole_turns),
                    "reviewed_turn_count": len(promoted_prerole_turns),
                    "profile_effect": (
                        "REVIEWED_SEMANTIC_TURNS_ONLY"
                        if promoted_prerole_turns else "NONE_UNTIL_FULL_PROMOTION"
                    ),
                } if source_status != "HISTORICAL_LONGFORM_SOURCE_GAP" else {
                    "status": source_status,
                    "gap_reason": selected_prerole.get("gap_reason"),
                    "identity_authority": False,
                    "profile_effect": "NONE",
                }),
                "verified_post_role_source_count": sum(
                    row.get("source_era") == "CURRENT_ROLE_CONDITIONED"
                    for row in (interview_sources_by_member.get(name) or [])
                ),
            }
        records[name] = record

    coop_covered = sum("cross_party_cooperation" in row["behavior"] for row in records.values())
    # 300 since 0b65a2cab (CU_IDENTITY_FIX, 2026-10-11): Rep. Robert Menendez (M001226) loses cooperation_v2
    # coverage once Sen. Menendez's Senate bills stop being credited to him by name. CU_IDENTITY_FIX=0 gives 301.
    if len(records) != 533 or coop_covered != (300 if os.environ.get("CU_IDENTITY_FIX", "1") != "0" else 301):
        raise SystemExit("coverage gate failed: members=%d cooperation=%d" % (len(records), coop_covered))
    if any((row.get("speech_profile") or {}).get("status") != "PASS" for row in records.values()):
        raise SystemExit("every member must receive a PASS speech profile")

    output = {
        "schema": "member-narrative-evidence-v4",
        "status": "ACTIVE_FAIL_CLOSED_INPUT",
        "basis_sha256": sha256(paths["basis"]),
        "sources_sha256": {
            **{key: sha256(path) for key, path in paths.items()},
            **{key: sha256(path) for key, path in optional_paths.items() if path.is_file()},
        },
        "method": {
            "rank_convention": "make_member_desk.py bisect_left/(n-1)",
            "constructs_independent": True,
            "cross_party_source": "coop_v2_candidate.json cooperation_v2_z only; no v1 fallback",
            "speech_gate": "all 533 receive word/interaction baseline; all accepted audio settings combine into a general baseline; thin coverage limits strength, never pass/fail",
            "audio_layer_rule": (
                "two complementary audio layers, never substitutes: general_audio_habits carries "
                "clip-weighted raw means over accepted descriptive clips (scale=%s) and "
                "verified_voiceprint_baseline carries duration-weighted eGeMAPSv02 z scores "
                "standardized within the legacy cache's own source (scale=%s). A member publishes "
                "every layer they have; a layer they do not have is null, never an empty object. "
                "The two scales may never share a comparison column on any surface."
                % (DESCRIPTIVE_AUDIO_SCALE, VOICEPRINT_AUDIO_SCALE)
            ),
            "floor_audio_rule": (
                "House-floor clips stay in the pooled baseline and are LABELED, never stripped. "
                "floor_composition reports the floor clip share, which is exactly the weight the "
                "floor pool carries in every pooled channel value: composition=floor_only at a "
                "share of 1.0, floor_dominant above %.2f, floor_present below it, no_floor at 0."
                % FLOOR_DOMINANT_SHARE
            ),
            "defensive_rule": "only repeated high floor-defense behavior across at least two independent programs",
            "topic_rule": "at least four observations across two independent episodes; association only",
            "appeal_rule": "agency/communion framing plus supported public sensibilities; demonstrated fit, not private motive",
            "longform_interview_rule": (
                "identity-checked short windows are anchors and acoustic samples, not conversational turns; "
                "a semantic turn runs from member floor-taking through handoff, has no maximum duration, and "
                "cannot move any engine or rank value; current formal leaders require pre-current-role audio "
                "for baseline claims, while later interviews remain separately labeled role-conditioned evidence"
            ),
            "longform_delivery_rule": (
                "complete turn WAVs use the existing six eGeMAPSv02 feature definitions without a "
                "duration ceiling; full-turn measurements stay separate from eight-second reference "
                "percentiles, personality estimates, and the legacy interaction cohort; only hash-bound "
                "review-qualified turns may support profile claims, with candidate-only measurements "
                "retained as non-authoritative diagnostics and leadership eras kept separate"
            ),
        },
        "counts": {
            "members": len(records), "cross_party_covered": coop_covered,
            "speech_pass": len(records), "general_audio_profiles": general_audio_count,
            # Per-layer census (2026-09-07).  general_audio_profiles keeps its old
            # meaning -- members carrying AT LEAST ONE general layer -- so the number
            # is unchanged; the breakdown below is what was previously invisible.
            "descriptive_audio_profiles": descriptive_audio_count,
            "verified_voiceprint_profiles": voiceprint_audio_count,
            "both_audio_layers": both_audio_layer_count,
            "voiceprint_only_audio_profiles": voiceprint_only_count,
            "no_audio_layer": no_audio_layer_count,
            "floor_only_general_audio": floor_only_count,
            "floor_dominant_general_audio": floor_dominant_count,
            "setting_audio_profiles": setting_audio_count,
            "longform_interview_profiles": longform_interview_count,
            "longform_turns": longform_turn_count,
            "longform_turn_profiles": len(interview_turns_by_member),
            "longform_delivery_measured_members": len(longform_delivery),
            "longform_delivery_measured_turns": len(longform_delivery_doc.get("turns") or []),
            "longform_delivery_qualified_turns": sum(
                row.get("profile_measurement_eligible") is True
                for row in longform_delivery_doc.get("turns") or []
            ),
            "formal_leadership_profiles_with_source_era_policy": len(leadership_roles),
            "leadership_prerole_sources_acquired_pending_promotion": (
                len(leadership_prerole_acquisition) - len({
                    name for name, record in records.items()
                    if ((record.get("leadership_audio_source_policy") or {})
                        .get("pre_role_source") or {}).get("reviewed_turn_count")
                })
            ),
            "leadership_prerole_sources_identity_verified_candidate": len(
                {row["member"] for row in leadership_turn_rows}
            ),
            "leadership_prerole_sources_human_review_promoted": len(
                {
                    name for name, record in records.items()
                    if ((record.get("leadership_audio_source_policy") or {})
                        .get("pre_role_source") or {}).get("reviewed_turn_count")
                }
            ),
            "repeated_defensive_profiles": repeated_defensive_count,
            "public_sensibility_profiles": public_sensibility_count,
            "supported_topic_association_profiles": topic_association_count,
        },
        "members": records,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_suffix(args.out.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(output, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    os.replace(temporary, args.out)
    print("%s: %d members; %d cooperation-covered; %d speech PASS; %d general-audio" %
          (args.out, len(records), coop_covered, len(records), general_audio_count))
    print("  audio layers: %d descriptive, %d verified-voiceprint, %d both, %d voiceprint-only, "
          "%d neither; floor: %d floor-only, %d floor-dominant" %
          (descriptive_audio_count, voiceprint_audio_count, both_audio_layer_count,
           voiceprint_only_count, no_audio_layer_count, floor_only_count, floor_dominant_count))
    print("basis_sha256=" + output["basis_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
