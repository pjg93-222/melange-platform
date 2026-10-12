# -*- coding: utf-8 -*-
"""Half-open uncertain intervals used by staff employment assertions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


PRECISIONS = {"day", "month", "year", "unknown"}


@dataclass(frozen=True)
class UncertainInterval:
    start_earliest: date | None
    start_latest: date | None
    end_earliest: date | None
    end_latest: date | None
    start_precision: str = "unknown"
    end_precision: str = "unknown"
    asserted_current: bool = False

    def validate(self) -> None:
        if self.start_precision not in PRECISIONS:
            raise ValueError(f"invalid start precision: {self.start_precision}")
        if self.end_precision not in PRECISIONS:
            raise ValueError(f"invalid end precision: {self.end_precision}")
        if (self.start_earliest is None) != (self.start_latest is None):
            raise ValueError("start bounds must both be set or both be unknown")
        if (self.end_earliest is None) != (self.end_latest is None):
            raise ValueError("end bounds must both be set or both be unknown")
        if self.start_earliest and self.start_latest and self.start_earliest > self.start_latest:
            raise ValueError("start_earliest exceeds start_latest")
        if self.end_earliest and self.end_latest and self.end_earliest > self.end_latest:
            raise ValueError("end_earliest exceeds end_latest")
        if self.asserted_current and self.end_earliest is not None:
            raise ValueError("asserted-current interval must keep source end unknown")
        if (
            self.start_earliest is not None
            and self.end_latest is not None
            and self.start_earliest > self.end_latest
        ):
            raise ValueError("interval has no possible half-open realization")

    def effective(self, as_of: date) -> "UncertainInterval":
        """Right-censor an asserted-current interval at the build ``as_of``."""

        self.validate()
        if self.end_earliest is not None:
            if self.end_latest and self.end_latest > as_of:
                raise ValueError("closed interval extends beyond build as_of")
            return self
        if not self.asserted_current:
            return self
        if self.start_earliest and self.start_earliest > as_of:
            raise ValueError("asserted-current interval begins beyond as_of")
        return UncertainInterval(
            self.start_earliest,
            self.start_latest,
            as_of,
            as_of,
            self.start_precision,
            "day",
            False,
        )


def _require_bounded(interval: UncertainInterval) -> None:
    interval.validate()
    if None in (
        interval.start_earliest,
        interval.start_latest,
        interval.end_earliest,
        interval.end_latest,
    ):
        raise ValueError("overlap requires bounded or as_of-clipped intervals")


def guaranteed_overlap_days(a: UncertainInterval, b: UncertainInterval) -> int:
    """Return overlap guaranteed under every realization of both intervals."""

    _require_bounded(a)
    _require_bounded(b)
    start = max(a.start_latest, b.start_latest)  # type: ignore[arg-type]
    end = min(a.end_earliest, b.end_earliest)  # type: ignore[arg-type]
    return max(0, (end - start).days)


def possible_overlap_days(a: UncertainInterval, b: UncertainInterval) -> int:
    """Return the largest overlap allowed by the outer uncertainty bounds."""

    _require_bounded(a)
    _require_bounded(b)
    start = max(a.start_earliest, b.start_earliest)  # type: ignore[arg-type]
    end = min(a.end_latest, b.end_latest)  # type: ignore[arg-type]
    return max(0, (end - start).days)

