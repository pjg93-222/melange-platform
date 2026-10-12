#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared timestamp contract for press collection receipts.

The R collector historically emitted ``YYYY-MM-DD HH:MM:SS UTC`` while Python
consumers expected ISO 8601 with a numeric offset.  Keep the compatibility rule
in one small module so discovery, intake audit, recovery, and tests cannot drift
back into separate timestamp dialects.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re


class ReceiptTimestampError(ValueError):
    """A receipt timestamp is missing or cannot be interpreted."""

    def __init__(self, reason: str, value, field: str = "completed_at"):
        self.reason = reason
        self.value = value
        self.field = field
        super().__init__("%s: %s=%r" % (reason, field, value))


_LEGACY_UTC_NAME = re.compile(
    r"^(?P<stamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)\s+UTC$"
)


def parse_receipt_timestamp(value, field: str = "completed_at") -> datetime:
    """Return a timezone-aware UTC datetime for every supported receipt shape.

    Supported inputs are ISO 8601 with an offset, ISO 8601 ending in ``Z``, a
    naive ISO stamp (UTC by the producer contract), and the historical R form
    ending in the timezone name ``UTC``.  Other timezone names are deliberately
    refused instead of guessed.
    """
    text = str(value or "").strip()
    if not text:
        raise ReceiptTimestampError("TIMESTAMP_MISSING", value, field)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    legacy = _LEGACY_UTC_NAME.match(candidate)
    if legacy:
        candidate = legacy.group("stamp") + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except (TypeError, ValueError) as exc:
        raise ReceiptTimestampError("TIMESTAMP_UNPARSABLE", value, field) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
