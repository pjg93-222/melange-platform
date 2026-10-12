# -*- coding: utf-8 -*-
"""Shared, cache-only global-client-ID identity harvest machinery."""

import collections
import re


NAME_PLAUSIBILITY_STOPWORDS = frozenset(
    {
        "INC",
        "LLC",
        "LP",
        "LLP",
        "CORP",
        "CORPORATION",
        "CO",
        "COMPANY",
        "THE",
        "OF",
        "AND",
        "GROUP",
        "HOLDINGS",
        "DBA",
        "FKA",
        "ON",
        "BEHALF",
        "INTERNATIONAL",
        "NATIONAL",
        "AMERICA",
        "AMERICAS",
        "AMERICAN",
        "US",
        "USA",
        "FEDERAL",
        "ASSOCIATION",
        "ASSOCIATIONS",
        "ASSOC",
        "COALITION",
        "COUNCIL",
        "INSTITUTE",
        "SOCIETY",
        "FEDERATION",
        "UNION",
        "UNIONS",
    }
)
NAME_PLAUSIBILITY_FAILED_RULE = (
    "intersection_has_fewer_than_2_tokens_and_neither_nonempty_set_"
    "is_a_subset_of_the_other"
)
ID_REJECTION_FIELDS = frozenset(
    {
        "client_id",
        "lobbyview_name",
        "filing_client_name",
        "lobbyview_tokens",
        "filing_name_tokens",
        "failed_rule",
    }
)


def tokenize_identity_name(value, error_type=ValueError):
    """Return the fixed non-generic token set for identity checks."""
    if not isinstance(value, str):
        raise error_type("identity name must be a string")
    return frozenset(
        token
        for token in re.split(r"[^A-Z0-9]+", value.upper())
        if token and token not in NAME_PLAUSIBILITY_STOPWORDS
    )


def name_plausibility(lobbyview_name, filing_client_name, error_type=ValueError):
    """Apply the fixed two-token-or-nonempty-subset identity guard."""
    lobbyview_tokens = tokenize_identity_name(lobbyview_name, error_type)
    filing_tokens = tokenize_identity_name(filing_client_name, error_type)
    if len(lobbyview_tokens & filing_tokens) >= 2:
        return True, "intersection_at_least_2", lobbyview_tokens, filing_tokens
    if (
        (lobbyview_tokens and lobbyview_tokens <= filing_tokens)
        or (filing_tokens and filing_tokens <= lobbyview_tokens)
    ):
        return True, "nonempty_subset", lobbyview_tokens, filing_tokens
    return (
        False,
        NAME_PLAUSIBILITY_FAILED_RULE,
        lobbyview_tokens,
        filing_tokens,
    )


def harvest_guarded_client_ids(
    seed,
    identity_loader,
    error_type=ValueError,
    owner_label="identities",
):
    """Harvest global client IDs and return fixed acceptance receipts.

    ``identity_loader(owner_key, filing_uuid)`` returns ``None`` for an
    unfetched seed UUID or ``(global_client_id, client_name)`` for a cached
    filing.  The caller owns all schema validation and disk access.
    """
    result = {}
    client_id_owners = {}
    for owner_key in sorted(seed):
        entry = seed[owner_key]
        filing_names_by_client_id = collections.defaultdict(set)
        filing_names = set()
        present = 0
        for filing_uuid in entry["uuids"]:
            identity = identity_loader(owner_key, filing_uuid)
            if identity is None:
                continue
            client_id, client_name = identity
            filing_names_by_client_id[client_id].add(client_name)
            filing_names.add(client_name)
            present += 1

        accepted_client_ids = []
        rejected_client_ids = []
        for client_id in sorted(filing_names_by_client_id):
            failed_comparisons = []
            accepted = False
            for client_name in sorted(filing_names_by_client_id[client_id]):
                (
                    plausible,
                    rule,
                    lobbyview_tokens,
                    filing_name_tokens,
                ) = name_plausibility(
                    entry["name"], client_name, error_type=error_type
                )
                if plausible:
                    accepted = True
                    break
                failed_comparisons.append(
                    {
                        "client_id": client_id,
                        "lobbyview_name": entry["name"],
                        "filing_client_name": client_name,
                        "lobbyview_tokens": sorted(lobbyview_tokens),
                        "filing_name_tokens": sorted(filing_name_tokens),
                        "failed_rule": rule,
                    }
                )
            if accepted:
                accepted_client_ids.append(client_id)
            else:
                rejected_client_ids.extend(failed_comparisons)

        for client_id in accepted_client_ids:
            prior = client_id_owners.get(client_id)
            if prior is not None and prior != owner_key:
                raise error_type(
                    "harvested client.id maps to multiple "
                    f"{owner_label}: {client_id} -> {prior}, {owner_key}"
                )
            client_id_owners[client_id] = owner_key

        row = {}
        if "stratum" in entry:
            row["stratum"] = entry["stratum"]
        row.update(
            {
                "lobbyview_name": entry["name"],
                # Accepted-only compatibility alias: a rejected identifier
                # can never escape through client_ids.
                "client_ids": list(accepted_client_ids),
                "accepted_client_ids": list(accepted_client_ids),
                "rejected_client_ids": rejected_client_ids,
                "filing_names_seen": sorted(filing_names),
                "uuids_present": present,
                "uuids_missing": len(entry["uuids"]) - present,
            }
        )
        result[owner_key] = row
    return result
