"""One resolution rule for the `industry` request parameter, used by every route.

DECISION: industry is a request parameter, never a console global.

1. `industry` present   -> must resolve in the registry by label or slug, else
                           'Choose a registered industry: <labels>.'
2. and `dataset` present but different -> the two disagree; say so, never
                           silently prefer one.
3. only `dataset`       -> the entry whose dataset id is that value, or None.
                           None is the legacy path: the downstream reader
                           raises its own 'Choose an available contribution
                           dataset.' exactly as it does today.
4. neither              -> None; the route keeps its existing behaviour.

ScopeError is a ValueError, so server.py's existing handlers answer 400 with
`{"error": str(error)}`. No new except clause is needed anywhere.

Every entry point takes either a report directory or an already-built registry,
so a route that needs the registry for another reason builds it once. `sources`
(contribution_data._sources()) lets a request that already scanned the folder
build that registry from its scan instead of scanning again.
"""
from __future__ import annotations

from urllib.parse import parse_qs

import snapshot_registry


class ScopeError(ValueError):
    """A request named an industry this console cannot address."""


def _registry(report_directory_or_registry, sources=None):
    if (isinstance(report_directory_or_registry, dict)
            and 'snapshots' in report_directory_or_registry):
        return report_directory_or_registry
    return snapshot_registry.registry(report_directory_or_registry, sources)


def labels(report_directory_or_registry):
    """Every registered published label, sorted."""
    return sorted(item['industry'] for item in _registry(report_directory_or_registry)['snapshots'])


def _refusal(registry):
    known = labels(registry)
    if not known:
        return ScopeError('No industry snapshots are registered on this console.')
    return ScopeError('Choose a registered industry: ' + ', '.join(known) + '.')


def resolve(report_directory_or_registry, industry=None, dataset=None, sources=None):
    """The registry entry this request addresses, or None for the legacy path."""
    if industry is None and dataset is None:
        # Nothing to resolve, so nothing is read: a route with no scope on it
        # must not pay for a directory scan it will not use.
        return None
    registry = _registry(report_directory_or_registry, sources)
    if industry is not None:
        if not isinstance(industry, str) or not industry:
            raise _refusal(registry)
        entry = snapshot_registry.find(registry, industry)
        if entry is None:
            raise _refusal(registry)
        if dataset is not None and dataset != entry['dataset']:
            raise ScopeError('The industry and dataset in this request disagree.')
        return entry
    if dataset is not None:
        # An unknown dataset is NOT refused here: the reader that owns the
        # dataset selector raises its own message, unchanged.
        return next((item for item in registry['snapshots'] if item['dataset'] == dataset), None)
    return None


def from_query(report_directory_or_registry, parsed_query):
    """Resolve `?industry=`, from a parse_qs mapping or a raw query string.

    Pass the raw string. parse_qs drops a blank value by default, so a
    mapping built that way reads `?industry=` as no industry at all instead
    of as the empty industry it is, and the request takes the legacy path
    rather than being refused.
    """
    if isinstance(parsed_query, str):
        parsed_query = parse_qs(parsed_query, keep_blank_values=True)
    return resolve(report_directory_or_registry, industry=parsed_query.get('industry', [None])[0])


def from_body(report_directory_or_registry, body, sources=None):
    """Resolve `industry` (and check it against `dataset`) from a POST body."""
    return resolve(report_directory_or_registry, industry=body.get('industry'),
                   dataset=body.get('dataset'), sources=sources)


def echo(entry):
    """What every industry-scoped response carries back, resolved or not."""
    return {'industry': entry['industry'] if entry else None,
            'build_stamp': entry['build_stamp'] if entry else None}


def label_or_value(report_directory_or_registry, value):
    """The published label for a label or slug, or the value itself untouched.

    The graph and comparison routes select by a name that graph_catalog()
    derives from every .xlsx in the report directory, which is a wider set than
    the registry's Overview workbooks. Refusing an unregistered name here would
    take away a view those routes serve today, so an unmatched value is handed
    on and the existing 'Choose an industry with completed reports.' answers it.
    """
    if not isinstance(value, str) or not value:
        return value
    entry = snapshot_registry.find(_registry(report_directory_or_registry), value)
    return entry['industry'] if entry else value
