"""Keep the sentinel statute tests off the network.

When House Code is down, collect() falls back to GovInfo's USCODE (D-259). Without this fixture every outage test would
try the real link service, and the process-wide House breaker (_STATUTE_RUN) would leak from one test into the next. The
GovInfo fallback is offline by default; a test that wants it patches R.govinfo_statute back (see
test_sentinel_source_unavailable.world(govinfo=...)) and R._LINK_OPENER / GF.network_fetcher with fixtures.
"""
from __future__ import annotations

import sys
from unittest.mock import Mock

import pytest


@pytest.fixture(autouse=True)
def _govinfo_statute_offline(monkeypatch):
    refresh = sys.modules.get('legislation.refresh_sentinel_admissions')
    if refresh is not None:
        refresh._STATUTE_RUN.update(house_down=None, integrity_failures=0, counts={'house_code': 0, 'govinfo_uscode': 0})
        monkeypatch.setattr(refresh, 'govinfo_statute',
                            Mock(side_effect=refresh.SourceUnavailable('GovInfo fixture offline')))
    yield
    if refresh is not None:
        refresh._STATUTE_RUN.update(house_down=None, integrity_failures=0, counts={'house_code': 0, 'govinfo_uscode': 0})
