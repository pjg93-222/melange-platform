# -*- coding: utf-8 -*-
"""Conservative preparation of the existing Voteview historical member spine."""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path
from typing import Any


REQUIRED_HEADERS = {
    "congress", "chamber", "icpsr", "state_abbrev", "bioname", "bioguide_id"
}


class HistoricMemberError(ValueError):
    pass


def prepare_voteview_spine(
    path: str | Path, *, expected_blank_bioguides: int | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Preserve every physical row and explicitly disposition blank Bioguides.

    Voteview Congress membership is a broad anchor, not an exact service-date
    assertion.  No term dates are synthesized here.
    """

    body = Path(path).read_bytes()
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HistoricMemberError("Voteview spine must be UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames is None or not REQUIRED_HEADERS.issubset(reader.fieldnames):
        raise HistoricMemberError("Voteview spine header mismatch")

    records: list[dict[str, Any]] = []
    blank_bioguides = 0
    for source_row, row in enumerate(reader, start=2):
        try:
            congress = int(row["congress"])
        except (TypeError, ValueError) as exc:
            raise HistoricMemberError(f"invalid congress at source row {source_row}") from exc
        bioguide = (row.get("bioguide_id") or "").strip() or None
        if bioguide is None:
            blank_bioguides += 1
        records.append(
            {
                "source_system_id": "voteview-hsall-members",
                "source_row": source_row,
                "congress": congress,
                "chamber": (row.get("chamber") or "").strip(),
                "icpsr": (row.get("icpsr") or "").strip(),
                "state_abbrev": (row.get("state_abbrev") or "").strip() or None,
                "bioname": (row.get("bioname") or "").strip() or None,
                "bioguide_id": bioguide,
                "bioguide_disposition": "PRESENT" if bioguide else "SOURCE_BLANK",
                "temporal_precision": "congress-only",
            }
        )
    if expected_blank_bioguides is not None and blank_bioguides != expected_blank_bioguides:
        raise HistoricMemberError("blank-Bioguide conservation failed")
    receipt = {
        "sha256": hashlib.sha256(body).hexdigest(),
        "observed_rows": len(records),
        "accepted_rows": len(records),
        "rejected_rows": 0,
        "blank_bioguide_rows": blank_bioguides,
        "date_semantics": "congress-membership-only; exact service dates not asserted",
    }
    return records, receipt


def historic_member_receipt(
    path: str | Path,
    *,
    generated_at: str,
    source_path_label: str,
    expected_blank_bioguides: int | None = None,
) -> dict[str, Any]:
    """Reproducibly emit the registered historic-member receipt shape."""

    records, internal = prepare_voteview_spine(
        path, expected_blank_bioguides=expected_blank_bioguides
    )
    return {
        "schema_id": "staffer-network-historic-member-receipt-v1",
        "generated_at": generated_at,
        "source_path": source_path_label,
        "source_sha256": internal["sha256"],
        "observed_rows": internal["observed_rows"],
        "accepted_rows": internal["accepted_rows"],
        "rejected_rows": internal["rejected_rows"],
        "blank_bioguide_rows": internal["blank_bioguide_rows"],
        "unique_nonblank_bioguides": len(
            {record["bioguide_id"] for record in records if record["bioguide_id"]}
        ),
        "date_semantics": internal["date_semantics"],
        "status": "PASS" if internal["observed_rows"] == internal["accepted_rows"] else "FAIL",
    }
