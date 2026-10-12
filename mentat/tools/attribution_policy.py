"""Evidence for an attribution exclusion, not a search match's confidence."""
import json
from pathlib import Path
import re

POLICY = json.loads((Path(__file__).resolve().parents[1] / 'config/attribution-policy.json').read_text())
UNKNOWN = frozenset(POLICY['unknown_tokens'].split())


def explicit_rejection(reason):
    return str(reason or '').strip().lower().startswith(tuple(POLICY['explicit_rejection_prefixes']))


def source_exclusion(reason):
    reason = str(reason or '').strip()
    return '' if reason.upper() in {value.upper() for value in POLICY['non_evidentiary_exclusions']} else reason


def names_organization(value, entity_type='IND'):
    tokens = re.findall(r'[A-Z0-9]+', str(value or '').upper())
    if not any(token not in UNKNOWN for token in tokens):
        return False
    # A reported entity name identifies an organization; a person's employer
    # field may instead contain a profession, placeholder or unresolved name.
    return (entity_type in ('ORG', 'PAC', 'COM', 'CCM', 'PTY') or
            bool(re.search(POLICY['organization_pattern'], ' '.join(tokens))))
