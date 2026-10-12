"""Sealed sparse SQLite index of every verified LDA NAMED_ON_ACTIVITY edge."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import gzip
from contextlib import closing
import hashlib
import json
from pathlib import Path
import math
import sqlite3
import time


SCHEMA = "staffer-general-activity-index-v2"
ROUTING_VERSION = "ecosystem-period-associations-v3-reconciliation"


class ActivitySearchLimited(Exception):
    pass


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def report_bounds(year, period):
    periods = {"first_quarter": (1, 4), "second_quarter": (4, 7), "third_quarter": (7, 10), "fourth_quarter": (10, 1)}
    if period not in periods:
        return None, None
    first, last = periods[period]
    try:
        return date(int(year), first, 1).isoformat(), date(int(year) + (last == 1), last, 1).isoformat()
    except (ValueError, TypeError):
        return None, None


def build_index(source_root, output_path):
    """Stream source files once into an atomic, hash-sealed read-only artifact."""
    source_root, output_path = Path(source_root), Path(output_path)
    if output_path.exists() or output_path.with_suffix(output_path.suffix + ".receipt.json").exists():
        raise ValueError("Choose a new output path; sealed indexes are immutable")
    source_receipt = source_root / "lda_source_native_corpus_receipt.json"
    receipt_bytes = source_receipt.read_bytes()
    receipt = json.loads(receipt_bytes)
    if receipt.get("status") != "PASS" or receipt.get("failures"):
        raise ValueError("Native source receipt is not PASS")
    specs = {entry["role"]: entry for entry in receipt["files"]}
    sources = []
    for role in ("activity_assertions", "industry_edges"):
        spec = specs[role]
        path = source_root / spec["path"]
        if path.stat().st_size != spec["bytes"] or sha(path) != spec["sha256"]:
            raise ValueError("Source hash/size mismatch: " + role)
        sources.append({**spec, "path": str(path)})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".building")
    if temporary.exists():
        raise ValueError("Incomplete build exists: " + str(temporary))
    connection = sqlite3.connect(temporary)
    counts = {"activities": 0, "industry_edges_read": 0, "named_on_activity": 0, "activities_without_report_dates": 0}
    started = time.monotonic()
    try:
        connection.executescript("""
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            PRAGMA temp_store=FILE;
            PRAGMA cache_size=-32768;
            CREATE TABLE activity(id INTEGER PRIMARY KEY, source_id TEXT NOT NULL UNIQUE,
              start TEXT, end_exclusive TEXT, posted TEXT, filing_uuid TEXT, firm_id TEXT, issue TEXT, client_id TEXT);
            CREATE TABLE named(person TEXT NOT NULL, activity INTEGER NOT NULL, edge_id TEXT NOT NULL,
              PRIMARY KEY(person,activity)) WITHOUT ROWID;
        """)
        batch = []
        for row in rows(source_root / specs["activity_assertions"]["path"]):
            start, end = report_bounds(row.get("filing_year"), row.get("filing_period"))
            counts["activities_without_report_dates"] += start is None
            batch.append((row["source_activity_id"], start, end, row.get("dt_posted"), row.get("filing_uuid"),
                          row.get("registrant_source_org_id"), row.get("general_issue_code"), row.get("client_source_org_id")))
            counts["activities"] += 1
            if len(batch) >= 10000:
                connection.executemany("INSERT INTO activity(source_id,start,end_exclusive,posted,filing_uuid,firm_id,issue,client_id) VALUES(?,?,?,?,?,?,?,?)", batch)
                batch.clear()
        connection.executemany("INSERT INTO activity(source_id,start,end_exclusive,posted,filing_uuid,firm_id,issue,client_id) VALUES(?,?,?,?,?,?,?,?)", batch)
        connection.commit()
        batch = []
        inserted = 0
        statement = "INSERT INTO named(person,activity,edge_id) SELECT ?,id,? FROM activity WHERE source_id=?"
        for row in rows(source_root / specs["industry_edges"]["path"]):
            counts["industry_edges_read"] += 1
            if row.get("predicate") != "NAMED_ON_ACTIVITY":
                continue
            if not str(row.get("subject_id", "")).startswith("lda:lobbyist:"):
                raise ValueError("NAMED_ON_ACTIVITY subject is not an exact LDA lobbyist ID")
            batch.append((row["subject_id"], row["edge_id"], row["object_id"]))
            counts["named_on_activity"] += 1
            if len(batch) >= 10000:
                inserted += connection.executemany(statement, batch).rowcount
                batch.clear()
        inserted += connection.executemany(statement, batch).rowcount
        if inserted != counts["named_on_activity"]:
            raise ValueError("Named edge references an absent activity")
        for role, key in (("activity_assertions", "activities"), ("industry_edges", "industry_edges_read")):
            if counts[key] != specs[role]["records"]:
                raise ValueError("Source row count mismatch: " + role)
        connection.execute("CREATE INDEX named_activity ON named(activity,person)")
        build_period_memberships(connection)
        counts['period_memberships'] = connection.execute('SELECT count(*) FROM member').fetchone()[0]
        counts['active_filings'] = connection.execute('SELECT count(*) FROM active_filing').fetchone()[0]
        connection.commit()
        counts["source_person_ids"] = connection.execute("SELECT count(DISTINCT person) FROM named").fetchone()[0]
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        if source_receipt.read_bytes() != receipt_bytes:
            raise ValueError("Source receipt changed during build")
        for source in sources:
            if sha(source["path"]) != source["sha256"]:
                raise ValueError("Source changed during index build: " + source["path"])
    finally:
        connection.close()
    temporary.replace(output_path)
    result = {"schema": SCHEMA, "status": "PASS", "database": output_path.name, "sha256": sha(output_path),
              "bytes": output_path.stat().st_size, "counts": counts, "sources": sources,
              "source_receipt": str(source_receipt), "source_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
              "build_seconds": round(time.monotonic() - started, 2),
              "semantics": "Issue, filing, registrant-period and client-mediated associations; never continuous employment or familiarity"}
    output_path.with_suffix(output_path.suffix + ".receipt.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def build_period_memberships(connection):
    """Keep raw filings; route on the latest unambiguous registrant/client/quarter version."""
    connection.executescript('''
      CREATE TABLE active_filing AS
      WITH filings AS (
        SELECT filing_uuid,firm_id,client_id,start,end_exclusive,max(posted) posted
        FROM activity WHERE filing_uuid IS NOT NULL AND firm_id IS NOT NULL AND client_id IS NOT NULL
          AND start IS NOT NULL AND end_exclusive>start AND posted IS NOT NULL
        GROUP BY filing_uuid,firm_id,client_id,start,end_exclusive
      ), latest AS (
        SELECT *, max(posted) OVER(PARTITION BY firm_id,client_id,start,end_exclusive) newest FROM filings
      ) SELECT min(filing_uuid) filing_uuid FROM latest WHERE posted=newest
        GROUP BY firm_id,client_id,start,end_exclusive HAVING count(*)=1;
      CREATE UNIQUE INDEX active_filing_id ON active_filing(filing_uuid);
      CREATE TABLE member AS
        SELECT n.person,a.filing_uuid,a.firm_id,a.client_id,a.start,a.end_exclusive,max(a.posted) posted,
          json_group_array(a.source_id) activities,json_group_array(n.edge_id) edge_ids
        FROM activity a JOIN active_filing f ON f.filing_uuid=a.filing_uuid JOIN named n ON n.activity=a.id
        GROUP BY n.person,a.filing_uuid,a.firm_id,a.client_id,a.start,a.end_exclusive;
      CREATE INDEX member_person ON member(person);
      CREATE INDEX member_firm_period ON member(firm_id,start,end_exclusive);
      CREATE INDEX member_client_period ON member(client_id,start,end_exclusive);
    ''')


class ActivityIndex:
    """Read-only lazy neighbor provider, mapped through exact existing identities."""
    def __init__(self, database, nodes, reconciliation_release='auto'):
        self.database = Path(database).resolve()
        self.receipt_path = self.database.with_suffix(self.database.suffix + ".receipt.json")
        self.receipt = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        if self.receipt.get("schema") not in (SCHEMA, "staffer-general-activity-index-v1") or self.receipt.get("status") != "PASS":
            raise ValueError("Activity index receipt not supported/PASS")
        if self.database.stat().st_size != self.receipt["bytes"] or sha(self.database) != self.receipt["sha256"]:
            raise ValueError("Activity index checksum mismatch")
        self.revision = self.receipt["sha256"] + ':' + ROUTING_VERSION
        self.nodes = nodes
        self.to_source = {}
        self.to_node = {}
        spine_links = defaultdict(set)
        for node_id, node in nodes.items():
            source_id = node.get("lda_source_person_id")
            if source_id and (node.get("spine_person_id") or node_id.startswith("person:hill:person:")):
                spine_links[source_id].add(node_id)
        for node_id, node in nodes.items():
            source_id = node.get("lda_source_person_id")
            if not source_id and node_id.startswith("person:lda:lobbyist:"):
                source_id = node_id.removeprefix("person:")
            if not source_id:
                continue
            candidates = spine_links[source_id]
            target = next(iter(candidates)) if len(candidates) == 1 else "person:" + source_id
            if target in nodes:
                self.to_node[source_id] = target
        # Carry already-adjudicated identities into all activity records. Names
        # are never used here to establish a new identity.
        self.identity_basis = {}
        for node_id, node in nodes.items():
            if node.get("identity_status") in {'USER_CONFIRMED_SAME_PERSON', 'REFERENCE_RESOLVED_SAME_PERSON'} or node.get("identity_basis") in {'USER_CONFIRMED', 'REFERENCE_RESOLVED', 'RECORD_AND_REFERENCE_RESOLVED'}:
                for source_id in node.get("source_person_ids", []):
                    self.to_node[source_id] = node_id
                    self.identity_basis[source_id] = {"basis": node.get('identity_basis') or node.get('identity_status'), "primary_id": node_id,
                                                       "source_person_ids": node["source_person_ids"],
                                                       "provenance": node.get("identity_provenance", {})}
        for source_id, target in self.to_node.items():
            self.to_source.setdefault(target, []).append(source_id)
        from .staffer_reconciled_evidence import load_release, bind_client_groups, bind_official_associations
        release = load_release(self.database.parent) if reconciliation_release=='auto' else reconciliation_release
        self.reconciliation_as_of = (release or {}).get('as_of')
        bind_client_groups(self, release)
        bind_official_associations(self,release)
        if release:
            self.revision += ':' + release['release_sha256']

    def records(self, person, offset=0, limit=50):
        """Page all named-on-activity evidence, including records without usable dates."""
        source_ids = self.to_source.get(person, [])
        offset, limit = max(0, int(offset)), max(1, min(100, int(limit)))
        if not source_ids:
            return {'records': [], 'total': 0, 'offset': offset, 'limit': limit, 'has_more': False}
        placeholders = ','.join('?' for _ in source_ids)
        with closing(sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True)) as connection:
            total = connection.execute('SELECT count(*) FROM named WHERE person IN (' + placeholders + ')', source_ids).fetchone()[0]
            cursor = connection.execute('''SELECT n.person,n.edge_id,a.source_id,a.start,a.end_exclusive,a.posted,a.filing_uuid,a.firm_id,a.issue
                FROM named n JOIN activity a ON a.id=n.activity WHERE n.person IN (''' + placeholders + ') ORDER BY a.id,n.person LIMIT ? OFFSET ?', [*source_ids, limit, offset])
            fields = ['source_person_id','source_edge_id','source_activity_id','report_start','report_end_exclusive','posted','filing_uuid','registrant_source_id','issue']
            values = [dict(zip(fields, row)) for row in cursor]
        return {'records': values, 'total': total, 'offset': offset, 'limit': limit, 'has_more': offset+len(values)<total,
                'provenance': {'index_sha256': self.receipt['sha256'], 'routing_version': ROUTING_VERSION, 'receipt': str(self.receipt_path)},
                'note': 'Named in a lobbying activity report. Reporting-period dates do not establish continuous employment or personal familiarity.'}

    def neighbors(self, person, options, budget):
        if self.receipt['schema'] == SCHEMA:
            from .staffer_ecosystem_routes import period_neighbors
            return period_neighbors(self, person, options, budget)
        if person not in self.to_source or not options.get("include_review", True):
            return {}
        remaining = options["max_neighbor_checks"] - budget["neighbor_checks"]
        if remaining <= 0:
            raise ActivitySearchLimited()
        connection = sqlite3.connect(self.database.as_uri() + "?mode=ro&immutable=1", uri=True)
        connection.set_progress_handler(lambda: int(time.monotonic() > budget["deadline"]), 1000)
        result = {}
        try:
            placeholders = ",".join("?" for _ in self.to_source[person])
            cursor = connection.execute("""SELECT other.person,a.source_id,a.start,a.end_exclusive,a.posted,
                a.filing_uuid,a.firm_id,a.issue,own.edge_id,other.edge_id,own.person
                FROM named own JOIN named other ON other.activity=own.activity
                JOIN activity a ON a.id=own.activity
                WHERE own.person IN (""" + placeholders + """) AND other.person<>own.person AND a.start IS NOT NULL
                """ + (" LIMIT ?" if math.isfinite(remaining) else ""),
                (*self.to_source[person], remaining + 1) if math.isfinite(remaining) else tuple(self.to_source[person]))
            for other_source, activity, start, end, posted, filing, firm, issue, own_edge, other_edge, own_source in cursor:
                budget["neighbor_checks"] += 1
                if budget["neighbor_checks"] > options["max_neighbor_checks"] or time.monotonic() > budget["deadline"]:
                    raise ActivitySearchLimited()
                target = self.to_node.get(other_source)
                if not target or target == person:
                    continue
                old = result.get(target)
                if old and old["end_exclusive"] >= end:
                    continue
                firm_node = "organization:" + str(firm)
                result[target] = {"source": person, "target": target, "relationship": "co_reported_lobbying_activity",
                    "temporal_status": "reported_activity_period", "activity_id": "activity:" + activity,
                    "employer_id": "activity:" + activity, "firm_id": firm_node,
                    "employer_label": self.nodes.get(firm_node, {}).get("label", str(firm)),
                    "start": start, "end_exclusive": end, "as_of": budget.get("as_of"),
                    "report_period": {"start": start, "end_exclusive": end}, "evidence_as_of": [posted, posted],
                    "continuous_employment_asserted": False, "current_evidence": False, "source_relation": True,
                    "confidence": None, "confidence_label": "EXACT_SOURCE_ACTIVITY_ASSOCIATION",
                    "confidence_scope": "shared reported lobbying activity; employment overlap and personal familiarity not established",
                    "review_only": True, "cost": options["reported_activity_cost"], "cost_overlay_only": True,
                    "provenance": {"index": str(self.database), "index_sha256": self.receipt['sha256'],
                        "source_receipt_sha256": self.receipt["source_receipt_sha256"], "source_activity_id": activity,
                        "filing_uuid": filing, "edge_ids": [own_edge, other_edge], "source_person_ids": [own_source, other_source], "issue": issue,
                        "identity_overlays": [self.identity_basis[source_id] for source_id in (own_source, other_source) if source_id in self.identity_basis]}}
        except sqlite3.OperationalError as exc:
            if "interrupt" in str(exc).lower():
                raise ActivitySearchLimited() from exc
            raise
        finally:
            connection.close()
        return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root")
    parser.add_argument("output_path")
    args = parser.parse_args()
    print(json.dumps(build_index(args.source_root, args.output_path), indent=2))
