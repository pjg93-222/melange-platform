#!/usr/bin/env python3
"""Build the human-reviewed pre-current-role source ledger and acquisition queue.

The ledger contains one row for every formal leadership member, including an
explicit gap when no public long-form source has been located.  Selection is
still discovery evidence: no row establishes speaker identity or changes a
profile until the ordinary audio/hash, ASR, in-band-name, full-turn,
speaker-continuity, and human-review gates all pass.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from fec.speech import source_media_recovery as SMR


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROLES = ROOT / "data/speech/member_leadership_role_cutoffs_v1.json"
DEFAULT_DISCOVERY = ROOT / "data/speech/member_leadership_prerole_discovery_v1.jsonl"
DEFAULT_OUT = ROOT / "data/speech/member_leadership_prerole_reviewed_sources_v1.jsonl"
DEFAULT_QUEUE = ROOT / "data/speech/member_leadership_prerole_acquisition_queue_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/member_leadership_prerole_reviewed_sources_v1.receipt.json"


# Candidate ranks below were read and accepted by a human reviewer.  They are
# not automatically the highest-scoring directory result.
DISCOVERY_RANKS = {
    "Brian Schatz": 1,
    "Christopher Murphy": 1,
    "Cory Booker": 1,
    "Hakeem Jeffries": 2,
    "Jay Obernolte": 1,
    "Joe Neguse": 1,
    "John Thune": 1,
    "Katherine Clark": 1,
    "Lisa McClain": 1,
    "Steve Scalise": 2,
    "Ted Lieu": 1,
    "Tim Scott": 4,
}


MANUAL_SOURCES = {
    "Amy Klobuchar": {
        "source_type": "youtube", "video_id": "TLKFSOemQcg",
        "url": "https://www.youtube.com/watch?v=TLKFSOemQcg",
        "title": "U.S. Senator Amy Klobuchar (D-MN)",
        "channel": "UChicago Institute of Politics", "recorded_at": "2016-02-17",
        "published": "2016-02-17", "duration_seconds": 3850,
        "reference_url": "https://www.youtube.com/watch?v=TLKFSOemQcg",
        "review_note": "Hour-long moderated Institute of Politics conversation.",
    },
    "Bernard Sanders": {
        "source_type": "youtube", "video_id": "qipncSCMMrc",
        "url": "https://www.youtube.com/watch?v=qipncSCMMrc",
        "title": "Bernie Sanders | The Young Turks Interview (FULL) May 27, 2016",
        "channel": "The Young Turks", "recorded_at": "2016-05-27",
        "published": "2016-05-28", "duration_seconds": 2588,
        "reference_url": "https://www.youtube.com/watch?v=qipncSCMMrc",
        "review_note": "Dedicated 43-minute candidate interview; replaces a directory false positive about Sanders.",
    },
    "Catherine Cortez Masto": {
        "source_type": "apple_directory_audio",
        "url": "https://knpr.org/show/knprs-state-of-nevada/2016-07-07/u-s-senate-catherine-cortez-masto-democratic",
        "audio_url": "https://npr.brightspotcdn.com/legacy/videos.knpr.org/audio/160923_cortezmasto_web.mp3",
        "title": "U.S. Senate: Catherine Cortez Masto - Democratic",
        "channel": "KNPR's State of Nevada", "recorded_at": "2016-07-07",
        "published": "2016-07-07", "duration_seconds": None,
        "reference_url": "https://knpr.org/show/knprs-state-of-nevada/2016-07-07/u-s-senate-catherine-cortez-masto-democratic",
        "review_note": "Dedicated Nevada Public Radio candidate interview; page supplies transcript highlights and audio.",
    },
    "Charles Schumer": {
        "source_type": "apple_directory_audio",
        "url": "https://www.wfae.org/2007-01-30/senator-chuck-schumer",
        "audio_url": "https://play.podtrac.com/npr-138333099/edge1.pod.npr.org/anon.npr-mp3/npr/fa/2007/01/20070130_fa_01.mp3",
        "title": "Senator Chuck Schumer", "channel": "Fresh Air",
        "recorded_at": "2007-01-30", "published": "2007-01-30",
        "duration_seconds": None,
        "reference_url": "https://www.wfae.org/2007-01-30/senator-chuck-schumer",
        "review_note": "Dedicated Terry Gross interview tied to Schumer's book; pre-dates his floor-leader role by a decade.",
    },
    "Elizabeth Warren": {
        "source_type": "youtube", "video_id": "vTLndJMS-mo",
        "url": "https://www.youtube.com/watch?v=vTLndJMS-mo",
        "title": "Elizabeth Warren Full Session (2015 Code Conference, Day 1)",
        "channel": "On with Kara Swisher", "recorded_at": "2015-05-26",
        "published": "2016-05-10", "duration_seconds": 3741,
        "reference_url": "https://www.youtube.com/watch?v=vTLndJMS-mo",
        "review_note": "Hour-long onstage interview; directory results were commentary rather than an interview with Warren.",
    },
    "James Lankford": {
        "source_type": "apple_directory_audio",
        "url": "https://podcasts.apple.com/us/podcast/24-senator-james-lankford-faith-family-and-public-service/id1734094890?i=1000665242718&uo=4",
        "audio_url": "https://traffic.megaphone.fm/TLNCN1873947405.mp3?updated=1735262220",
        "title": "#24 - Senator James Lankford: Faith, Family, and Public Service",
        "channel": "Nothing Left Unsaid", "recorded_at": "2024-08-14",
        "published": "2024-08-14", "duration_seconds": 3942,
        "reference_url": "https://podcasts.apple.com/us/podcast/24-senator-james-lankford-faith-family-and-public-service/id1734094890?i=1000665242718&uo=4",
        "review_note": "Dedicated 65-minute interview before the 119th-Congress leadership role; replaces a migrated New York Times media URL that now returns 404.",
    },
    "John Barrasso": {
        "source_type": "apple_directory_audio",
        "url": "https://podcasts.apple.com/us/podcast/all-things-with-kim-strassel-sen-john-barrasso/id971901464?i=1000679957284&uo=4",
        "audio_url": "https://dowjones.simplecastaudio.com/d905ec1f-0648-4e7c-b600-2979d14d2d3e/episodes/4bc6402e-040a-47f3-8b0f-bb676232c60d/audio/128/default.mp3?aid=rss_feed&awCollectionId=d905ec1f-0648-4e7c-b600-2979d14d2d3e&awEpisodeId=4bc6402e-040a-47f3-8b0f-bb676232c60d&feed=uvLfx5GI",
        "title": "Sen. John Barrasso Previews the New Senate Leadership",
        "channel": "WSJ Potomac Watch", "recorded_at": "2024-12-11",
        "published": "2024-12-11", "duration_seconds": 1320,
        "reference_url": "https://podcasts.apple.com/us/podcast/all-things-with-kim-strassel-sen-john-barrasso/id971901464?i=1000679957284&uo=4",
        "review_note": "Dedicated 22-minute interview recorded before he became majority whip.",
    },
    "Mark Warner": {
        "source_type": "youtube", "video_id": "lgEYX73wAOM",
        "url": "https://www.youtube.com/watch?v=lgEYX73wAOM",
        "title": "Budgets and the Future of America's Defense Industry with Sen. Mark Warner",
        "channel": "Atlantic Council", "recorded_at": "2014-03-31",
        "published": "2014-04-01", "duration_seconds": 3537,
        "reference_url": "https://www.youtube.com/watch?v=lgEYX73wAOM",
        "review_note": "Hour-long moderated public-policy conversation with extended Warner turns.",
    },
    "Mike Johnson": {
        "source_type": "apple_directory_audio",
        "url": "https://podcasts.apple.com/us/podcast/109-peace-through-strength-with-congressman-mike-johnson/id1541390793?i=1000556420266&uo=4",
        "audio_url": "https://media.transistor.fm/cdfd558c/a0c01727.mp3",
        "title": "#109 Peace Through Strength with Congressman Mike Johnson",
        "channel": "Better for America Podcast", "recorded_at": "2022-03-24",
        "published": "2022-04-01", "duration_seconds": 1920,
        "reference_url": "https://podcasts.apple.com/us/podcast/109-peace-through-strength-with-congressman-mike-johnson/id1541390793?i=1000556420266&uo=4",
        "review_note": "Dedicated interview from Johnson's House career, nineteen months before his election as Speaker.",
    },
    "Pete Aguilar": {
        "source_type": "youtube", "video_id": "E9RBrNfRZ4k",
        "url": "https://www.youtube.com/watch?v=E9RBrNfRZ4k",
        "title": "Rep. Pete Aguilar Talks Jan. 6th and Investigating Trump",
        "channel": "Higher Learning / The Ringer", "recorded_at": "2022-08-19",
        "published": "2022-08-19", "duration_seconds": 1574,
        "reference_url": "https://www.youtube.com/watch?v=E9RBrNfRZ4k",
        "review_note": "Dedicated 26-minute podcast interview before he became caucus chair.",
    },
    "Shelley Capito": {
        "source_type": "apple_directory_audio",
        "url": "https://podcasts.apple.com/us/podcast/shelley-moore-capito-ferris-bueller-her-bernie-sanders/id1490012592?i=1000465371056&uo=4",
        "audio_url": "https://anchor.fm/s/10045974/podcast/play/10323754/https%3A%2F%2Fd3ctxlq1ktw2nl.cloudfront.net%2Fproduction%2F2020-1-12%2F48087328-48000-2-c82999d980892.mp3",
        "title": "Shelley Moore Capito: Ferris Bueller, her Bernie Sanders impression, and sharing hairspray with the President",
        "channel": "20 for 20", "recorded_at": "2020-02-12",
        "published": "2020-02-12", "duration_seconds": 1440,
        "reference_url": "https://podcasts.apple.com/us/podcast/shelley-moore-capito-ferris-bueller-her-bernie-sanders/id1490012592?i=1000465371056&uo=4",
        "review_note": "Dedicated, personal 24-minute interview before the 119th-Congress leadership role.",
    },
    "Tammy Baldwin": {
        "source_type": "apple_directory_audio",
        "url": "https://www.agri-pulse.com/media/podcasts/85-agri-pulse-open-mic-interview/play/7245-senator-tammy-baldwin-d-wi",
        "audio_url": "https://www.agri-pulse.com/ext/resources/podcasts/o/p/e/1/6/Open-mic-Senator-Tammy-Baldwin-100220016.mp3",
        "title": "Senator Tammy Baldwin, D-WI", "channel": "Agri-Pulse Open Mic",
        "recorded_at": "2016-10-02", "published": "2016-10-02",
        "duration_seconds": 1637,
        "reference_url": "https://www.agri-pulse.com/media/podcasts/85-agri-pulse-open-mic-interview/play/7245-senator-tammy-baldwin-d-wi",
        "review_note": "Dedicated 27-minute policy interview before her conference-secretary role.",
    },
    "Tom Cotton": {
        "source_type": "youtube", "video_id": "KYVCQN3obQ8",
        "url": "https://www.youtube.com/watch?v=KYVCQN3obQ8",
        "title": "Renewing American strength abroad: A conversation with Senator Tom Cotton",
        "channel": "American Enterprise Institute", "recorded_at": "2017-02-06",
        "published": "2017-02-06", "duration_seconds": 3471,
        "reference_url": "https://www.youtube.com/watch?v=KYVCQN3obQ8",
        "review_note": "Hour-long moderated conversation well before his 119th-Congress conference-chair role.",
    },
    "Tom Emmer": {
        "source_type": "apple_directory_audio",
        "url": "https://podcasts.apple.com/us/podcast/saving-crypto-innovation-with-congressman-tom-emmer/id1497698127?i=1000542779915&uo=4",
        "audio_url": "https://downloads.pod.co/b3a5eef4-992e-4755-a874-5eb801a66c42/e9c61d46-49ba-457d-ba0a-348a500d9722.mp3",
        "title": "Saving Crypto Innovation with Congressman Tom Emmer",
        "channel": "On The Chain", "recorded_at": "2021-11-23",
        "published": "2021-11-23", "duration_seconds": 3795,
        "reference_url": "https://podcasts.apple.com/us/podcast/saving-crypto-innovation-with-congressman-tom-emmer/id1497698127?i=1000542779915&uo=4",
        "review_note": "Dedicated 63-minute interview before he became majority whip; selected after the original publisher retired its media URL.",
    },
}


GAPS = {
    "Richard Durbin": (
        "Current whip tenure began in 2005, before modern podcast distribution. "
        "The located 2004 Fox interview is only 6:57 and is access-restricted by its archive; "
        "no public qualifying long-form recording has yet been located."
    ),
}


def parse_date(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def compact_discovery(row: dict) -> dict:
    return {
        "source_type": "apple_directory_audio", "url": row.get("reference_url"),
        "audio_url": row.get("audio_url"), "title": row.get("title"),
        "channel": row.get("show"), "recorded_at": str(row.get("published"))[:10],
        "published": row.get("published"), "duration_seconds": row.get("duration_seconds"),
        "reference_url": row.get("reference_url"),
        "review_note": "Human-selected from the pre-role directory candidates.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roles", type=Path, default=DEFAULT_ROLES)
    parser.add_argument("--discovery", type=Path, default=DEFAULT_DISCOVERY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    args = parser.parse_args()

    role_doc = json.loads(args.roles.read_text(encoding="utf-8-sig"))
    discovered = {
        (row["member"], int(row["candidate_rank"])): row
        for row in SMR.read_jsonl(args.discovery, required=True)
    }
    rows = []
    queue = []
    for member, role in sorted(role_doc["members"].items()):
        source = MANUAL_SOURCES.get(member)
        if source is None and member in DISCOVERY_RANKS:
            key = (member, DISCOVERY_RANKS[member])
            if key not in discovered:
                raise ValueError(f"reviewed discovery candidate missing: {key}")
            source = compact_discovery(discovered[key])
        if source is None:
            if member not in GAPS:
                raise ValueError(f"no reviewed source or explicit gap for {member}")
            rows.append({
                "schema": "member-leadership-prerole-reviewed-source-v1",
                "status": "HISTORICAL_LONGFORM_SOURCE_GAP", "member": member,
                "current_role": role["current_role"],
                "current_role_assumed_on": role["current_role_assumed_on"],
                "gap_reason": GAPS[member], "profile_effect": "NONE",
            })
            continue
        evidence_date = parse_date(str(source["recorded_at"]))
        cutoff = parse_date(str(role["current_role_assumed_on"]))
        if evidence_date >= cutoff:
            raise ValueError(f"{member}: pre-role source is not before role cutoff")
        reviewed = {
            "schema": "member-leadership-prerole-reviewed-source-v1",
            "status": "PREROLE_SOURCE_REVIEWED_IDENTITY_UNVERIFIED", "member": member,
            "current_role": role["current_role"],
            "current_role_assumed_on": role["current_role_assumed_on"],
            "source_era": "PRE_CURRENT_LEADERSHIP_ROLE",
            "baseline_eligible_after_full_promotion": True,
            "identity_authority": False, "profile_effect": "NONE_UNTIL_PROMOTED",
            **source,
        }
        rows.append(reviewed)
        primary = {
            key: reviewed.get(key) for key in (
                "source_type", "url", "audio_url", "video_id", "title", "channel",
                "recorded_at", "published", "duration_seconds", "reference_url",
                "source_era", "current_role_assumed_on",
            ) if reviewed.get(key) is not None
        }
        queue.append({
            "schema": "member-leadership-prerole-acquisition-queue-v1",
            "status": "PREROLE_SOURCE_READY_FOR_ACQUISITION", "member": member,
            "primary": primary, "alternatives": [primary],
            "acquisition_route": (
                "youtube_audio_download" if primary["source_type"] == "youtube"
                else "direct_audio_download"
            ),
            "contract": {
                "source_is_pre_current_role": True,
                "search_metadata_establishes_identity": False,
                "profile_effect_before_promotion": "NONE",
            },
        })

    SMR.write_jsonl_atomic(args.out, rows)
    SMR.write_jsonl_atomic(args.queue, queue)
    counts = {
        "leadership_members": len(rows), "reviewed_prerole_sources": len(queue),
        "historical_longform_source_gaps": len(rows) - len(queue),
        "youtube_sources": sum(row["primary"]["source_type"] == "youtube" for row in queue),
        "direct_audio_sources": sum(
            row["primary"]["source_type"] == "apple_directory_audio" for row in queue
        ),
    }
    SMR.write_json_atomic(args.receipt, {
        "schema": "member-leadership-prerole-reviewed-sources-receipt-v1",
        "status": "PASS_WITH_EXPLICIT_HISTORICAL_GAPS" if counts["historical_longform_source_gaps"] else "PASS",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "counts": counts,
        "contract": {
            "recording_must_predate_current_role": True,
            "publication_is_conservative_proxy_when_recording_date_unknown": True,
            "post_role_audio_is_role_conditioned_not_baseline": True,
            "selection_establishes_identity": False,
            "profile_effect": "NONE_UNTIL_FULL_PROMOTION",
        },
        "inputs": {
            "roles_sha256": SMR.sha256_file(args.roles),
            "discovery_sha256": SMR.sha256_file(args.discovery),
        },
        "outputs": {
            "reviewed_sources": str(args.out.resolve()),
            "reviewed_sources_sha256": SMR.sha256_file(args.out),
            "acquisition_queue": str(args.queue.resolve()),
            "acquisition_queue_sha256": SMR.sha256_file(args.queue),
        },
    })
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
