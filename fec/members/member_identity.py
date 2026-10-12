# -*- coding: utf-8 -*-
"""Fail-closed member identity resolution shared by pipeline joins.

REWRITTEN 2026-08-18 (Paul-ordered, after the identity-collision sweep). The previous
version carried the fail-closed name while having three fail-open paths, each verified by
executing it against the live 533-member roster:

  1. FIRST-INITIAL NARROWING ON A BARE SURNAME. `toks[0][0]` is the *first token's* initial,
     but a bare surname has only one token, so it compared the SURNAME's own initial against
     candidates' first names. `Johnson` -> Julie Johnson, `Lee` -> Laurel Lee,
     `Jackson` -> Jonathan Jackson, `Joyce` -> John Joyce, `Meeks` -> Miller-Meeks. The
     apparent refusals (`Moran`, `Smith`, `Scott`) were accidental: they held only because no
     colliding member's first name happened to start with that surname's letter.
  2. UNIQUE-SURNAME MATCH WITH NO FIRST-NAME CHECK. Any non-roster person sharing a
     roster-unique surname was absorbed: `Michael Barr` -> Andy Barr (the exact case
     fec/stance/extract_cu_hearings.py's docstring claims this class mitigates),
     `John Smith` -> Jason Smith, `Travis Hill` -> French Hill, `Jane Waters` -> Maxine Waters.
  3. RAW "Last, First" TRIED BEFORE REORDERING. Its last token is the FIRST name while the
     index is keyed on surnames, so 23 of 533 members resolved to somebody else:
     `Masto, Catherine Cortez` -> Alexandria Ocasio-Cortez, `Clyburn, James` -> John James,
     `Reed, Jack` -> Brian Jack, `Goldman, Craig` -> Angie Craig.

The rule now, in one sentence: **a query that carries a first name must agree on BOTH first
and last name; a query that carries only a surname resolves only if that surname is unique in
the roster; everything else returns None.** fec/network/process_quorum.py's matchname() is the
model this follows.

WHAT DID NOT CHANGE, deliberately: a bare surname unique in the roster still resolves. That is
the common legitimate case ("Chairman Barr" in a hearing) and tightening it further would be a
behavioural change to every caller with no defect to point at. It remains unsafe across the
ROSTER BOUNDARY -- a departed member sharing a surname with a sitting one is invisible here,
which is exactly how the Menendez misattribution happened -- so callers that can supply a
bioguide must, and fec/tools/identity_collision_census.py exists to measure that exposure.

FIRST-NAME AGREEMENT is deliberately not exact-equality. It accepts an initial ("J. Smith" vs
"Jason Smith"), a prefix ("Chris"/"Christopher"), and an explicit NICKNAME equivalence
("Bernie"/"Bernard", "Maggie"/"Margaret", "Jim"/"James", "Lou"/"Luis"). Ambiguity created by
that latitude is resolved by refusing: if more than one roster member survives the check, the
answer is None, never a pick.

The nickname table is lifted from fec/network/process_quorum.py, which had the repo's only
correct resolver and carried this table privately. Sharing it here is the point -- the first
build of this fix omitted it and dropped SITTING MEMBERS from a rebuild of
electoral_history.json (Bernard Sanders, Jack Reed, James Risch, Margaret Hassan), because the
source books call them Bernie, Jack, Jim and Maggie. Strictness that refuses real members is a
defect too, just a quieter one than absorbing strangers.

A multi-token query is checked against ALL of its non-surname tokens, not just the first, so a
source that leads with an initial ("J. French Hill") still matches French Hill on 'french'.
"""
import re, unicodedata, collections, json, os

_SUFFIXES={'jr','sr','ii','iii','iv'}

# Nickname equivalence, lifted from fec/network/process_quorum.py's FIRST_EQUIV so every caller
# of this shared Resolver gets it, not just the Quorum lane.
_FIRST_EQUIV=[
    {'richard','dick','rick','rich'}, {'charles','chuck'}, {'william','bill','billy'},
    {'michael','mike'}, {'robert','bob','bobby','rob'}, {'edward','ed','eddie'},
    {'james','jim','jimmy'}, {'thomas','tom','tommy'}, {'john','jack','johnny'},
    {'joseph','joe'}, {'gerald','jerry'}, {'jerrold','jerry'}, {'daniel','dan'},
    {'donald','don'}, {'ronald','ron'}, {'andrew','andy'}, {'kenneth','ken'},
    {'benjamin','ben'}, {'nicholas','nick'}, {'christopher','chris'},
    {'stephen','steve'}, {'timothy','tim'}, {'matthew','matt'}, {'david','dave'},
    {'samuel','sam'}, {'patrick','pat'}, {'anthony','tony'}, {'alexander','alex'},
    {'katherine','katie','kate'}, {'elizabeth','liz','beth'}, {'margaret','maggie'},
    {'cynthia','cindy'}, {'earl','buddy'}, {'addison','mitch'}, {'rafael','ted'},
    {'louis','lou'}, {'luis','lou'}, {'francis','frank'}, {'rebecca','becca'},
    {'abraham','abe'}, {'gregory','greg'}, {'douglas','doug'}, {'raymond','ray'},
    {'harold','hal'}, {'zachary','zach'}, {'janice','jan'}, {'bernard','bernie'},
    {'jennifer','jen'}, {'elijah','eli'}, {'henry','hank'}, {'bradley','brad'},
]
_FIRST_GROUPS={}
for _i,_g in enumerate(_FIRST_EQUIV):
    for _n in _g: _FIRST_GROUPS.setdefault(_n,set()).add(_i)

# MEMBER ALIASES -- normalized source spelling -> normalized roster spelling.
# NOT a nickname rule: these are individual members whose common name is not derivable from
# their roster name by any general rule. Mirrors the NICK map in process_quorum.py.
#
# EVIDENCE STANDARD, and it is deliberately high: an entry is admitted only when the two names
# are the SAME PERSON, established independently of the data being joined. Surname+state+chamber
# agreement is NOT sufficient evidence -- it was tested and produced mostly false positives,
# because challengers and predecessors share a state with the member who beat or succeeded them
# (Eddie Bernice Johnson vs Julie Johnson TX, David Scott vs Austin Scott GA, Rodney Davis vs
# Danny Davis IL, Raul vs Adelita Grijalva AZ). Every one of those must keep refusing.
_MEMBER_ALIASES={
    # AR-01. The roster carries the legal first name Eric; he serves and appears on every ballot
    # and in the FEC 'Federal Elections' books as Rick. Verified 2026-08-18 against those books:
    # 'Rick Crawford' is the only refused FEC name in 2016-2022 that is genuinely the same
    # person as a roster member.
    'rick crawford':'eric crawford',
}


def normalize_name(value):
    text=unicodedata.normalize('NFKD',str(value or '')).encode('ascii','ignore').decode('ascii').casefold()
    text=re.sub(r"[^a-z0-9]+",' ',text).strip()
    parts=[p for p in text.split() if p not in _SUFFIXES]
    return ' '.join(parts)


def first_names_agree(query_first,candidate_first):
    """True when two first-name tokens can denote the same person.

    Equality, single-initial abbreviation, or a prefix relationship. Deliberately permissive
    about form and strict about identity: 'adam' vs 'adrian' share a prefix of 2 but neither
    is a prefix of the other, so they do NOT agree."""
    if not query_first or not candidate_first: return False
    if query_first==candidate_first: return True
    if len(query_first)==1: return candidate_first.startswith(query_first)
    if len(candidate_first)==1: return query_first.startswith(candidate_first)
    if query_first.startswith(candidate_first) or candidate_first.startswith(query_first): return True
    # explicit nickname equivalence (Bernie/Bernard, Maggie/Margaret, Jim/James, Lou/Luis)
    return bool(_FIRST_GROUPS.get(query_first,set()) & _FIRST_GROUPS.get(candidate_first,set()))


class Resolver:
    def __init__(self,names,name_to_bioguide=None):
        self.names=list(names); self.name_set=set(self.names)
        self.by_norm=collections.defaultdict(list); self.by_last=collections.defaultdict(list)
        for name in self.names:
            norm=normalize_name(name); self.by_norm[norm].append(name)
            if norm: self.by_last[norm.split()[-1]].append(name)
        self.by_bio={bio:name for name,bio in (name_to_bioguide or {}).items() if name in self.name_set and bio}

    def resolve(self,name=None,bioguide=None):
        """Return the roster name, or None. None means REFUSED -- callers must treat it as a
        refusal to guess, never as 'not found, pick something close'."""
        if bioguide and bioguide in self.by_bio: return self.by_bio[bioguide]
        if name in self.name_set: return name
        raw=str(name or '').strip()
        _alias=_MEMBER_ALIASES.get(normalize_name(raw))
        if _alias:
            for _n in self.names:
                if normalize_name(_n)==_alias: return _n
        # A comma means "Last, First": reorder FIRST. The raw form's last token is the FIRST
        # name, which is what made 23 of 533 members resolve to a different person.
        variants=[]
        if ',' in raw:
            left,right=raw.split(',',1); variants.append((right+' '+left).strip())
        variants.append(raw)
        for variant in variants:
            norm=normalize_name(variant)
            exact=self.by_norm.get(norm,[])
            if len(exact)==1: return exact[0]
            if len(exact)>1: return None      # identical full names -> refuse, never pick
            toks=norm.split()
            if not toks: continue
            candidates=self.by_last.get(toks[-1],[])
            if not candidates: continue
            if len(toks)==1:
                # Bare surname: no first name to check. Resolve only if the surname is unique
                # in the roster; otherwise refuse. (The old code guessed via the surname's own
                # initial, which is how `Johnson` became Julie Johnson.)
                if len(candidates)==1: return candidates[0]
                continue
            # A first name was supplied: it MUST agree. This is the check whose absence let
            # `Michael Barr` become Andy Barr.
            #
            # The FIRST given token takes precedence, and later tokens are consulted ONLY if it
            # matches nothing. Trying every given token at once is too permissive: a middle
            # initial can match a DIFFERENT candidate's first name, and the resulting ambiguity
            # refuses a member who was never in doubt. Measured -- "Bennie G. Thompson" matched
            # both Bennie Thompson and Glenn Thompson (on the 'G'), and "Susan M. Collins"
            # matched both Susan Collins and Mike Collins, dropping sitting members from an
            # electoral_history rebuild. The fallback still lets a source that LEADS with an
            # initial resolve ("J. French Hill" -> French Hill on 'french').
            firsts={c:normalize_name(c).split()[0] for c in candidates}
            matched=[c for c in candidates if first_names_agree(toks[0],firsts[c])]
            if len(matched)==1: return matched[0]
            if not matched and len(toks)>2:
                matched=[c for c in candidates
                         if any(first_names_agree(t,firsts[c]) for t in toks[1:-1])]
            if len(matched)==1: return matched[0]
            # zero matches -> the person is not this roster member; >1 -> genuinely ambiguous.
        return None


def billstatus_unverified(bills,key,bioguide):
    """True when BILLSTATUS covers the bill and the NAME-resolved member is not on it.

    The Resolver cannot see across the roster boundary (module docstring): the CU archive
    names Sen. Robert Menendez (M000639, off-roster) and resolves to Rep. Robert Menendez
    (M001226), crediting 42 Senate-bill rows to the House member (2026-10-11). BILLSTATUS
    is bioguide-keyed, so a bill it covers whose sponsor, cosponsors and original
    cosponsors (withdrawn ones included; withdrawal is judged elsewhere) all lack the
    resolved bioguide is a different person. A bill BILLSTATUS does not cover, or a
    member with no bioguide, is NOT judged here (returns False)."""
    bill=(bills or {}).get(key)
    if not bill or not bioguide: return False
    ids={(bill.get('sponsor') or {}).get('bioguide')}
    ids.update(c.get('bioguide') for c in (bill.get('cosponsors') or [])+(bill.get('original_cosponsors') or []))
    return bioguide not in ids


def load_billstatus(root):
    """BILLSTATUS bills for billstatus_unverified(); {} (= the unchecked join) when
    CU_IDENTITY_FIX=0. The one dial gate for every reader of congress_bills_archive."""
    import moneyball_core as MC
    if not MC.cu_identity_fix_on(): return {}
    with open(os.path.join(root,'bill_repository_live.json'),encoding='utf-8') as h:
        return json.load(h)['bills']


def archive_row_unverified(bills,name,congress,bioguide):
    """billstatus_unverified() for a raw congress_bills_archive row (Name, Congress columns).
    A row that is not a parseable bill (letters, notes) is NOT judged (returns False)."""
    from legislation.congress_bill_repository import parse_bill_identity
    try: key=parse_bill_identity(name,congress)['key']
    except ValueError: return False
    return billstatus_unverified(bills,key,bioguide)
