# -*- coding: utf-8 -*-
"""Eligibility rules for the positional Influence bonus.

Only a current chamber/party elite may receive this additive term. Committee chairs and ranking
members are excluded because their committee position is already present in the base committee
score; adding both would count the same institutional power twice.
"""
import re


def committee_leadership_roles(seats):
    """Return committee seat strings tagged Chair or Ranking (including subcommittees)."""
    out=[]
    for piece in str(seats or '').split('|'):
        match=re.search(r'\[([^\]]+)\]',piece)
        role=(match.group(1).lower() if match else '')
        if 'chair' in role or 'ranking' in role:
            out.append(piece.strip())
    return out


def eligible_bonus(entry,seats):
    """Return ``(applied_bonus, reason, committee_roles)`` for one registry entry."""
    entry=entry or {}; nominal=float(entry.get('influence_bonus') or 0.0)
    committee_roles=committee_leadership_roles(seats)
    if entry.get('role_type')!='party_elite':
        return 0.0,'not_party_elite',committee_roles
    if committee_roles:
        return 0.0,'committee_leadership_already_in_base',committee_roles
    return nominal,('eligible_party_elite' if nominal else 'no_nominal_bonus'),committee_roles
