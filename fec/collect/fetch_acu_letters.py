# -*- coding: utf-8 -*-
"""Fetch a durable snapshot of ACU advocacy letters.

The two GraphQL operations in this collector are mirrored from the live
browser-network capture in audits/legfix3/acu_graphql_shapes.md (2026-07-25).
There is deliberately no --tracker mode: that capture did not reveal a
tracker-position request, and inventing an endpoint would not be evidence.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import http.client
import json
import math
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# main() names the snapshot by the run's UTC start date; the pinned 20260725 file is never overwritten
# (system_manifest.json and the legislation evidence receipts hash it by name) unless --force is given.
OUTPUT_DIR = os.path.join(ROOT, "source_data")
PARTIAL_PATH = os.path.join(OUTPUT_DIR, "acu_letters_snapshot.partial.json")

ENDPOINT = "https://www.americascreditunions.org/graphql"
REFERER = (
    "https://www.americascreditunions.org/advocacy/"
    "letters-testimonies"
)
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Referer": REFERER,
    "User-Agent": (
        "Melange-Systems-Legislative-Data/1.0 "
        "(ACU advocacy snapshot collector)"
    ),
}

DEFAULT_PAGE_SIZE = 50
FALLBACK_PAGE_SIZE = 12
MIN_REQUEST_DELAY_SECONDS = 1.0
REQUEST_CAP = 200
MAX_ATTEMPTS = 3
RETRY_AFTER_CAP_SECONDS = 120
PAGE_SIZE_REJECTION_STATUSES = {400, 413, 422}
PAGE_SIZE_ERROR_PATTERN = re.compile(
    r"limit|page.?size|too (many|large)|maximum|exceed", re.I
)

# Verbatim query text captured in audits/legfix3/acu_graphql_shapes.md.
SEARCH_QUERY = """query search($offset: Int, $limit: Int, $query: String, $facets: [InputSolrFacet], $filters: [InputSolrConditionsGroup], $sort: InputSort) {
  allResults: solrSearch(
    index: "searchstax_index"
    langcode: "en"
    offset: $offset
    limit: $limit
    facets: $facets
    query: $query
    filters: $filters
    sort: $sort
  ) {
    count
    documents {
      ... on SolrDocument {
        id
        __typename
      }
      __typename
    }
    facets {
      field
      values {
        count
        value
        __typename
      }
      __typename
    }
    __typename
  }
}"""

ADVOCACY_QUERY = """query AdvocacyQuery($id: [String], $limit: Int!, $sort_field: String!, $direction: EntityQuerySortOrder) {
  entityQuery(
    entityType: STORAGE
    filter: {conditions: [{field: "id", value: $id}]}
    limit: $limit
    sort: {direction: $direction, field: $sort_field}
  ) {
    items {
      id
      label
      entityBundle
      ... on StorageAdvocacyCommunication {
        type: fieldDocumentType {
          label
          __typename
        }
        fieldAsset {
          ... on MediaAcquiaDamPdfAsset {
            acquiaDamAssetId {
              versionId
              __typename
            }
            name
            __typename
          }
          ... on MediaAcquiaDamDocumentsAsset {
            acquiaDamAssetId {
              versionId
              __typename
            }
            name
            __typename
          }
          __typename
        }
        fieldPublishDate {
          formatted(format: "c")
          __typename
        }
        fieldRecipientAgency {
          label
          __typename
        }
        fieldBusinessArea {
          label
          __typename
        }
        fieldMemberOnlyContent
        fieldAdvocacyIssueTypes {
          label
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}"""

SEARCH_FILTERS = [
    {
        "tag": "default",
        "conditions": [
            {
                "field": "storage_type",
                "values": ["advocacy_communication"],
            },
            {
                "field": "document_type_machine_name",
                "operator": "IN",
                "values": [
                    "policy_letter",
                    "comment_letter",
                    "joint_letters",
                    "congressional_testimony",
                ],
            },
        ],
    }
]

SEARCH_FACETS = [
    {"field": "document_type", "min_count": 1},
    {"field": "recipient_agency_name", "min_count": 1},
]

CACHED_TAG_COUNTS = {
    "Examination and Supervision": 176,
    "Payments & Technology": 92,
    "Consumer Protection": 87,
    "Lending": 85,
    "Tax, Budget & Bank Attacks": 82,
    "Credit Union Powers & Charter": 74,
    "Regulatory Relief": 23,
    "Information Security": 21,
    "Diversity, Equity & Inclusion": 2,
}

# Mirrored from the legislative-audit scratchpad gapb.py:11-19 and its
# checked-in reference at audits/legfix2/audit_sp_reference/gapb.py:11-19.
L1_CATEGORY_MAP = {
    "Examination and Supervision": [
        "Examination & Supervision",
        "CU Capital Requirements",
        "NCUA Budget & Insurance",
    ],
    "Payments & Technology": [
        "Interchange",
        "Payments",
        "Technology & Emerging",
    ],
    "Information Security": [
        "Fraud Prevention",
        "Data Privacy & Cyber",
    ],
    "Credit Union Powers & Charter": [
        "Charter Modernization",
        "Defense CU Issues",
        "Field of Membership",
    ],
    "Tax, Budget & Bank Attacks": [
        "CU Tax Status",
        "CDFI Fund",
    ],
    "Consumer Protection": [
        "CFPB",
        "Credit Card Mandates",
        "TCPA",
    ],
    "Lending": [
        "Member Business Lending",
        "Mortgage & Housing",
    ],
}

BILL_PATTERNS = {
    "H.R.8398": re.compile(
        r"(?<![A-Z0-9])H\s*\.?\s*R\s*\.?\s*8398(?!\d)",
        re.IGNORECASE,
    ),
    "H.R.8413": re.compile(
        r"(?<![A-Z0-9])H\s*\.?\s*R\s*\.?\s*8413(?!\d)",
        re.IGNORECASE,
    ),
}


class CollectorFailure(RuntimeError):
    """A fail-closed collection error with a printable status."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class PageSizeRejected(CollectorFailure):
    """The speculative 50-item page was rejected or silently capped."""


def _utc_iso():
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _search_payload(offset, limit):
    return {
        "operationName": "search",
        "variables": {
            "offset": offset,
            "limit": limit,
            "query": "",
            "filters": SEARCH_FILTERS,
            "sort": {"field": "name", "order": "ASC"},
            "facets": SEARCH_FACETS,
        },
        "query": SEARCH_QUERY,
    }


def _advocacy_payload(ids, limit):
    return {
        "operationName": "AdvocacyQuery",
        "variables": {
            "id": ids,
            "limit": limit,
            "sort_field": "name",
            "direction": "ASC",
        },
        "query": ADVOCACY_QUERY,
    }


def _graphql_error_summary(errors):
    if not isinstance(errors, list):
        return "GraphQL response contained an errors field"
    messages = []
    for entry in errors[:3]:
        if isinstance(entry, dict):
            message = entry.get("message")
        else:
            message = entry
        if message:
            messages.append(str(message).replace("\r", " ").replace("\n", " "))
    if not messages:
        return "GraphQL response contained %d error(s)" % len(errors)
    return "; ".join(messages)[:500]


class GraphQLClient:
    """Small rate-limited stdlib GraphQL client."""

    def __init__(self, timeout):
        self.timeout = timeout
        self.requests_made = 0

    def post(self, payload, allow_page_size_fallback=False):
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            ENDPOINT,
            data=body,
            headers=HEADERS,
            method="POST",
        )
        for attempt in range(MAX_ATTEMPTS):
            # Every attempt, retries included, passes the cap check and the politeness delay
            # and is counted against REQUEST_CAP.
            if self.requests_made >= REQUEST_CAP:
                raise CollectorFailure(
                    "REQUEST_CAP",
                    "hard cap of %d requests reached" % REQUEST_CAP,
                )
            if self.requests_made:
                time.sleep(MIN_REQUEST_DELAY_SECONDS)
            self.requests_made += 1
            retry_after = None
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout
                ) as response:
                    status_code = getattr(response, "status", 200)
                    content_type = response.headers.get("Content-Type", "")
                    raw = response.read()
                break
            except urllib.error.HTTPError as exc:
                status = "HTTP %s" % exc.code
                if (
                    allow_page_size_fallback
                    and exc.code in PAGE_SIZE_REJECTION_STATUSES
                ):
                    raise PageSizeRejected(
                        status,
                        "server rejected the 50-item request",
                    ) from exc
                failure = CollectorFailure(
                    status,
                    "ACU GraphQL returned %s" % status,
                )
                retryable = exc.code == 429 or 500 <= exc.code <= 599
                retry_after = (
                    exc.headers.get("Retry-After") if exc.headers else None
                )
                exc.close()
                cause = exc
            except urllib.error.URLError as exc:
                reason = getattr(exc, "reason", exc)
                if isinstance(reason, (TimeoutError, socket.timeout)):
                    failure = CollectorFailure(
                        "TIMEOUT",
                        "ACU GraphQL request exceeded %.1fs"
                        % self.timeout,
                    )
                    retryable = True
                else:
                    failure = CollectorFailure(
                        "URL_ERROR",
                        "ACU GraphQL request failed: %s" % reason,
                    )
                    retryable = isinstance(reason, ConnectionError)
                cause = exc
            except (TimeoutError, socket.timeout) as exc:
                failure = CollectorFailure(
                    "TIMEOUT",
                    "ACU GraphQL request exceeded %.1fs" % self.timeout,
                )
                retryable = True
                cause = exc
            except (OSError, http.client.HTTPException) as exc:
                # urlopen() failures arrive as HTTPError/URLError and are handled above, but a failure
                # DURING response.read() -- IncompleteRead, RemoteDisconnected, ConnectionResetError --
                # is a plain OSError/HTTPException that main() does not catch, so a mid-body reset late
                # in a ~200-request crawl escaped as a bare traceback: no status line, no partial
                # snapshot, every accumulated letter lost.  Route it to the typed failure the
                # partial-snapshot machinery already handles.
                failure = CollectorFailure(
                    type(exc).__name__,
                    "ACU GraphQL response read failed: %s" % exc,
                )
                retryable = True
                cause = exc

            if not retryable or attempt == MAX_ATTEMPTS - 1:
                raise failure from cause
            if retry_after and re.fullmatch(r"[0-9]+", retry_after.strip()):
                delay = min(int(retry_after), RETRY_AFTER_CAP_SECONDS)
            else:
                delay = 2 ** attempt * 2
            print(
                "retrying after %s in %ds (attempt %d/%d)"
                % (failure.status, delay, attempt + 1, MAX_ATTEMPTS),
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)

        try:
            text = raw.decode("utf-8", "strict")
            document = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CollectorFailure(
                "HTTP %s NON_JSON" % status_code,
                "ACU GraphQL returned non-JSON content (%s)"
                % (content_type or "unknown content type"),
            ) from exc
        if not isinstance(document, dict):
            raise CollectorFailure(
                "HTTP %s BAD_JSON" % status_code,
                "ACU GraphQL response root is not an object",
            )
        if document.get("errors"):
            summary = _graphql_error_summary(document["errors"])
            if allow_page_size_fallback and PAGE_SIZE_ERROR_PATTERN.search(summary):
                raise PageSizeRejected("GRAPHQL_ERRORS", summary)
            raise CollectorFailure("GRAPHQL_ERRORS", summary)
        return document


def _clean_graphql(value):
    if isinstance(value, dict):
        return {
            key: _clean_graphql(item)
            for key, item in value.items()
            if key != "__typename"
        }
    if isinstance(value, list):
        return [_clean_graphql(item) for item in value]
    return value


def _labels(value):
    if value is None:
        return []
    entries = value if isinstance(value, list) else [value]
    found = set()
    for entry in entries:
        if isinstance(entry, dict):
            label = entry.get("label")
        else:
            label = entry
        if label is not None and str(label).strip():
            found.add(str(label).strip())
    return sorted(found, key=lambda item: (item.casefold(), item))


def _formatted_date(value):
    if isinstance(value, list):
        for entry in value:
            date = _formatted_date(entry)
            if date:
                return date
        return ""
    if isinstance(value, dict):
        formatted = value.get("formatted")
        return str(formatted).strip() if formatted is not None else ""
    return ""


def _normalize_letter(item):
    if not isinstance(item, dict):
        raise CollectorFailure(
            "DETAIL_SCHEMA",
            "AdvocacyQuery item is not an object",
        )
    entity_id = item.get("id")
    if entity_id is None or not str(entity_id).strip():
        raise CollectorFailure(
            "DETAIL_SCHEMA",
            "AdvocacyQuery item has no id",
        )
    label = item.get("label")
    title = "" if label is None else str(label)
    cleaned = _clean_graphql(item)
    return {
        "id": str(entity_id),
        "label": title,
        "title": title,
        "url": None,
        "entityBundle": cleaned.get("entityBundle"),
        "type": cleaned.get("type"),
        "fieldAsset": cleaned.get("fieldAsset"),
        "fieldPublishDate": cleaned.get("fieldPublishDate"),
        "fieldRecipientAgency": cleaned.get("fieldRecipientAgency"),
        "fieldBusinessArea": cleaned.get("fieldBusinessArea"),
        "fieldAdvocacyIssueTypes": cleaned.get(
            "fieldAdvocacyIssueTypes"
        ),
        "fieldMemberOnlyContent": cleaned.get(
            "fieldMemberOnlyContent"
        ),
    }


def _letter_sort_key(letter):
    date = _formatted_date(letter.get("fieldPublishDate"))
    locator = letter.get("url") or letter.get("id") or ""
    return (date, str(locator))


def _sorted_letters(letters_by_id):
    return sorted(letters_by_id.values(), key=_letter_sort_key)


def _new_state(page_size):
    return {
        "page_size": page_size,
        "reported_count": None,
        "search_ids_seen": 0,
        "letters_by_id": {},
    }


def _search_result(document):
    data = document.get("data")
    result = data.get("allResults") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        raise CollectorFailure(
            "SEARCH_SCHEMA",
            "search response has no data.allResults object",
        )
    count = result.get("count")
    documents = result.get("documents")
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
    ):
        raise CollectorFailure(
            "SEARCH_SCHEMA",
            "search response count is not a nonnegative integer",
        )
    if not isinstance(documents, list):
        raise CollectorFailure(
            "SEARCH_SCHEMA",
            "search response documents is not a list",
        )
    ids = []
    for entry in documents:
        entity_id = entry.get("id") if isinstance(entry, dict) else None
        if entity_id is None or not str(entity_id).strip():
            raise CollectorFailure(
                "SEARCH_SCHEMA",
                "search response contains a document without an id",
            )
        ids.append(str(entity_id))
    return count, ids


def _detail_items(document):
    data = document.get("data")
    query = data.get("entityQuery") if isinstance(data, dict) else None
    items = query.get("items") if isinstance(query, dict) else None
    if not isinstance(items, list):
        raise CollectorFailure(
            "DETAIL_SCHEMA",
            "AdvocacyQuery response has no data.entityQuery.items list",
        )
    return items


def _fetch_attempt(client, page_size, state):
    offset = 0
    seen_search_ids = set()
    while (
        state["reported_count"] is None
        or offset < state["reported_count"]
    ):
        allow_fallback = page_size == DEFAULT_PAGE_SIZE
        search_document = client.post(
            _search_payload(offset, page_size),
            allow_page_size_fallback=allow_fallback,
        )
        count, ids = _search_result(search_document)
        if state["reported_count"] is None:
            state["reported_count"] = count
        elif count != state["reported_count"]:
            raise CollectorFailure(
                "COUNT_CHANGED",
                "search count changed from %d to %d during pagination"
                % (state["reported_count"], count),
            )

        if len(ids) > page_size:
            raise CollectorFailure(
                "SEARCH_SCHEMA",
                "search returned %d ids for limit %d"
                % (len(ids), page_size),
            )
        if (
            offset + len(ids) < count
            and len(ids) < page_size
        ):
            if allow_fallback:
                raise PageSizeRejected(
                    "PAGE_SIZE_CAPPED",
                    "server returned %d ids for a 50-item page"
                    % len(ids),
                )
            raise CollectorFailure(
                "SHORT_SEARCH_PAGE",
                "search returned %d ids at offset %d before count %d"
                % (len(ids), offset, count),
            )
        if not ids and offset < count:
            raise CollectorFailure(
                "EMPTY_SEARCH_PAGE",
                "search returned no ids at offset %d before count %d"
                % (offset, count),
            )
        duplicates = seen_search_ids.intersection(ids)
        if duplicates:
            raise CollectorFailure(
                "DUPLICATE_SEARCH_ID",
                "search repeated id(s): %s"
                % ", ".join(sorted(duplicates)),
            )
        if not ids:
            # count == 0 (a non-empty count with no ids already raised EMPTY_SEARCH_PAGE); skip the
            # detail query rather than send id=[].
            break

        detail_document = client.post(
            _advocacy_payload(ids, page_size),
            allow_page_size_fallback=allow_fallback,
        )
        normalized = [
            _normalize_letter(item)
            for item in _detail_items(detail_document)
        ]
        detail_ids = [letter["id"] for letter in normalized]
        if len(detail_ids) != len(set(detail_ids)):
            raise CollectorFailure(
                "DUPLICATE_DETAIL_ID",
                "AdvocacyQuery returned duplicate ids",
            )
        if set(detail_ids) != set(ids):
            missing = sorted(set(ids).difference(detail_ids))
            extra = sorted(set(detail_ids).difference(ids))
            message = "AdvocacyQuery id mismatch"
            if missing:
                message += "; missing=" + ",".join(missing)
            if extra:
                message += "; extra=" + ",".join(extra)
            if allow_fallback and missing and not extra:
                raise PageSizeRejected("DETAIL_PAGE_CAPPED", message)
            raise CollectorFailure("DETAIL_ID_MISMATCH", message)

        seen_search_ids.update(ids)
        state["search_ids_seen"] = len(seen_search_ids)
        for letter in normalized:
            state["letters_by_id"][letter["id"]] = letter
        print(
            "page_size=%d offset=%d search=%d/%d details=%d "
            "requests=%d"
            % (
                page_size,
                offset,
                len(seen_search_ids),
                count,
                len(state["letters_by_id"]),
                client.requests_made,
            ),
            flush=True,
        )
        offset += page_size

    expected = state["reported_count"]
    if len(seen_search_ids) != expected:
        raise CollectorFailure(
            "SEARCH_COUNT_MISMATCH",
            "search yielded %d unique ids but reported %d"
            % (len(seen_search_ids), expected),
        )
    if len(state["letters_by_id"]) != expected:
        raise CollectorFailure(
            "DETAIL_COUNT_MISMATCH",
            "collected %d detail records but search reported %d"
            % (len(state["letters_by_id"]), expected),
        )
    return _sorted_letters(state["letters_by_id"])


def _atomic_write_json(path, payload):
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    temporary = path + ".tmp." + str(os.getpid())
    try:
        with open(
            temporary,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as output:
            json.dump(
                payload,
                output,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def _snapshot_payload(letters, reported_count, fetched_at):
    return {
        "schema": "acu-advocacy-letters-snapshot-v1",
        "source": ENDPOINT,
        "fetched_at": fetched_at,
        "reported_count": reported_count,
        "collected_count": len(letters),
        "letters": letters,
    }


def _write_partial(state, client, failure):
    letters = _sorted_letters(state["letters_by_id"])
    payload = _snapshot_payload(
        letters,
        state["reported_count"],
        _utc_iso(),
    )
    payload.update(
        {
            "status": "partial",
            "page_size": state["page_size"],
            "search_ids_seen": state["search_ids_seen"],
            "requests_made": client.requests_made,
            "error_status": failure.status,
            "error": str(failure),
        }
    )
    _atomic_write_json(PARTIAL_PATH, payload)


def _tag_counts(letters):
    counts = collections.Counter()
    for letter in letters:
        tags = _labels(letter.get("fieldAdvocacyIssueTypes"))
        counts.update(tags)
    return counts


def _load_routing_weights(path):
    with open(path, encoding="utf-8") as source:
        audit = json.load(source)
    rows = audit.get("rows") if isinstance(audit, dict) else None
    if not isinstance(rows, list):
        raise ValueError(
            "routing audit has no top-level rows list: %s" % path
        )
    weights = collections.defaultdict(float)
    selected_count = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("routing audit contains a non-object row")
        if row.get("disposition") != "selected":
            continue
        category = row.get("effective_category")
        if not category:
            raise ValueError(
                "selected routing row has no effective_category"
            )
        try:
            contribution = float(row.get("contribution") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "selected routing row has invalid contribution"
            ) from exc
        if not math.isfinite(contribution):
            raise ValueError(
                "selected routing row has non-finite contribution"
            )
        weights[str(category)] += abs(contribution)
        selected_count += 1
    total = sum(weights.values())
    if not selected_count or total <= 0:
        raise ValueError(
            "routing audit has no positive selected absolute contribution"
        )
    return weights, total, selected_count


def _print_tag_counts(fresh):
    print()
    print("LAYER-1 TAG COUNTS (fresh vs cached 2026-07-24)")
    for tag, cached in CACHED_TAG_COUNTS.items():
        current = fresh.get(tag, 0)
        print(
            "  %-34s fresh=%3d  cached=%3d  delta=%+d"
            % (tag, current, cached, current - cached)
        )
    unexpected = sorted(set(fresh).difference(CACHED_TAG_COUNTS))
    for tag in unexpected:
        print(
            "  %-34s fresh=%3d  cached=n/a"
            % (tag, fresh[tag])
        )


def _print_reverse_gaps(fresh, weights, total_weight, selected_count):
    old_acu_total = sum(
        CACHED_TAG_COUNTS[layer] for layer in L1_CATEGORY_MAP
    )
    new_acu_total = sum(
        fresh.get(layer, 0) for layer in L1_CATEGORY_MAP
    )
    if new_acu_total <= 0:
        # main() promises the snapshot survives this (it is already written and SUCCESS printed).
        print(
            "WARNING reverse-gap multipliers skipped: fresh letters contain no tags in the "
            "seven mapped layers",
            file=sys.stderr,
        )
        return

    rows = []
    for layer, categories in L1_CATEGORY_MAP.items():
        pillar_weight = sum(weights.get(category, 0.0)
                            for category in categories)
        pillar_share = pillar_weight / total_weight
        old_acu_share = CACHED_TAG_COUNTS[layer] / old_acu_total
        new_acu_share = fresh.get(layer, 0) / new_acu_total
        old_multiplier = pillar_share / old_acu_share
        new_multiplier = (
            pillar_share / new_acu_share
            if new_acu_share
            else None
        )
        rows.append(
            (
                layer,
                old_acu_share,
                new_acu_share,
                pillar_share,
                old_multiplier,
                new_multiplier,
            )
        )

    rows.sort(
        key=lambda row: (
            row[5] is None,
            -(row[5] or 0),
            row[0],
        )
    )
    print()
    print(
        "REVERSE-GAP MULTIPLIERS "
        "(pillar share / ACU share; old vs fresh)"
    )
    print(
        "  mapping: audits/legfix2/audit_sp_reference/"
        "gapb.py:11-19"
    )
    print(
        "  routing: selected rows=%d  total_abs_contribution=%.1f"
        % (selected_count, total_weight)
    )
    for (
        layer,
        old_acu_share,
        new_acu_share,
        pillar_share,
        old_multiplier,
        new_multiplier,
    ) in rows:
        if new_multiplier is None:
            new_text = "n/a"
            delta_text = "n/a"
            flag = "NO FRESH ACU SHARE"
        else:
            new_text = "%.2fx" % new_multiplier
            delta_text = "%+.2fx" % (
                new_multiplier - old_multiplier
            )
            flag = (
                "OVER-WEIGHTED"
                if new_multiplier >= 1.5
                else (
                    "UNDER-WEIGHTED"
                    if new_multiplier <= 0.67
                    else ""
                )
            )
        print(
            "  %-32s ACU old=%5.1f%% new=%5.1f%%  "
            "pillar=%5.1f%%  multiplier old=%5.2fx new=%5s "
            "delta=%6s  %s"
            % (
                layer,
                100 * old_acu_share,
                100 * new_acu_share,
                100 * pillar_share,
                old_multiplier,
                new_text,
                delta_text,
                flag,
            )
        )


def _print_bill_evidence(letters):
    print()
    print(
        "H.R.8398 / H.R.8413 EVIDENCE "
        "(tolerant match against label/title only)"
    )
    matches = []
    found_bills = set()
    for letter in letters:
        title = letter.get("label") or ""
        bills = [
            bill for bill, pattern in BILL_PATTERNS.items()
            if pattern.search(title)
        ]
        if bills:
            found_bills.update(bills)
            matches.append((letter, bills))
    if not matches:
        print("  NONE")
    for letter, bills in matches:
        tags = _labels(letter.get("fieldAdvocacyIssueTypes"))
        print(
            "  bills=%s | date=%s | id=%s | tags=%s | title=%s"
            % (
                ",".join(bills),
                _formatted_date(letter.get("fieldPublishDate"))
                or "(none)",
                letter["id"],
                " ; ".join(tags) or "(none)",
                letter.get("label") or "",
            )
        )
    for bill in BILL_PATTERNS:
        if bill not in found_bills:
            print("  %s: NO MATCHING LETTER LABEL" % bill)


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Fetch ACU advocacy letters and compare fresh issue shares "
            "with a legislation routing audit."
        )
    )
    parser.add_argument(
        "--routing-audit",
        required=True,
        help=(
            "legislation_routing_audit.json used to recompute selected "
            "absolute-contribution pillar shares"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="per-request timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing snapshot for the same date",
    )
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    return args


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = _parse_args(argv)
    started = datetime.datetime.now(datetime.timezone.utc)

    try:
        weights, total_weight, selected_count = _load_routing_weights(
            args.routing_audit
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(
            "ERROR routing-audit validation failed: %s" % exc,
            file=sys.stderr,
        )
        return 1

    # Dated by the run's UTC start so a rerun never silently replaces pinned evidence; refuse before any request.
    output_path = os.path.join(
        OUTPUT_DIR,
        "acu_letters_snapshot_%s.json" % started.strftime("%Y%m%d"),
    )
    if os.path.exists(output_path) and not args.force:
        print(
            "ERROR snapshot already exists: %s (use --force to overwrite)"
            % output_path,
            file=sys.stderr,
        )
        return 1

    client = GraphQLClient(args.timeout)
    state = _new_state(DEFAULT_PAGE_SIZE)
    try:
        try:
            try:
                letters = _fetch_attempt(
                    client,
                    DEFAULT_PAGE_SIZE,
                    state,
                )
            except PageSizeRejected as exc:
                print(
                    "page_size=50 rejected status=%s progress=%d/%s; "
                    "retrying with browser page_size=12"
                    % (
                        exc.status,
                        len(state["letters_by_id"]),
                        state["reported_count"]
                        if state["reported_count"] is not None
                        else "?",
                    ),
                    flush=True,
                )
                state = _new_state(FALLBACK_PAGE_SIZE)
                letters = _fetch_attempt(
                    client,
                    FALLBACK_PAGE_SIZE,
                    state,
                )
        except KeyboardInterrupt as exc:
            raise CollectorFailure(
                "INTERRUPTED", "interrupted by user"
            ) from exc
    except CollectorFailure as exc:
        progress = len(state["letters_by_id"])
        expected = (
            state["reported_count"]
            if state["reported_count"] is not None
            else "?"
        )
        print(
            "ERROR status=%s progress=%d/%s requests=%d: %s"
            % (
                exc.status,
                progress,
                expected,
                client.requests_made,
                exc,
            ),
            file=sys.stderr,
        )
        try:
            _write_partial(state, client, exc)
            print(
                "partial snapshot: %s" % PARTIAL_PATH,
                file=sys.stderr,
            )
        except OSError as write_exc:
            print(
                "ERROR could not write partial snapshot: %s"
                % write_exc,
                file=sys.stderr,
            )
        return 2

    fresh_counts = _tag_counts(letters)
    # The Layer-1 tag comparison below is an ANALYSIS aid built on a hardcoded 2026-07-24 table
    # (CACHED_TAG_COUNTS).  It must never gate the durable snapshot: if ACU renames or drops an
    # issue-type label, fresh_total goes to zero and a complete, fully validated ~200-request
    # collection was discarded with nothing written at all -- not even the partial.
    if sum(CACHED_TAG_COUNTS[layer] for layer in L1_CATEGORY_MAP) <= 0 or sum(
            fresh_counts.get(layer, 0) for layer in L1_CATEGORY_MAP) <= 0:
        print(
            "WARNING mapped Layer-1 tag total is zero; the cached tag table no longer "
            "matches the source vocabulary (snapshot still written)",
            file=sys.stderr,
        )
    try:
        fetched_at = _utc_iso()
        snapshot = _snapshot_payload(
            letters,
            state["reported_count"],
            fetched_at,
        )
        _atomic_write_json(output_path, snapshot)
    except (OSError, ValueError) as exc:
        print(
            "ERROR snapshot validation/write failed: %s" % exc,
            file=sys.stderr,
        )
        return 1
    try:
        os.remove(PARTIAL_PATH)
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(
            "WARNING could not remove stale partial %s: %s" % (PARTIAL_PATH, exc),
            file=sys.stderr,
        )

    print()
    print(
        "SUCCESS collected=%d reported=%d requests=%d page_size=%d"
        % (
            len(letters),
            state["reported_count"],
            client.requests_made,
            state["page_size"],
        )
    )
    print("durable snapshot: %s" % output_path)
    _print_tag_counts(fresh_counts)
    _print_reverse_gaps(
        fresh_counts,
        weights,
        total_weight,
        selected_count,
    )
    _print_bill_evidence(letters)
    return 0


if __name__ == "__main__":
    sys.exit(main())
