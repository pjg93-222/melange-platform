# -*- coding: utf-8 -*-
"""Build the member interaction ledger page.

Level 4 of the industry drill-down.  The industry page ranks members by how many
bills an industry lobbied that they sponsored or cosponsored; this page shows the
FILINGS BEHIND THAT NUMBER, grouped by year: when it was filed, who filed it,
which bill, what position (none -- see below), and what the lobbyist wrote.

ON POSITION.  The LD-2 form has no position field, so no filing in this corpus
states one.  A row therefore shows either "Not disclosed" -- the honest default --
or a label a model INFERRED from the filer's own paragraph, always wearing an
INFERRED tag.  The distinction is carried in data, not in styling: the ledger's
``position`` / ``position_basis`` pair is the source, and ``lobbying.lobby_position_join``
is the only thing that may set a basis other than ``not_disclosed``.

THE FIVE-CLASS LABEL IS WHAT SHIPS.  Support / Oppose / Amend / Monitor / Engage,
Kim's own scheme, because the finer classes carry the useful distinction: amending
a bill and monitoring it are different postures and "engage" erases both.  An
earlier build displayed only the three-class collapse, justified by an Amend F1 of
0.00 -- that figure came from scoring Amend against Kim's gnn annotator, whose
label space contains no Amend at all, so it measured the annotator rather than the
class.  The three-class collapse is still available in the tooltip and the filter.

The DIRECTIONAL classes remain the weak ones -- Support and Oppose sit near F1 0.5
against Kim's labels while the non-directional majority carries the ~94% headline
-- so the page never shows a Support or Oppose badge without that caveat, and the
lobbyist's verbatim description sits beside every one of them.

SIZE.  The page inlines every description verbatim, so it is index-compressed:
organizations, registrants, lobbyists, issue codes, bills and note text are each
interned once and referenced by integer.  Member -> bill -> activity is resolved
in the browser from an inverted index rather than materialized, because the
materialized join is roughly twenty times larger than the data it joins.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT, LOCAL_MELANGE

import argparse
import csv
import html
import json
import os
import sys
from collections import defaultdict

import members.historical_member_names as identity
from lobbying import lobby_posture as posture
from surfaces import make_lobby_network_html as industry

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


IngestError = industry.IngestError

YEARS = industry.YEARS
# 2026-08-05: 8MB (was 6MB).  The sector channel grew the org universe
# 616->1,095 and the ledger 32,561->44,559 rows; this page's contract is a
# COMPLETE render of every sectored ledger row with receipts, so the budget
# moves rather than inventing a per-org cap that would break that contract.
# 2026-10-07: 16MB (was 8MB). Telecom joined the sectored ledger (orgs 259->430, acts 29,122->50,977); same complete-render contract, so the budget moves again.
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024

POSITION_NOT_DISCLOSED_LABEL = "Not disclosed"

CHANNEL_LABELS = {
    "formal_strict": "Formal citation",
    "both_strict": "Formal citation + title match",
    "title_strict": "Title match",
    "title_generous_only": "Title match · generous",
}
PERIOD_LABELS = {
    "first_quarter": "Q1",
    "second_quarter": "Q2",
    "third_quarter": "Q3",
    "fourth_quarter": "Q4",
    "mid_year": "Mid-year",
    "year_end": "Year-end",
}

POSITION_NOTE = (
    "The lobbying filing has no place to state a position. No filing in this "
    "corpus states whether the filer supported or opposed the bill, so a row that "
    "reads “Not disclosed” is reporting the disclosure regime, not a gap in this "
    "build. What the lobbyist actually wrote is reproduced verbatim beside it."
)
INFERRED_POSITION_NOTE = (
    "A row marked INFERRED carries a model's reading of the filer's own "
    "description, not the filer's statement. Against In Song Kim's published "
    "labels the three-class read agrees about 94% of the time, but that headline "
    "is carried almost entirely by the non-directional majority. Support and "
    "Oppose — the two anyone actually wants — are the weak ones, near F1 0.5. "
    "Treat a Support or Oppose badge as a lead worth checking against the filing "
    "text beside it, never as a fact."
)
FIVE_CLASS_NOTE = (
    "Labels follow Kim's five-class scheme, and the finer classes are the point: "
    "AMEND (seeking changes to a bill) and MONITOR (watching it without pushing) "
    "are different postures, and collapsing both to “engage” throws the "
    "distinction away. ENGAGE appears only where a label was never refined past "
    "non-directional; Support and Oppose collapse to themselves."
)
INTERACTION_NOTE = (
    "An interaction is one disclosed lobbying activity that cited a bill this "
    "member sponsored or cosponsored. It records that the organization worked "
    "the bill. It is NOT evidence that anyone contacted this member, that the "
    "member agreed, or that the two ever spoke."
)
NOTE_AUTHORSHIP_NOTE = (
    "The notes are the filer's own words, copied from the filing without edit. "
    "They describe the whole lobbying activity, which often covers several bills "
    "at once, so a note may discuss matters beyond the bill on its row."
)
COVERAGE_NOTE = (
    "Only activities that cite a bill can appear here — roughly 28% of all "
    "activities when pooled across the 2021–2026 window — and that 28% is not a "
    "random sample, because better-resourced filers cite bills more often. A "
    "quiet year on this page can mean a quiet year, or a year of filings that "
    "named no bill."
)


def _default_data_root():
    return os.path.join(LOCAL_MELANGE, "lda_rebuild", "derived")


def _input_paths(data_root, ledger_dir=None, **identity_paths):
    ledger_dir = ledger_dir or os.path.join(data_root, "interactions")
    paths = [
        ("interaction_ledger", os.path.join(ledger_dir, "interaction_ledger.csv")),
        ("interaction_notes", os.path.join(ledger_dir, "interaction_notes.csv")),
        (
            "interaction_bill_members",
            os.path.join(ledger_dir, "interaction_bill_members.csv"),
        ),
        (
            "interaction_receipts",
            os.path.join(ledger_dir, "interaction_receipts.json"),
        ),
        ("fullscope_org_roster", os.path.join(data_root, "fullscope_org_roster.csv")),
        (
            "sector_roster",
            os.path.join(data_root, "sector_spend", "sector_roster.csv"),
        ),
        (
            "sector_spend_json",
            os.path.join(data_root, "sector_spend", "sector_spend.json"),
        ),
        (
            "sector_spend_csv",
            os.path.join(data_root, "sector_spend", "sector_spend.csv"),
        ),
        ("fullscope_receipts", os.path.join(data_root, "fullscope_receipts.json")),
        (
            "champions_csv",
            os.path.join(data_root, "champions_fullscope", "champions.csv"),
        ),
        (
            "dyad_signs",
            os.path.join(data_root, "stance_priors", "dyad_signs.csv"),
        ),
        (
            "bills_audit",
            identity_paths.get("bills_audit_path")
            or os.path.join(MB_ROOT, "bills_audit.csv"),
        ),
        (
            "congestion_roster",
            identity_paths.get("congestion_path") or identity.DEFAULT_CONGESTION_PATH,
        ),
        (
            "legislators_roster",
            identity_paths.get("legislators_path")
            or identity.DEFAULT_LEGISLATORS_PATH,
        ),
        (
            "voteview_members",
            identity_paths.get("voteview_path") or identity.DEFAULT_VOTEVIEW_PATH,
        ),
    ]
    paths.extend(
        (
            f"network_{year}",
            os.path.join(data_root, f"our_network_fullscope_{year}_strict.csv"),
        )
        for year in YEARS
    )
    return paths


class _Intern:
    """Interning table: value -> stable index, emitted once in the payload."""

    def __init__(self):
        self.values = []
        self._index = {}

    def add(self, value):
        key = value
        found = self._index.get(key)
        if found is None:
            found = len(self.values)
            self._index[key] = found
            self.values.append(value)
        return found

    def __len__(self):
        return len(self.values)


def _load_member_profiles(champions_path, resolver):
    """Party and chamber per member, from the champions roster."""
    profiles = {}
    fields = ("bioguide_id", "member_name", "party", "chamber")
    for _row_number, row in industry._iter_csv(champions_path, "champions CSV", fields):
        bioguide = (row["bioguide_id"] or "").strip()
        if not bioguide or bioguide in profiles:
            continue
        record = resolver.resolve(bioguide)
        name = (row["member_name"] or "").strip()
        if not name or name == bioguide:
            name = record["name"]
        profiles[bioguide] = {
            "name": name,
            "party": (row["party"] or "").strip(),
            "chamber": (row["chamber"] or "").strip(),
            "flag": industry._member_flag(record),
        }
    return profiles


def _load_ledger(path):
    fields = (
        "filing_year",
        "filing_uuid",
        "activity_index",
        "filing_date",
        "filing_period",
        "lob_id",
        "client_name",
        "registrant_name",
        "lobbyists",
        "covered_positions",
        "general_issue_code",
        "general_issue_display",
        "government_entities",
        "bill_key",
        "bill_display",
        "channel",
        "note_id",
        "position",
        "position_basis",
    )
    rows = []
    bases = set()
    for row_number, row in industry._iter_csv(path, "interaction ledger", fields):
        year = industry._integer(row["filing_year"], f"ledger row {row_number} year")
        if year not in YEARS:
            raise IngestError(f"ledger row {row_number} has out-of-range year {year}")
        bases.add((row["position_basis"] or "").strip())
        rows.append(row)
    if not rows:
        raise IngestError("interaction ledger is empty")
    return rows, bases


def _load_notes(path):
    notes = {}
    fields = ("note_id", "n_activities", "n_characters", "description")
    for _row_number, row in industry._iter_csv(path, "interaction notes", fields):
        notes[row["note_id"]] = row["description"]
    return notes


def _load_bill_members(path):
    members = defaultdict(list)
    roles = {}
    fields = ("bill_key", "bioguide_id", "role", "sponsorship_date")
    for _row_number, row in industry._iter_csv(path, "bill members", fields):
        bill_key = (row["bill_key"] or "").strip()
        bioguide = (row["bioguide_id"] or "").strip()
        if not bill_key or not bioguide:
            continue
        members[bill_key].append(bioguide)
        roles[(bill_key, bioguide)] = (
            (row["role"] or "").strip(),
            (row["sponsorship_date"] or "").strip(),
        )
    return members, roles


def build_payload(paths_by_label, resolver, data_root):
    organizations = industry._load_org_roster(paths_by_label["fullscope_org_roster"])
    # Keep this unpack in sync with make_lobby_network_html's own call site: bank-tier
    # segregation grew the return from 3 to 5, and this page went unbuildable for a day
    # without anything reporting it, because nothing invoked this builder.
    (
        sector_by_org,
        roster_sectors,
        _join_receipt,
        _tier_by_org,
        _roster_families,
    ) = industry._load_sector_roster(
        paths_by_label["sector_roster"], organizations
    )
    # How far the partial year runs is derived exactly as the industry page derives it:
    # the same loader, the same LD-2 deadline rule against the producer's sweep date, and
    # the minimum across sectors for the page-wide claim.  Never typed into the page.
    spend, _tier_meta, _entity_receipt = industry._load_spend(
        paths_by_label["sector_spend_json"],
        paths_by_label["sector_spend_csv"],
        roster_sectors,
        industry._sweep_as_of(paths_by_label["fullscope_receipts"]),
    )
    through_quarter = industry._page_through_quarter(
        [{"spend": rows} for rows in spend.values()]
    )
    attention, org_attention, _active, _by_sector, _rows = industry._load_network(
        paths_by_label, organizations, sector_by_org
    )
    dyad_signs, dyad_signs_receipt = industry._load_dyad_signs(
        paths_by_label["dyad_signs"]
    )
    profiles = _load_member_profiles(paths_by_label["champions_csv"], resolver)
    ledger, position_bases = _load_ledger(paths_by_label["interaction_ledger"])
    notes = _load_notes(paths_by_label["interaction_notes"])
    bill_members, bill_roles = _load_bill_members(
        paths_by_label["interaction_bill_members"]
    )
    # 2026-08-06: the shared title loader moved to the BILLSTATUS-backed
    # cache (data_root, bill_keys) during the industry family split; feed it
    # every bill this page can render (ledger rows + sponsorship rosters).
    _title_keys = set(bill_members) | {
        (row.get("bill_key") or "").strip()
        for row in ledger
        if (row.get("bill_key") or "").strip()
    }
    titles, _title_receipt = industry._load_bill_titles(data_root, _title_keys)

    orgs = _Intern()
    regs = _Intern()
    lobs = _Intern()
    issues = _Intern()
    bills = _Intern()
    note_text = _Intern()
    # Channel, period, basis and covered-position text repeat on nearly every
    # row -- a covered-position string alone runs to a few hundred characters.
    # Interning them is the difference between a page that fits and one that
    # does not; nothing is dropped, only stored once.
    channels = _Intern()
    periods = _Intern()
    covered_text = _Intern()
    entity_text = _Intern()
    positions = _Intern()

    org_records = []
    org_seen = {}
    acts = []
    bill_keys = []
    dropped_unsectored = 0

    for row in ledger:
        lob_id = (row["lob_id"] or "").strip()
        sector = sector_by_org.get(lob_id)
        if sector is None:
            # Same exclusion the industry page applies: an organization with no
            # unambiguous sector is left out rather than relabelled.
            dropped_unsectored += 1
            continue
        org_index = org_seen.get(lob_id)
        if org_index is None:
            org_index = orgs.add(lob_id)
            org_seen[lob_id] = org_index
            org_records.append(
                {
                    "id": lob_id,
                    "name": organizations[lob_id]["name"]
                    if lob_id in organizations
                    else (row["client_name"] or "").strip(),
                    "sector": sector,
                }
            )
        bill_key = (row["bill_key"] or "").strip()
        bill_index = bills.add(bill_key)
        if bill_index == len(bill_keys):
            bill_keys.append(bill_key)
        lobbyist_indexes = [
            lobs.add(name)
            for name in (row["lobbyists"] or "").split("|")
            if name.strip()
        ]
        entities = [
            value.strip()
            for value in (row["government_entities"] or "").split("|")
            if value.strip()
        ]
        description = notes.get(row["note_id"], "")
        acts.append(
            [
                int(row["filing_year"]),
                (row["filing_date"] or "").strip(),
                org_index,
                regs.add((row["registrant_name"] or "").strip()),
                lobbyist_indexes,
                issues.add(
                    (
                        (row["general_issue_code"] or "").strip(),
                        (row["general_issue_display"] or "").strip(),
                    )
                ),
                bill_index,
                note_text.add(description),
                channels.add((row["channel"] or "").strip()),
                periods.add((row["filing_period"] or "").strip()),
                covered_text.add((row["covered_positions"] or "").strip()),
                entity_text.add("|".join(entities)),
                # Position ships from the ledger, not from this page's opinion.
                positions.add(
                    (
                        (row["position"] or "").strip(),
                        (row["position_basis"] or "").strip(),
                    )
                ),
            ]
        )

    # bill -> activities, and member -> bills. The member view is the composition
    # of the two, resolved in the browser.
    bill_acts = [[] for _ in range(len(bills))]
    for index, act in enumerate(acts):
        bill_acts[act[6]].append(index)

    members = _Intern()
    member_records = []
    member_bills = defaultdict(list)
    member_roles = {}
    for bill_index, bill_key in enumerate(bill_keys):
        if not bill_acts[bill_index]:
            continue
        for bioguide in bill_members.get(bill_key, ()):
            member_index = members.add(bioguide)
            if member_index == len(member_records):
                record = resolver.resolve(bioguide)
                profile = profiles.get(bioguide)
                member_records.append(
                    {
                        "id": bioguide,
                        "name": (profile or {}).get("name") or record["name"],
                        "party": (profile or {}).get("party", ""),
                        "chamber": (profile or {}).get("chamber", ""),
                        "flag": (profile or {}).get("flag")
                        or industry._member_flag(record),
                    }
                )
            member_bills[member_index].append(bill_index)
            role, date = bill_roles.get((bill_key, bioguide), ("", ""))
            if role:
                # sponsor/cosponsor as a flag, not a repeated word
                member_roles[f"{member_index}:{bill_index}"] = [
                    1 if role == "sponsor" else 0,
                    date,
                ]

    bill_records = []
    titled = 0
    for bill_key in bill_keys:
        congress, bill_type, number = bill_key.split(":")
        # _load_bill_titles keys on the canonical "congress:type:number" string,
        # which is exactly what the ledger carries -- no re-derivation.
        title = titles.get(bill_key, "")
        titled += bool(title)
        bill_records.append(
            {
                "k": bill_key,
                "d": industry._bill_display(bill_type, int(number)),
                "c": int(congress),
                "t": title,
            }
        )

    # Sector-level posture, the same computation and the same encoding the
    # industry page badges with -- one PostureTable class, two pages.
    posture_table = industry.PostureTable()
    sector_postures = {}
    for sector, by_member in attention.items():
        for bioguide, by_year in by_member.items():
            member_index = members._index.get(bioguide)
            if member_index is None:
                continue
            first_service_year = resolver.resolve(bioguide)["first_service_year"]
            sector_postures[f"{sector}:{member_index}"] = posture_table.encode(
                [by_year.get(year, 0) for year in YEARS],
                first_service_year=first_service_year,
            )

    org_postures = {}
    for (lob_id, bioguide), by_year in org_attention.items():
        member_index = members._index.get(bioguide)
        org_index = org_seen.get(lob_id)
        if member_index is None or org_index is None:
            continue
        first_service_year = resolver.resolve(bioguide)["first_service_year"]
        org_postures[f"{org_index}:{member_index}"] = posture_table.encode(
            [by_year.get(year, 0) for year in YEARS],
            first_service_year=first_service_year,
            signs=dyad_signs.get((lob_id, bioguide)),
            dyad_key=(lob_id, bioguide),
        )

    sectors = sorted({record["sector"] for record in org_records})
    members_by_sector = defaultdict(set)
    org_sector_index = [record["sector"] for record in org_records]
    for member_index, bill_indexes in member_bills.items():
        for bill_index in bill_indexes:
            for act_index in bill_acts[bill_index]:
                members_by_sector[org_sector_index[acts[act_index][2]]].add(
                    member_index
                )

    posture_receipt = posture_table.receipt()
    posture_receipt["dyad_signs_input"] = dyad_signs_receipt
    receipts = {
        "ledger_rows": len(ledger),
        "rendered_rows": len(acts),
        "dropped_unsectored_rows": dropped_unsectored,
        "organizations": len(org_records),
        "members": len(member_records),
        "bills": len(bill_records),
        "bills_with_a_title": titled,
        "notes": len(note_text),
        "lobbyists": len(lobs),
        "registrants": len(regs),
        "position_bases": sorted(basis for basis in position_bases if basis),
        "posture": posture_receipt,
    }
    payload = {
        "throughQuarter": through_quarter,
        "sectors": sectors,
        "orgs": org_records,
        "regs": regs.values,
        "lobs": lobs.values,
        "issues": [list(item) for item in issues.values],
        "bills": bill_records,
        "notes": note_text.values,
        "channels": channels.values,
        "periods": periods.values,
        "covered": covered_text.values,
        "entities": entity_text.values,
        "positions": [list(item) for item in positions.values],
        "postureStates": posture_table.payload(),
        "acts": acts,
        "billActs": bill_acts,
        "members": member_records,
        "memberBills": {
            str(key): sorted(set(value)) for key, value in member_bills.items()
        },
        "memberRoles": member_roles,
        "membersBySector": {
            sector: sorted(values) for sector, values in members_by_sector.items()
        },
        "sectorPosture": sector_postures,
        "orgPosture": org_postures,
        "postureRule": posture.POSTURE_RULE_NOTE,
        "receipts": receipts,
    }
    return payload, receipts


def _document(payload, hashes_markup, receipts):
    data_json = industry._json_for_script(payload)
    posture_receipt = receipts["posture"]
    posture_coverage_markup = html.escape(posture.coverage_note(posture_receipt))
    style = """
<style>
:root{color-scheme:dark;--bg:#1D1912;--bar:#282113;--panel:#2E2514;--panel-hover:#3B2E16;--text:#F3F3E6;--text-soft:#D2CDBC;--muted:#A49D8A;--faint:#9D9784;--border:#564019;--rule:#433317;--blue:#D2A63C;--blue-bright:#EECD5C;--blue-hover:#EECD5C;--green:#16a34a;--green-text:#7ee787;--red:#dc2626;--red-text:#ff7b72;--amber:#d29922;--amber-text:#e3b341;--radius:6px;--radius-sm:5px;--shadow:none;--fs-label:11px;--fs-body:13px;--fs-card:13px;--fs-section:16px;--fs-page:16px;--accent:#D2A63C;--accent-bright:#EECD5C;--accent-hover:#EECD5C;--ui-display:"Century Gothic","Tw Cen MT",Futura,"Avenir Next","Segoe UI",sans-serif}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;overflow-x:hidden;background:var(--bg);color:var(--text);font:13px/1.45 -apple-system,"Segoe UI",Roboto,sans-serif}
button,input,select{font:inherit}
a{color:inherit}
.skiplink{position:fixed;z-index:100;left:16px;top:-64px;padding:8px 16px;background:var(--blue);color:#1D1912}.skiplink:focus{top:16px}
.product-header{display:flex;flex-direction:column;align-items:stretch;gap:16px;padding:16px max(24px,calc((100vw - 1400px)/2 + 24px)) 0;background:var(--bar);border-bottom:1px solid var(--border)}
.product-title h1{margin:0;font-size:var(--fs-page);font-weight:800;line-height:1.2}.product-title p{margin:8px 0 0;color:var(--text-soft)}
.eyebrow,.field label,.seriesname{font-size:var(--fs-label);font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.tabstrip{display:flex;flex-wrap:wrap;align-items:flex-end;gap:4px;margin-top:8px;border-bottom:1px solid var(--border)}
.tab{position:relative;display:inline-flex;align-items:center;gap:8px;padding:12px 24px;border:1px solid transparent;border-bottom:0;border-radius:var(--radius-sm) var(--radius-sm) 0 0;color:var(--muted);font-size:var(--fs-body);font-weight:700;text-decoration:none;white-space:nowrap;transition:background 140ms ease-out,color 140ms ease-out}
.tab:hover{color:var(--text)}
.tab[aria-current=page]{background:var(--panel);border-color:var(--border);color:#F3F3E6}
.tab[aria-current=page]::after{content:"";position:absolute;left:0;right:0;top:-1px;height:2px;background:var(--accent-bright)}
.tab .tabicon{font-size:var(--fs-label)}
main{width:min(100%,1400px);margin:0 auto;padding:32px 24px 64px}
h2{margin:0;font-size:var(--fs-section);font-weight:750;line-height:1.3}
h3{margin:0 0 8px;font-size:var(--fs-card);font-weight:700;line-height:1.4}
.panel{margin-top:32px;padding:32px;border:1px solid var(--border);border-radius:var(--radius);background:var(--panel);box-shadow:none}
.panelhead{display:flex;align-items:end;justify-content:space-between;gap:16px}
.honesty{margin:16px 0;padding:16px;border:1px solid rgba(227,179,65,.32);border-radius:var(--radius-sm);background:rgba(210,153,34,.08);color:var(--text-soft)}
.honesty p{margin:8px 0}
.methnote{margin:-2px 0 12px;max-width:760px}
.methnote>summary{cursor:pointer;list-style:none;color:var(--blue);font-size:12px;display:inline-flex;align-items:center;gap:5px}
.methnote>summary::-webkit-details-marker{display:none}
.methnote>summary::before{content:'ⓘ';font-size:11px}
.methnote[open]>summary{margin-bottom:6px}
.methbody{font-size:11.5px;color:var(--muted);line-height:1.7;background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);padding:10px 12px}
.methbody p{margin:0}.methbody p+p{margin-top:8px}
.subtle,.receipt,.rowcount{font-size:var(--fs-label);color:var(--muted)}
.num,.stat strong,.rowcount{font-variant-numeric:tabular-nums}
.filters{display:grid;grid-template-columns:minmax(224px,1.4fr) repeat(3,minmax(160px,.7fr)) auto;gap:8px;margin:16px 0}
.field label{display:block;margin:0 0 8px}
.field input,.field select{width:100%;height:40px;padding:8px 16px;border:1px solid var(--border);border-radius:var(--radius-sm);background:#1D1912;color:var(--text)}
.clearbutton{height:40px;padding:8px 16px;border:1px solid var(--border);border-radius:var(--radius-sm);background:#2E2514;color:inherit;cursor:pointer}
.clearbutton:hover{border-color:var(--accent)}
.hero-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));margin:16px 0;border:1px solid var(--border);border-radius:var(--radius);background:var(--panel)}
.stat{display:flex;flex-direction:column;padding:16px;border-right:1px solid var(--rule)}
.stat:last-child{border-right:0}
.stat strong{font-size:var(--fs-section);font-weight:800}
.stat span{margin-top:8px;font-size:var(--fs-label);font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.memberlink{color:var(--accent-bright);text-decoration:none;border-bottom:1px dotted #6B4E1F}
.memberlink:hover{color:#F3F3E6}
.memberflag{display:inline-block;margin-left:8px;padding:0 8px;border:1px solid var(--amber);border-radius:4px;color:var(--amber);font-size:var(--fs-label);font-weight:700;letter-spacing:.06em;text-transform:uppercase;white-space:nowrap}
.memberflag.unresolved{border-color:var(--red-text);color:var(--red-text)}
.postbadge{display:inline-flex;align-items:center;gap:8px;padding:4px 12px;border:1px solid transparent;border-radius:999px;font-size:var(--fs-label);font-weight:700;white-space:nowrap}
.postbadge .pdot{width:8px;height:8px;flex:0 0 8px;border-radius:50%}
.postbadge.cultivating{border-color:rgba(126,231,135,.42);background:rgba(22,163,74,.14);color:var(--green-text)}
.postbadge.cultivating .pdot{background:var(--green-text)}
.postbadge.maintaining{border-color:rgba(88,166,255,.40);background:rgba(31,111,235,.14);color:#58a6ff}
.postbadge.maintaining .pdot{background:#58a6ff}
.postbadge.disengaging{border-color:rgba(255,123,114,.44);background:rgba(220,38,38,.14);color:var(--red-text)}
.postbadge.disengaging .pdot{background:var(--red-text)}
.postbadge.adversarial{border-width:2px;border-style:double}
.postbadge.unknown{border-color:var(--border);background:rgba(139,148,158,.12);color:#8b949e}
.postbadge.unknown .pdot{background:#6e7681}
.postdelta{margin-left:8px;color:var(--muted);font-size:var(--fs-label);font-variant-numeric:tabular-nums}
.toggle{display:flex;width:max-content;overflow:hidden;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg)}
.toggle button{padding:8px 16px;border:0;border-radius:var(--radius-sm);background:transparent;color:var(--text-soft);cursor:pointer;transition:background 140ms ease-out,color 140ms ease-out}
.toggle button.active{background:var(--accent);color:#1D1912}
.member-sector-control{margin:16px 0}.member-sector-control .toggle{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));width:100%}
.yearblock{margin-top:32px;border:1px solid var(--rule);border-radius:var(--radius-sm)}
.yearhead{display:flex;align-items:baseline;justify-content:space-between;gap:16px;padding:16px 24px;border-bottom:1px solid var(--rule);background:#2E2514}
.yearhead h3{margin:0;font-size:var(--fs-section)}
.yearhead .partial{color:var(--amber-text);font-size:var(--fs-label);font-weight:700}
.interaction{padding:24px;border-bottom:1px solid var(--rule)}
.interaction:last-child{border-bottom:0}
.interaction:hover{background:rgba(238,205,92,.045)}
.imeta{display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px;margin-bottom:16px}
.idate{font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap}
.iorg{font-weight:700;color:#F3F3E6}
.ibill{display:inline-flex;align-items:center;gap:8px;padding:4px 12px;border:1px solid rgba(238,205,92,.4);border-radius:999px;background:rgba(210,166,60,.12);color:var(--accent-bright);font-weight:700;white-space:nowrap}
.ichannel,.iissue{padding:0 8px;border:1px solid var(--border);border-radius:4px;color:var(--muted);font-size:var(--fs-label)}
.irole{padding:0 8px;border:1px solid rgba(238,205,92,.4);border-radius:4px;color:var(--blue-bright);font-size:var(--fs-label);font-weight:700;text-transform:uppercase;letter-spacing:.06em}
.igrid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.6fr);gap:24px}
.ifield{min-width:0}
.ifield .eyebrow{display:block;margin-bottom:8px}
.ilobbyists{margin:0;padding:0;list-style:none}
.ilobbyists li{padding:2px 0;color:var(--text-soft)}
.iregistrant{color:var(--text-soft)}
.iposition{display:inline-flex;align-items:center;gap:8px;padding:4px 12px;border:1px dashed var(--border);border-radius:var(--radius-sm);color:var(--muted);font-weight:700}
.inferred{margin-left:8px;padding:2px 8px;border:1px dashed var(--amber);border-radius:4px;color:var(--amber-text);font-size:var(--fs-label);font-weight:700;letter-spacing:.06em;text-transform:uppercase;cursor:help}
.inotes{margin:0;padding:16px;border-left:2px solid var(--blue);border-radius:0 var(--radius-sm) var(--radius-sm) 0;background:#282113;color:var(--text-soft);font-size:var(--fs-body);line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere}
.empty{padding:32px 16px;border:1px dashed var(--border);border-radius:var(--radius-sm);color:var(--muted);text-align:center}
.hidden{display:none!important}
.picker{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr));gap:16px;margin-top:16px}
.pickcard{display:flex;flex-direction:column;gap:8px;padding:24px;border:1px solid var(--border);border-radius:var(--radius);background:var(--panel);color:var(--text);text-align:left;text-decoration:none;cursor:pointer;transition:background 140ms ease-out,border-color 140ms ease-out,transform 140ms ease-out}
.pickcard:hover{background:var(--panel-hover);border-color:var(--accent);transform:translateY(-2px)}
.pickname{font-size:var(--fs-card);font-weight:700;color:#F3F3E6}
footer{margin-top:32px;padding-top:32px;border-top:1px solid var(--border);color:var(--muted);font-size:var(--fs-label)}
footer h3{font-size:var(--fs-card)}
.hashes{overflow:auto;padding:16px;border:1px solid var(--border);border-radius:var(--radius-sm);background:#1D1912;color:var(--text-soft);white-space:pre-wrap;word-break:break-all}
.hashes li{display:grid;grid-template-columns:minmax(208px,.8fr) minmax(384px,1.2fr);gap:16px;padding:8px 0}
:where(button,select,input,a):focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}*,*::before,*::after{animation-duration:1ms!important;transition-duration:1ms!important}}
@media(max-width:900px){main{padding:32px 16px 48px}.panel{padding:24px}.filters{grid-template-columns:repeat(2,minmax(0,1fr))}.member-sector-control .toggle{grid-template-columns:repeat(2,minmax(0,1fr))}.igrid{grid-template-columns:1fr}.hero-stats{grid-template-columns:1fr}.stat{border-right:0;border-bottom:1px solid var(--rule)}.stat:last-child{border-bottom:0}}
</style>
<style>
/* Moneyball dashboard skin: dense 13px type, compact controls, and one chrome accent. */
body{font:13px/1.45 -apple-system,"Segoe UI",Roboto,sans-serif}
.skiplink{left:16px;top:-64px;padding:6px 12px}.skiplink:focus{top:10px}
.product-header{flex-direction:column;align-items:stretch;gap:8px;padding:10px 16px 0}.product-title{min-width:0;flex:1}.product-title h1{margin:0;font-size:16px;font-weight:700;line-height:1.45;letter-spacing:.2px}.product-title p{margin:2px 0 0;color:var(--muted);font-size:12px}
.eyebrow,.field label{font-size:11px;font-weight:600;letter-spacing:0;text-transform:none;color:var(--muted)}
.tabstrip{gap:4px;margin-top:0}.tab{gap:6px;padding:6px 12px;border-radius:var(--radius) var(--radius) 0 0;font-size:12px;font-weight:600;transition:background 140ms ease-out,color 140ms ease-out}.tab:hover{background:var(--panel-hover);color:#F3F3E6}.tab[aria-current=page]:hover{background:var(--panel)}.tab .tabicon{font-size:11px}
main{width:min(100%,1400px);padding:16px 16px 40px}h2{margin:0;font-size:16px;font-weight:700;line-height:1.45}h3{margin:0 0 6px;font-size:13px;font-weight:700;line-height:1.45}.panel{margin-top:16px;padding:14px;border-radius:var(--radius);box-shadow:none}.panelhead{gap:10px}.honesty{margin:10px 0;padding:10px}.honesty p{margin:4px 0}.subtle,.receipt,.rowcount{font-size:11px}
.filters{gap:8px;margin:10px 0}.field label{margin:0 0 4px}.field input,.field select{height:auto;min-height:31px;padding:6px 9px;border-radius:var(--radius);font-size:13px}.clearbutton{height:auto;min-height:31px;padding:6px 11px;border-radius:var(--radius);font-size:12px;transition:background 140ms ease-out,border-color 140ms ease-out,color 140ms ease-out}.clearbutton:hover{border-color:var(--accent);background:var(--panel-hover);color:#F3F3E6}
.hero-stats{margin:10px 0;border-radius:var(--radius)}.stat{padding:10px 12px}.stat strong{font-size:16px;font-weight:700}.stat span{margin-top:2px;font-size:11px;font-weight:600;letter-spacing:0;text-transform:none}
.memberlink{color:inherit;text-decoration:none;border-bottom:1px dotted #6B4E1F}.memberlink:hover{color:#F3F3E6;border-bottom-color:var(--accent-bright)}.memberflag{margin-left:6px;padding:1px 6px;border-radius:var(--radius-sm);font-size:11px;letter-spacing:0;text-transform:none}
.postbadge{gap:4px;padding:1px 6px;border-radius:var(--radius-sm);font-size:11px;font-weight:600}.postbadge .pdot{width:7px;height:7px;flex-basis:7px}.postdelta{margin-left:4px;font-size:11px}
.toggle{border-radius:var(--radius)}.toggle button{padding:6px 12px;font-size:12px;transition:background 140ms ease-out,color 140ms ease-out}.toggle button:hover{background:var(--panel-hover);color:#F3F3E6}.toggle button.active{background:var(--accent);color:#1D1912;font-weight:600}.toggle button.active:hover{background:var(--accent-hover)}
.yearblock{margin-top:16px;border-radius:var(--radius)}.yearhead{gap:10px;padding:8px 12px}.yearhead h3{margin:0;font-size:13px}.yearhead .partial{font-size:11px}.interaction{padding:12px 14px}.interaction:hover{background:var(--panel-hover)}.imeta{gap:6px 10px;margin-bottom:8px}.idate{font-weight:700}.ibill{gap:4px;padding:1px 6px;border-radius:var(--radius-sm);font-size:11px;font-weight:600}.ichannel,.iissue{padding:1px 6px;border-radius:var(--radius-sm);font-size:11px}.irole{padding:1px 6px;border:1px solid rgba(238,205,92,.40);border-radius:var(--radius-sm);color:var(--accent-bright);font-size:11px;font-weight:600;text-transform:none;letter-spacing:0}
.igrid{gap:14px}.ifield .eyebrow{margin-bottom:4px}.iposition{gap:4px;padding:1px 6px;border-radius:var(--radius-sm);font-size:11px}.inferred{margin-left:6px;padding:1px 6px;border-radius:var(--radius-sm);font-size:11px;letter-spacing:0;text-transform:none}.inotes{padding:8px 10px;border-radius:0 var(--radius-sm) var(--radius-sm) 0;font-size:13px;line-height:1.45}.empty{padding:16px 12px}.picker{gap:10px;margin-top:10px}.pickcard{gap:4px;padding:12px;border-radius:var(--radius);transition:background 140ms ease-out,border-color 140ms ease-out}.pickcard:hover{background:var(--panel-hover);border-color:var(--accent);transform:none}.pickname{font-size:13px}
footer{margin-top:16px;padding-top:16px;font-size:11px}footer h3{font-size:13px}.hashes{padding:10px}.hashes li{gap:10px;padding:4px 0}:where(button,select,input,a):focus-visible{outline:2px solid var(--accent);outline-offset:1px}@media(max-width:900px){main{padding:16px 12px 32px}.panel{padding:12px}}
.product-title h1,.eyebrow,h2:not(#member-title){font-family:var(--ui-display);text-transform:uppercase}.product-title h1{letter-spacing:.16em;display:flex;align-items:center;gap:6px}.product-title .brand-glyph{flex:0 0 auto}.eyebrow,h2:not(#member-title){letter-spacing:.14em}

/* ---- Melange compact shell (ratified 2026-08-24): one 48px sticky bar; the family nav folds
   under it; the per-page subtitle is demoted (string stays in the DOM). All chrome under the
   Assertion Test: constant across its class, encodes no payload field. ---- */
.product-header{flex-direction:row;align-items:center;gap:18px;height:48px;padding:0 max(20px,calc((100vw - 1400px)/2 + 20px));border-bottom:1px solid var(--border);position:sticky;top:0;z-index:40;background:rgba(40,33,19,.88);-webkit-backdrop-filter:blur(10px) saturate(1.08);backdrop-filter:blur(10px) saturate(1.08)}
.product-titlerow{gap:10px;flex:0 0 auto;align-items:center}
.product-title h1{font-size:13px;letter-spacing:.18em;margin:0}
.product-title p{display:none}
.sitenav{flex:0 0 auto;margin-left:auto}
.desklink{padding:5px 11px;font-size:11px}
.tabstrip{margin-top:0;gap:2px;border-bottom:0;flex:1 1 auto;flex-wrap:nowrap;overflow:visible}
.tab{padding:6px 12px;font-size:12px;border-radius:5px;border-bottom:1px solid transparent}
.tab[aria-current=page]::after{display:none}
.tab[aria-current=page]{background:var(--panel-hover)}
.family-nav{position:absolute;left:max(20px,calc((100vw - 1400px)/2 + 20px));top:48px;margin:0;padding:5px 0;gap:2px;background:transparent}
.family-nav .tab{padding:3px 9px;font-size:11px}
main{padding-top:44px}
section[id],footer[id]{scroll-margin-top:96px}
/* LAYOUT BUG REPAIR, not a redesign: .hero/.hero-stats are display:grid but declare
   grid-template-columns ONLY inside @media(max-width:900px), so above 900px the three
   coverage figures stack (.stat's border-right proves the row was the intent). Measured:
   hero 262px -> 67px, first data section 443px -> 159px. NO thead offset anywhere here:
   every .tablewrap{overflow:auto} is its own scroll container, so the shipped thead top:0
   is already correct - offsetting it floats the header INTO the table over the first row. */
@media(min-width:901px){.hero{grid-template-columns:minmax(0,1fr) minmax(0,auto)}.hero-stats{grid-template-columns:repeat(3,minmax(0,1fr))}}
</style>
"""
    body = f"""
</head><body><a class=skiplink href=#main>Skip to content</a>
<header class=product-header><div class=product-title><div class=eyebrow>Melange — Industries</div><h1><svg class=brand-glyph width="19" height="31" viewBox="0 0 128 208" aria-hidden="true"><path d="M108 20 L64 64 L28 104 L64 144 L20 188 M64 64 L100 104 L64 144" fill="none" stroke="#EECD5C" stroke-width="19" stroke-linejoin="miter" stroke-miterlimit="6"/></svg>Filings behind the number</h1><p>Every disclosed lobbying activity that cited a bill this member sponsored or cosponsored · 2021–2026</p></div>
<nav class=tabstrip aria-label="Melange surfaces"><a class=tab href="Moneyball Dashboard.html"><span class=tabicon aria-hidden=true>◧</span>Main dashboard</a><a class=tab href="cu-member-desk.html"><span class=tabicon aria-hidden=true>▤</span>Member desk</a><a class=tab href="lobby-network.html"><span class=tabicon aria-hidden=true>◈</span>Industry</a><a class=tab aria-current=page href="lobby-interactions.html"><span class=tabicon aria-hidden=true>▦</span>Filings</a></nav></header>
<main id=main>
<div class=honesty><p>{html.escape(INTERACTION_NOTE)}</p></div><details class=methnote><summary>How to read this (methodology)</summary><div class=methbody><p>{html.escape(POSITION_NOTE)}</p><p><b>Inferred positions.</b> {html.escape(INFERRED_POSITION_NOTE)}</p><p>{html.escape(FIVE_CLASS_NOTE)}</p><p>{html.escape(COVERAGE_NOTE)}</p><p>{html.escape(NOTE_AUTHORSHIP_NOTE)}</p><p><b>Posture.</b> {html.escape(posture.POSTURE_HONESTY_NOTE)}</p><p>{html.escape(posture.POSTURE_RULE_NOTE)}</p><p>{posture_coverage_markup}</p></div></details>

<section class=panel id=picker-panel aria-labelledby=picker-title><div class=panelhead><div><div class=eyebrow>Choose a member</div><h2 id=picker-title>Members this industry lobbies</h2><div class=subtle id=picker-subtitle></div></div><div><span class=eyebrow>Industry</span><br><select id=sector-picker></select></div></div>
<div class=filters aria-label="Member filters"><div class=field><label for=member-search>Member name</label><input id=member-search type=search placeholder="Try Barr or Comer" autocomplete=off></div></div>
<p id=picker-count class=rowcount aria-live=polite></p>
<div class=picker id=picker-list></div></section>

<section class="panel hidden" id=member-panel aria-labelledby=member-title><div class=panelhead><div><div class=eyebrow id=member-eyebrow>member</div><h2 id=member-title></h2><div class=subtle id=member-subtitle></div></div><div style="display:flex;gap:16px;align-items:flex-end"><div><span class=eyebrow>Posture wording</span><div class=toggle aria-label="Posture wording"><button type=button data-posture-mode=posture class=active aria-pressed=true>Cultivating / Maintaining / Disengaging / Adversarial</button><button type=button data-posture-mode=trend aria-pressed=false>Rising / Steady / Falling / Adversarial</button></div></div><button type=button id=close-member class=clearbutton>← All members</button></div></div>
<div class=member-sector-control><span class=eyebrow id=member-sector-label>Industry</span><div class=toggle id=member-sector-tabs role=group aria-labelledby=member-sector-label></div></div>
<div id=member-posture style="margin:16px 0"></div>
<div class=hero-stats aria-label="Interaction summary"><div class=stat><strong id=stat-filings>0</strong><span>interactions</span></div><div class=stat><strong id=stat-orgs>0</strong><span>organizations</span></div><div class=stat><strong id=stat-bills>0</strong><span>bills</span></div><div class=stat><strong id=stat-lobbyists>0</strong><span>named lobbyists</span></div><div class=stat><strong id=stat-years>—</strong><span>years active</span></div><div class=stat><strong id=stat-directional>—</strong><span>inferred support / oppose</span></div></div>
<div class=filters aria-label="Interaction filters"><div class=field><label for=text-search>Search bill, note, or lobbyist</label><input id=text-search type=search placeholder="Try interchange, or a lobbyist's name" autocomplete=off></div><div class=field><label for=org-filter>Organization</label><select id=org-filter><option value="">All organizations</option></select></div><div class=field><label for=year-filter>Year</label><select id=year-filter><option value="">All years</option></select></div><div class=field><label for=issue-filter>Issue area</label><select id=issue-filter><option value="">All issue areas</option></select></div><div class=field><label for=position-filter>Position (best guess)</label><select id=position-filter><option value="">All positions</option><option value=directional>Support or Oppose</option><option value=Support>Support</option><option value=Oppose>Oppose</option><option value=Amend>Amend</option><option value=Monitor>Monitor</option><option value=Engage>Not specified</option><option value=none>Not classified</option></select></div><button type=button id=clear-filters class=clearbutton>Clear filters</button></div>
<p id=interaction-count class=rowcount aria-live=polite></p>
<div id=interaction-list></div></section>

<footer id=receipts><details class=methnote><summary>Where these numbers come from</summary><div class=methbody><p>Interaction ledger: {receipts['rendered_rows']:,} of {receipts['ledger_rows']:,} ledger rows rendered across {receipts['organizations']:,} organizations, {receipts['members']:,} members and {receipts['bills']:,} bills; {receipts['dropped_unsectored_rows']:,} rows belong to organizations with no unambiguous sector and follow the industry page's exclusion rather than being relabelled. {receipts['notes']:,} distinct filing descriptions, {receipts['lobbyists']:,} named lobbyists and {receipts['registrants']:,} registrants are interned once each and referenced by index.</p><p>Position basis across every rendered row: {html.escape(", ".join(receipts['position_bases']) or "none")}. The lobbying filing has no place to state a position, so the column is wired and deliberately empty.</p><p>Posture override: {posture_receipt['sign_override']['signed_dyads']:,} of {posture_receipt['sign_override']['dyads']:,} organization–member pairs have signed evidence; {posture_receipt['sign_override']['adversarial_dyads']:,} render as Adversarial under the oppose_n &gt;= 2 corroboration floor.</p><h3>Input SHA-256</h3><ul class=hashes>{hashes_markup}</ul></div></details></footer>
<script id=page-data type=application/json>{data_json}</script>
"""
    script = r"""
<script>
"use strict";
const DATA=JSON.parse(document.getElementById("page-data").textContent);
const EM_DASH="—";
const esc=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const number=value=>Number(value).toLocaleString("en-US");
const DESK_FILE="cu-member-desk.html";
const CHANNEL_LABELS=__CHANNEL_LABELS__;
const PERIOD_LABELS=__PERIOD_LABELS__;
const POSITION_LABEL=__POSITION_LABEL__;
const INFERRED_TIP=__INFERRED_TIP__;
// The FIVE-CLASS label is what ships, because it is the useful one: "amend" and
// "monitor" are different postures toward a bill and collapsing them to "engage"
// throws away the distinction a reader actually wants. An earlier build showed
// only the collapse, justified by an Amend F1 of 0.00 -- that number came from
// scoring Amend against Kim's gnn annotator, whose label space contains no Amend
// at all. It measured the annotator, not the class.
const THREE_CLASS={Support:"Support",Oppose:"Oppose",Amend:"Engage",Monitor:"Engage",Engage:"Engage",Mention:"Engage"};
// Directional reads carry the strongest claim and the weakest evidence, so they
// are coloured; the non-directional refinements stay neutral. Every one of them
// wears the "inferred" tag -- no badge here may look like something the filer said.
const POSITION_CLASS={Support:"cultivating",Oppose:"disengaging",Amend:"maintaining",Monitor:"unknown",Engage:"unknown",Mention:"unknown"};
const displaySector=value=>String(value||"unclassified").replaceAll("_"," ").split(" ").map(w=>w?w[0].toUpperCase()+w.slice(1):w).join(" ");
// Activity tuple layout, mirrored from the Python builder.
const A_YEAR=0,A_DATE=1,A_ORG=2,A_REG=3,A_LOBS=4,A_ISSUE=5,A_BILL=6,A_NOTE=7,A_CHANNEL=8,A_PERIOD=9,A_COVERED=10,A_ENTITIES=11,A_POSITION=12;
let currentSector=DATA.sectors[0]||"";
let currentMember=null;
let postureMode="posture";
const MEMBER_FLAG_LABELS={former:"Former",unresolved:"Name unresolved"};
function memberFlag(flag){
  if(!flag)return "";
  const label=MEMBER_FLAG_LABELS[flag]||flag;
  const title=flag==="former"?"No longer serving in Congress":"No roster or Voteview record names this bioguide";
  return `<span class="memberflag ${esc(flag)}" title="${esc(title)}">${esc(label)}</span>`;
}
// A posture arrives as [stateIndex, recent, prior, partial, delta]. The state
// decision was made in Python; this only re-renders the sentence that describes
// it, from the same numbers, so the page ships one copy of the words instead of
// thirteen thousand.
const P_STATE=0,P_RECENT=1,P_PRIOR=2,P_PARTIAL=3,P_DELTA=4;
const plural=(count,word)=>`${count} ${word}${count===1?"":"s"}`;
function postureDetail(verdict){
  const state=DATA.postureStates[verdict[P_STATE]];
  return `2024–2025: ${plural(verdict[P_RECENT],"bill")} vs 2022–2023: ${plural(verdict[P_PRIOR],"bill")} (${verdict[P_DELTA]}); 2026 partial year: ${verdict[P_PARTIAL]}. ${state.m}`;
}
function postureBadge(verdict){
  if(!verdict)return "";
  const state=DATA.postureStates[verdict[P_STATE]];
  const label=postureMode==="posture"?state.p:state.t;
  return `<span class="postbadge ${esc(state.b)} ${esc(state.n)}" title="${esc(postureDetail(verdict))}"><span class=pdot aria-hidden=true></span>${esc(label)}</span><span class=postdelta>${esc(verdict[P_DELTA])}</span>`;
}
// Member -> bills -> activities. The join is composed here rather than shipped,
// because materializing it is about twenty times the size of its inputs.
function memberActs(memberIndex,sector){
  const bills=DATA.memberBills[String(memberIndex)]||[],out=[];
  for(const billIndex of bills){
    for(const actIndex of (DATA.billActs[billIndex]||[])){
      if(sector&&DATA.orgs[DATA.acts[actIndex][A_ORG]].sector!==sector)continue;
      out.push(actIndex);
    }
  }
  // newest first inside a year; the year blocks themselves run newest to oldest
  out.sort((a,b)=>{const x=DATA.acts[a],y=DATA.acts[b];return y[A_YEAR]-x[A_YEAR]||String(y[A_DATE]).localeCompare(String(x[A_DATE]))||DATA.orgs[x[A_ORG]].name.localeCompare(DATA.orgs[y[A_ORG]].name);});
  return out;
}
function sectorMembers(sector){
  return (DATA.membersBySector[sector]||[]).slice().sort((a,b)=>{
    const ca=memberActs(a,sector).length,cb=memberActs(b,sector).length;
    return cb-ca||DATA.members[a].name.localeCompare(DATA.members[b].name);
  });
}
function renderPicker(){
  const query=document.getElementById("member-search").value.trim().toLocaleLowerCase();
  const members=sectorMembers(currentSector).filter(index=>!query||DATA.members[index].name.toLocaleLowerCase().includes(query)||DATA.members[index].id.toLocaleLowerCase().includes(query));
  document.getElementById("picker-subtitle").textContent=`${displaySector(currentSector)} · every member named in at least one filing`;
  document.getElementById("picker-count").textContent=`${number(members.length)} ${members.length===1?"member":"members"}`;
  document.getElementById("picker-list").innerHTML=members.length?members.map(index=>{
    const member=DATA.members[index],count=memberActs(index,currentSector).length;
    const verdict=DATA.sectorPosture[`${currentSector}:${index}`];
    return `<button type=button class=pickcard data-member="${index}"><span class=pickname>${esc(member.name)}${memberFlag(member.flag)}</span><span class=subtle>${esc(member.party||EM_DASH)} · ${esc(member.chamber||EM_DASH)} · ${esc(member.id)}</span><span>${postureBadge(verdict)}</span><span class=subtle>${number(count)} ${count===1?"interaction":"interactions"}</span></button>`;
  }).join(""):`<div class=empty><b>No member matches that search.</b><br>Clear the search to see every member this industry worked.</div>`;
}
function visibleActs(){
  if(currentMember===null)return [];
  const query=document.getElementById("text-search").value.trim().toLocaleLowerCase();
  const org=document.getElementById("org-filter").value,year=document.getElementById("year-filter").value,issue=document.getElementById("issue-filter").value;
  const position=document.getElementById("position-filter").value;
  return memberActs(currentMember,currentSector).filter(index=>{
    const act=DATA.acts[index];
    if(org&&String(act[A_ORG])!==org)return false;
    if(year&&String(act[A_YEAR])!==year)return false;
    if(issue&&String(act[A_ISSUE])!==issue)return false;
    if(position){
      // filter on the five-class label, which is what the badge shows
      const label=(DATA.positions[act[A_POSITION]]||["",""])[0];
      if(position==="none"){if(label)return false;}
      else if(position==="directional"){if(label!=="Support"&&label!=="Oppose")return false;}
      else if(label!==position)return false;
    }
    if(!query)return true;
    const bill=DATA.bills[act[A_BILL]],lobbyists=act[A_LOBS].map(i=>DATA.lobs[i]).join(" ");
    return `${bill.d} ${bill.t} ${DATA.notes[act[A_NOTE]]} ${lobbyists} ${DATA.orgs[act[A_ORG]].name} ${DATA.regs[act[A_REG]]}`.toLocaleLowerCase().includes(query);
  });
}
function interactionMarkup(actIndex){
  const act=DATA.acts[actIndex],bill=DATA.bills[act[A_BILL]],org=DATA.orgs[act[A_ORG]];
  const lobbyists=act[A_LOBS].map(i=>DATA.lobs[i]);
  const issue=DATA.issues[act[A_ISSUE]];
  const role=DATA.memberRoles[`${currentMember}:${act[A_BILL]}`];
  const periodKey=DATA.periods[act[A_PERIOD]]||"";
  const period=PERIOD_LABELS[periodKey]||periodKey;
  const channelKey=DATA.channels[act[A_CHANNEL]]||"";
  const covered=DATA.covered[act[A_COVERED]]||"";
  const entities=DATA.entities[act[A_ENTITIES]]||"";
  const [positionText,positionBasis]=DATA.positions[act[A_POSITION]]||["",""];
  const orgVerdict=DATA.orgPosture[`${act[A_ORG]}:${currentMember}`];
  // Position comes from the ledger. It is empty for every row in this corpus
  // because the LD-2 form has no position field; the label says so rather than
  // leaving a blank cell that reads like missing data.
  const collapsed=THREE_CLASS[positionText]||"";
  const position=positionText
    ? `<span class="postbadge ${POSITION_CLASS[positionText]||"unknown"}" title="${esc('Collapses to "'+collapsed+'" in the three-class view. '+INFERRED_TIP)}"><span class=pdot aria-hidden=true></span>${esc(positionText)}</span><span class=inferred title="${esc(positionBasis)}">inferred</span>`
    : `<span class=iposition title="The lobbying filing has no place to state a position, so no filing in this corpus states one.">${esc(POSITION_LABEL)}</span>`;
  return `<article class=interaction>
<div class=imeta><span class=idate>${esc(act[A_DATE]||EM_DASH)}</span>${period?`<span class=ichannel>${esc(period)}</span>`:""}<span class=iorg>${esc(org.name)}</span>${orgVerdict?postureBadge(orgVerdict):""}<span class=ibill title="${esc(bill.t||"Title unavailable")}">${esc(bill.d)}</span>${role?`<span class=irole>${role[0]===1?"Sponsor":"Cosponsor"}${role[1]?` ${esc(role[1])}`:""}</span>`:""}<span class=iissue title="${esc(issue[1]||"")}">${esc(issue[0]||EM_DASH)}</span><span class=ichannel>${esc(CHANNEL_LABELS[channelKey]||channelKey)}</span></div>
${bill.t?`<p class=subtle style="margin:0 0 16px">${esc(bill.d)} · ${esc(bill.t)}</p>`:""}
<div class=igrid>
<div class=ifield><span class=eyebrow>Filed by</span><div class=iregistrant>${esc(DATA.regs[act[A_REG]]||EM_DASH)}</div>
<span class=eyebrow style="display:block;margin:16px 0 8px">Lobbyists named</span>${lobbyists.length?`<ul class=ilobbyists>${lobbyists.map(name=>`<li>${esc(name)}</li>`).join("")}</ul>`:`<div class=subtle>No individual lobbyist named on this activity.</div>`}
${covered?`<span class=eyebrow style="display:block;margin:16px 0 8px">Government jobs held</span><div class=subtle>${esc(covered)}</div>`:""}
${entities?`<span class=eyebrow style="display:block;margin:16px 0 8px">Agencies contacted</span><div class=subtle>${esc(entities.split("|").join(", "))}</div>`:""}
<span class=eyebrow style="display:block;margin:16px 0 8px">Position taken</span>${position}</div>
<div class=ifield><span class=eyebrow>What the lobbyist wrote</span><p class=inotes>${esc(DATA.notes[act[A_NOTE]]||"No description filed.")}</p></div>
</div></article>`;
}
function renderInteractions(){
  const acts=visibleActs(),byYear=new Map();
  for(const index of acts){const year=DATA.acts[index][A_YEAR];if(!byYear.has(year))byYear.set(year,[]);byYear.get(year).push(index);}
  const years=[...byYear.keys()].sort((a,b)=>b-a);
  document.getElementById("interaction-count").textContent=`${number(acts.length)} ${acts.length===1?"interaction":"interactions"} across ${number(years.length)} ${years.length===1?"year":"years"}`;
  const orgs=new Set(),bills=new Set(),lobbyists=new Set();
  for(const index of acts){const act=DATA.acts[index];orgs.add(act[A_ORG]);bills.add(act[A_BILL]);act[A_LOBS].forEach(i=>lobbyists.add(i));}
  document.getElementById("stat-filings").textContent=number(acts.length);
  document.getElementById("stat-orgs").textContent=number(orgs.size);
  document.getElementById("stat-bills").textContent=number(bills.size);
  document.getElementById("stat-lobbyists").textContent=number(lobbyists.size);
  document.getElementById("stat-years").textContent=years.length?`${Math.min(...years)}–${Math.max(...years)}`:EM_DASH;
  // Directional reads are the weak classes, so the tile counts them rather than
  // implying the whole corpus has a stance; zero here usually means "nobody wrote
  // anything a classifier could read as a position", not "nobody had one".
  let support=0,oppose=0,classified=0;
  for(const index of acts){const label=THREE_CLASS[(DATA.positions[DATA.acts[index][A_POSITION]]||["",""])[0]]||"";if(label)classified++;if(label==="Support")support++;else if(label==="Oppose")oppose++;}
  const dirEl=document.getElementById("stat-directional");
  dirEl.textContent=classified?`${number(support)} / ${number(oppose)}`:EM_DASH;
  dirEl.title=classified?`${number(classified)} of ${number(acts.length)} shown interactions carry an inferred position. Support and Oppose are the classifier's weakest classes (F1 0.58 and 0.53).`:"No interaction in this view carries an inferred position.";
  document.getElementById("interaction-list").innerHTML=acts.length?years.map(year=>{
    const rows=byYear.get(year);
    return `<section class=yearblock><div class=yearhead><h3>${year}${year===2026?` <span class=partial>partial year through ${DATA.throughQuarter}</span>`:""}</h3><span class=rowcount>${number(rows.length)} ${rows.length===1?"interaction":"interactions"}</span></div>${rows.map(interactionMarkup).join("")}</section>`;
  }).join(""):`<div class=empty><b>No interaction matches these filters.</b><br>Clear a filter to see every filing behind this member's count.</div>`;
}
function populateFilters(){
  if(currentMember===null)return;
  const acts=memberActs(currentMember,currentSector);
  const orgs=[...new Set(acts.map(i=>DATA.acts[i][A_ORG]))].sort((a,b)=>DATA.orgs[a].name.localeCompare(DATA.orgs[b].name));
  const years=[...new Set(acts.map(i=>DATA.acts[i][A_YEAR]))].sort((a,b)=>b-a);
  const issues=[...new Set(acts.map(i=>DATA.acts[i][A_ISSUE]))].sort((a,b)=>String(DATA.issues[a][0]).localeCompare(String(DATA.issues[b][0])));
  document.getElementById("org-filter").innerHTML=`<option value="">All organizations</option>`+orgs.map(i=>`<option value="${i}">${esc(DATA.orgs[i].name)}</option>`).join("");
  document.getElementById("year-filter").innerHTML=`<option value="">All years</option>`+years.map(y=>`<option value="${y}">${y}${y===2026?" (partial)":""}</option>`).join("");
  document.getElementById("issue-filter").innerHTML=`<option value="">All issue areas</option>`+issues.map(i=>`<option value="${i}">${esc(DATA.issues[i][0])} · ${esc(DATA.issues[i][1])}</option>`).join("");
}
function renderMemberSector(){
  if(currentMember===null)return;
  document.getElementById("member-eyebrow").textContent=`${displaySector(currentSector)}`;
  const verdict=DATA.sectorPosture[`${currentSector}:${currentMember}`];
  document.getElementById("member-posture").innerHTML=verdict?`${postureBadge(verdict)} <span class=subtle>${esc(postureDetail(verdict))}</span>`:"";
  document.getElementById("member-sector-tabs").innerHTML=DATA.sectors.map(sector=>{
    const count=memberActs(currentMember,sector).length,active=sector===currentSector;
    return `<button type=button data-member-sector="${esc(sector)}"${active?" class=active":""} aria-pressed="${active}">${esc(displaySector(sector))} · ${number(count)} ${count===1?"interaction":"interactions"}</button>`;
  }).join("");
}
function switchMemberSector(sector,push=true){
  if(currentMember===null||!DATA.sectors.includes(sector)||sector===currentSector)return;
  currentSector=sector;
  document.getElementById("sector-picker").value=currentSector;
  // Clear sector-scoped state before rebuilding controls or rendering the new sector.
  document.getElementById("position-filter").value="";
  populateFilters();
  renderMemberSector();
  if(push){const member=DATA.members[currentMember],url=`?sector=${encodeURIComponent(currentSector)}&b=${encodeURIComponent(member.id)}`;history.pushState({member:currentMember,sector:currentSector},"",url);}
  renderInteractions();
}
function openMember(memberIndex,push=true){
  currentMember=memberIndex;
  const member=DATA.members[memberIndex];
  document.getElementById("member-title").innerHTML=`${esc(member.name)}${memberFlag(member.flag)}`;
  // A former member has no current desk profile, so the name stays plain text
  // rather than becoming a link that lands nowhere.
  const deskLink=member.flag?"":` · <a class=memberlink href="${DESK_FILE}?m=${encodeURIComponent(member.name)}">Open in the member desk</a>`;
  document.getElementById("member-subtitle").innerHTML=`${esc(member.party||EM_DASH)} · ${esc(member.chamber||EM_DASH)} · ${esc(member.id)}${deskLink}`;
  document.getElementById("picker-panel").classList.add("hidden");
  document.getElementById("member-panel").classList.remove("hidden");
  document.getElementById("text-search").value="";
  document.getElementById("position-filter").value="";
  populateFilters();
  renderMemberSector();
  renderInteractions();
  if(push){const url=`?sector=${encodeURIComponent(currentSector)}&b=${encodeURIComponent(member.id)}`;history.pushState({member:memberIndex,sector:currentSector},"",url);}
  window.scrollTo({top:0,behavior:"smooth"});
}
function closeMember(push=true){
  currentMember=null;
  document.getElementById("member-panel").classList.add("hidden");
  document.getElementById("picker-panel").classList.remove("hidden");
  renderPicker();
  if(push)history.pushState({sector:currentSector},"",`?sector=${encodeURIComponent(currentSector)}`);
}
function applyLocation(push){
  const params=new URLSearchParams(window.location.search);
  const sector=params.get("sector");
  if(sector&&DATA.sectors.includes(sector))currentSector=sector;
  document.getElementById("sector-picker").value=currentSector;
  const bioguide=params.get("b"),name=params.get("m");
  let index=null;
  if(bioguide)index=DATA.members.findIndex(member=>member.id===bioguide);
  else if(name)index=DATA.members.findIndex(member=>member.name===name);
  if(index!==null&&index>=0)openMember(index,push);
  else closeMember(push);
}
document.getElementById("sector-picker").innerHTML=DATA.sectors.map(sector=>`<option value="${esc(sector)}">${esc(displaySector(sector))}</option>`).join("");
document.getElementById("sector-picker").addEventListener("change",event=>{currentSector=event.target.value;closeMember();});
document.getElementById("member-search").addEventListener("input",renderPicker);
document.getElementById("picker-list").addEventListener("click",event=>{const card=event.target.closest("[data-member]");if(card)openMember(Number(card.dataset.member));});
document.getElementById("close-member").addEventListener("click",()=>closeMember());
document.getElementById("member-sector-tabs").addEventListener("click",event=>{const button=event.target.closest("[data-member-sector]");if(button)switchMemberSector(button.dataset.memberSector);});
["text-search"].forEach(id=>document.getElementById(id).addEventListener("input",renderInteractions));
["org-filter","year-filter","issue-filter","position-filter"].forEach(id=>document.getElementById(id).addEventListener("change",renderInteractions));
document.getElementById("clear-filters").addEventListener("click",()=>{document.getElementById("text-search").value="";["org-filter","year-filter","issue-filter","position-filter"].forEach(id=>document.getElementById(id).value="");renderInteractions();document.getElementById("text-search").focus();});
document.querySelectorAll("[data-posture-mode]").forEach(button=>button.addEventListener("click",()=>{postureMode=button.dataset.postureMode;document.querySelectorAll("[data-posture-mode]").forEach(item=>{item.classList.toggle("active",item===button);item.setAttribute("aria-pressed",item===button?"true":"false");});if(currentMember!==null){renderMemberSector();renderInteractions();}else renderPicker();}));
window.addEventListener("popstate",()=>applyLocation(false));
applyLocation(false);
</script></main></body></html>
"""
    script = (
        script.replace("__CHANNEL_LABELS__", json.dumps(CHANNEL_LABELS))
        .replace("__PERIOD_LABELS__", json.dumps(PERIOD_LABELS))
        .replace("__POSITION_LABEL__", json.dumps(POSITION_NOT_DISCLOSED_LABEL))
        .replace("__INFERRED_TIP__", json.dumps(INFERRED_POSITION_NOTE))
    )
    return "".join(
        (
            "<!doctype html><html lang=en><head><meta charset=utf-8>",
            '<meta name=viewport content="width=device-width,initial-scale=1">',
            "<title>The filings behind the number</title>",
            """<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 208'><path d='M108 20L64 64L28 104L64 144L20 188M64 64L100 104L64 144' fill='none' stroke='%23EECD5C' stroke-width='24' stroke-linejoin='miter' stroke-miterlimit='6'/></svg>">""",
            style,
            body,
            script,
        )
    )


def build(
    data_root,
    out,
    ledger_dir=None,
    bills_audit_path=None,
    congestion_path=None,
    legislators_path=None,
    voteview_path=None,
):
    data_root = os.path.abspath(data_root)
    input_paths = _input_paths(
        data_root,
        ledger_dir=ledger_dir,
        bills_audit_path=bills_audit_path,
        congestion_path=congestion_path,
        legislators_path=legislators_path,
        voteview_path=voteview_path,
    )
    paths_by_label = dict(input_paths)
    hashes = {label: industry._sha256(path, label) for label, path in input_paths}
    try:
        resolver = identity.MemberNameResolver.from_paths(
            congestion_path=paths_by_label["congestion_roster"],
            legislators_path=paths_by_label["legislators_roster"],
            voteview_path=paths_by_label["voteview_members"],
        )
    except identity.IdentityError as exc:
        raise IngestError(f"member name resolution unavailable: {exc}") from exc

    payload, receipts = build_payload(paths_by_label, resolver, data_root)
    document = _document(
        payload, industry._hash_footer(input_paths, hashes), receipts
    )
    document_bytes = len(document.encode("utf-8"))
    if document_bytes >= MAX_DOCUMENT_BYTES:
        raise IngestError(
            f"generated page is {document_bytes:,} bytes; limit is "
            f"{MAX_DOCUMENT_BYTES - 1:,}; shard by sector or trim retention explicitly"
        )
    out_path = os.path.abspath(out)
    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.isdir(out_dir):
        raise IngestError(f"output directory not found: {out_dir}")
    industry._write_text_atomic(document, out_path)
    print(f"interactions HTML: {out_path} ({document_bytes:,} bytes)")
    print(
        "  "
        + " ".join(
            f"{key}={value:,}" if isinstance(value, int) else f"{key}={value}"
            for key, value in receipts.items()
            if isinstance(value, int)
        )
    )
    return document


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Build the member interaction ledger page"
    )
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--ledger-dir", default=None)
    # Same default as make_lobby_network_html: the live tree, not a derived
    # staging directory. A builder whose sibling deploys and which quietly does
    # not is a trap -- it reports success while the page nobody can see stays old.
    parser.add_argument(
        "--out", default=os.path.join(MB_ROOT, "lobby-interactions.html")
    )
    parser.add_argument("--bills-audit", default=None)
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    data_root = args.data_root or _default_data_root()
    out = args.out
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    try:
        build(
            data_root,
            out,
            ledger_dir=args.ledger_dir,
            bills_audit_path=args.bills_audit,
        )
    except IngestError as exc:
        print(f"INTERACTIONS PAGE ERROR: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
