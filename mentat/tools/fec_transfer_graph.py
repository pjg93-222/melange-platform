"""Auditable committee cash-transfer graph and explicitly modelled allocations.

OTH 24G/24K outgoing records only: incoming mirror records and independent
expenditures are never counted again as transfers. Sources:
https://www.fec.gov/campaign-finance-data/transaction-type-code-descriptions/
https://www.fec.gov/campaign-finance-data/any-transaction-one-committee-another-file-description/
"""
import argparse
from collections import defaultdict,deque
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import time
import sqlite3
import csv

VERSION=6
CASH_CODES={'24G','24K'}
COMMITTEE=re.compile(r'C[0-9]{8}')
DESTINATION_GLOB="destination GLOB 'C[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'"
# `committee|report_type|transaction_id` groups rows that may be versions of one
# transaction, but it is not an identity: filers reuse a schedule line label as
# TRAN_ID, so `SE.5` collides across every filing they make. What a row IS gets
# decided by its counterparty, amount, date and transaction type -- see
# resolve_group(). A shared label or attribute similarity is a review clue,
# not proof that one observation supersedes another.
# Default accounting retains every distinct observation until a preserved
# filing relationship proves supersession. Legacy policies are sensitivity
# scenarios, never a claim that an ambiguous earlier row was contradicted.
# VERSION 6 (F11, Paul 2026-09-25) makes one exception: an FEC reload, the same
# record loaded again under a second SUB_ID, counts once. See reload_key().
AMBIGUOUS_VERSION_POLICY='count_all'
POLICY_TAGS={'withhold':'w','count_latest':'c','count_all':'a'}
AMENDMENT_ORDER={'T':2,'A':1}


def atomic_json(path,value):
    pending=path.with_suffix('.pending')
    pending.write_text(json.dumps(value,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    os.replace(pending,path)


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def publish_index(directory,index):
    path=directory/'transfer-edges.csv'
    temporary=path.with_suffix('.pending')
    with temporary.open('w',encoding='utf-8',newline='') as stream:
        fields=['cycle','source','destination','amount','record_count','first_date','last_date','records']
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(index['edges'])
    os.replace(temporary,path)
    info={key:value for key,value in index.items() if key!='edges'}
    info['edges_sha256']=sha(path)
    atomic_json(directory/'transfer-info.json',info)
    atomic_json(directory/'transfer-current.json',index)


def verified_headers(evidence):
    """Accept only preserved, hash-checked filing headers, never inferred dates."""
    from fec_filing_headers import parse_header
    headers={}
    for item in evidence:
        header=item['header']; number=header['file_num']
        prefix=bytes.fromhex(item['prefix_hex'])
        if (hashlib.sha256(prefix).hexdigest()!=item['prefix_sha256']
                or parse_header(prefix,number)!=header
                or item['url']!=f'https://docquery.fec.gov/dcdev/posted/{number}.fec'):
            raise ValueError('Transfer filing header evidence failed verification')
        if number in headers and headers[number]!=header:
            raise ValueError('Conflicting transfer filing headers')
        headers[number]=header
    return headers


def transfer_key(fields,row_number):
    return '|'.join((fields[0],fields[2],fields[16])) if fields[16] else 'SUB|'+fields[0]+'|'+(fields[20] or str(row_number))


def identity(row):
    """What a row claims to BE: who received it, how much, when, and as what."""
    return (row['destination'],row['cents'],row['date'],row['type'])


def reload_key(row):
    """F11: the key of an FEC reload, one record loaded twice under two SUB_IDs.

    Rows that share committee, report type and transaction id (the version
    key), the file number, the identity (counterparty id and name, amount, date,
    type) and the memo code differ in nothing but the SUB_ID, and only they count
    once. A row without a transaction id or a file number is never a reload.
    Genuine repeats carry distinct transaction ids and cross-file observations
    distinct file numbers, so neither meets here; every other ambiguity rule is
    unchanged. R/fec_beneficiary.R's FEC_PAS2_RELOAD_KEY is the same key for PAS2.
    """
    if not row['transaction_id'] or not row['file_num']:return ('row',row['rowid'])
    return (row['version_key'],row['file_num'],*identity(row),row['raw_line'].split('|')[7],row['memo'])


def collapse_reloads(rows):
    """(kept, reloads): one row per reload_key survives, the highest SUB_ID; input order kept."""
    buckets=defaultdict(list)
    for row in rows:buckets[reload_key(row)].append(row)
    keep={max(bucket,key=lambda row:(row['sub_id'] or '',row['rowid']))['rowid'] for bucket in buckets.values()}
    return [row for row in rows if row['rowid'] in keep],[row for row in rows if row['rowid'] not in keep]


def distinguishable(left,right):
    """True only when two identities name two DIFFERENT real counterparties.

    A blank or malformed destination names nobody, so it can never establish
    that two rows describe different transactions; such a pair stays ambiguous.
    """
    first,second=left[0] or '',right[0] or ''
    return bool(COMMITTEE.fullmatch(first) and COMMITTEE.fullmatch(second) and first!=second)


def header_families(rows,headers):
    """Group rows into amendment families using verified headers only.

    Returns {family: (rows, winning_file_num)}, or None when the headers do not
    cover every filing involved or do not establish an order. Unlike VERSION 3
    this tolerates several rows per filing -- a filing itemizing two lines under
    one label is not a contradiction; two filings claiming the same amendment
    number still is.
    """
    families=defaultdict(list)
    for row in rows:
        header=headers.get(row['file_num'],{})
        if (header.get('committee_id')!=row['source'] or header.get('report_type')!=row['report']
                or header.get('amendment_indicator')!=row['amendment']
                or header.get('relationship_evidence') is False):return None
        original=header.get('original_file_num')
        if header.get('amendment_indicator')=='N' and not original:family=header['file_num']
        elif header.get('amendment_indicator') in ('A','T') and original and original!=header['file_num']:family=original
        else:return None
        families[family].append(row)
    resolved={}
    for family,members in families.items():
        versions={}
        for row in members:
            header=headers.get(row['file_num'],{})
            number='0' if header.get('amendment_indicator')=='N' else str(header.get('amendment_number',''))
            if not number.isdigit():return None
            number=int(number)
            if versions.setdefault(number,row['file_num'])!=row['file_num']:return None
        resolved[family]=(members,versions[max(versions)])
    return resolved


def resolve_group(rows,headers,policy):
    """Decide one version-key group. `rows` are its distinct raw lines.

    Returns (decisions, tran_id_reused, route, ambiguous_components) where each
    decision is (row, status, version_basis, unverified_version).
    """
    identities=defaultdict(list)
    for row in rows:identities[identity(row)].append(row)
    reused=1 if len(identities)>1 else 0

    def keep_one_per_identity(group,basis):
        """Keep distinct observations; count_all collapses only FEC reloads (reload_key)."""
        decided=[];buckets=defaultdict(list)
        for row in group:buckets[identity(row)].append(row)
        for bucket in buckets.values():
            bucket.sort(key=lambda row:(AMENDMENT_ORDER.get(row['amendment'],0),row['sub_id'] or '',row['rowid']))
            if policy == 'count_all':
                kept,reloads=collapse_reloads(bucket)
                decided.extend((row,'duplicate','repeated_itemization',0) for row in reloads)
                decided.extend((row,'current',basis,int(len(kept)>1)) for row in kept)
                continue
            decided.extend((row,'duplicate','repeated_itemization',0) for row in bucket[:-1])
            decided.append((bucket[-1],'current',basis,0))
        return decided

    if len({row['file_num'] for row in rows})>1:
        resolved=header_families(rows,headers)
        if resolved is not None:
            decided=[]
            for members,winner in resolved.values():
                decided.extend((row,'superseded','header',0) for row in members if row['file_num']!=winner)
                decided.extend(keep_one_per_identity(
                    [row for row in members if row['file_num']==winner],'header'))
            return decided,reused,'header',[]
    if policy == 'count_all':
        # Reused transaction labels, identical amounts and a larger file number
        # do not establish an amendment family or a duplicate source identity.
        # An FEC reload does (F11): it counts once, the highest SUB_ID, and every
        # other row here is judged exactly as before.
        rows,reloads=collapse_reloads(rows)
        buckets=defaultdict(list)
        for row in rows:
            buckets[row['destination'],'amount',row['cents']].append(row)
            buckets[row['destination'],'date',row['date']].append(row)
        uncertain={row['rowid'] for members in buckets.values() if len(members)>1 for row in members}
        if len({row['file_num'] for row in rows})>1 and any(not COMMITTEE.fullmatch(row['destination'] or '') for row in rows):
            uncertain.update(row['rowid'] for row in rows)
        ambiguous=[rows] if uncertain else []
        return [(row,'duplicate','repeated_itemization',0) for row in reloads]+[(row,'current','filing_relationship_unverified' if row['rowid'] in uncertain else 'distinct_observation',
                 int(row['rowid'] in uncertain)) for row in rows],reused,'file_number',ambiguous
    # Legacy sensitivity scenarios assume equal identities are repeat filings
    # or itemizations and prefer the largest file number. These assumptions
    # are not evidence and are deliberately bypassed by the default above.
    decisions=[];survivors={}
    for key,members in identities.items():
        winner=max(row['file_num'] for row in members)
        decisions.extend((row,'superseded','file_number',0) for row in members if row['file_num']!=winner)
        kept=keep_one_per_identity([row for row in members if row['file_num']==winner],'file_number')
        decisions.extend(decision for decision in kept if decision[1]=='duplicate')
        survivors[key]=(winner,[decision[0] for decision in kept if decision[1]=='current'])
    keys=list(survivors);parent={key:key for key in keys}

    def root(key):
        while parent[key]!=key:parent[key]=parent[parent[key]];key=parent[key]
        return key

    def merge(members):
        for other in members[1:]:
            left,right=root(members[0]),root(other)
            if left!=right:parent[left]=right

    # Two surviving identities in DIFFERENT filings are candidate VERSIONS of
    # one transaction only when they name the same real counterparty AND agree
    # on the amount or on the date. An amendment corrects a figure or a date;
    # rows that share neither have nothing in common but a recycled label --
    # the same `SB22.2` in two years' Q2 reports is two transfers, not a
    # version. Type is deliberately NOT part of this test, so a 24K restated as
    # 24Z on the same day for the same money is still a version.
    # Merging per bucket and resolving with union-find keeps transitivity: A~B
    # on the amount and B~C on the date is one component.
    buckets=defaultdict(list)
    for key in keys:
        if COMMITTEE.fullmatch(key[0] or ''):
            buckets['amount',key[0],key[1]].append(key)
            buckets['date',key[0],key[2]].append(key)
    for members in buckets.values():
        if len({survivors[key][0] for key in members})>1:merge(members)
    files={survivors[key][0] for key in keys}
    blank_files={survivors[key][0] for key in keys if not COMMITTEE.fullmatch(key[0] or '')}
    if blank_files:
        merge([key for key in keys
               if (files if not COMMITTEE.fullmatch(key[0] or '') else blank_files)-{survivors[key][0]}])
    components=defaultdict(list)
    for key in keys:components[root(key)].append(key)
    ambiguous=[]
    for members in components.values():
        group=[row for key in members for row in survivors[key][1]]
        if len(members)<2:
            decisions.extend((row,'current','file_number',0) for row in group)
            continue
        ambiguous.append(group)
        if policy!='count_latest':
            decisions.extend((row,'unresolved_version','',0) for row in group)
            continue
        latest=max(group,key=lambda row:(row['file_num'],row['sub_id'] or '',row['rowid']))
        decisions.extend((row,'current' if row is latest else 'superseded','file_number_unverified',1)
                         for row in group)
    return decisions,reused,'file_number',ambiguous


def transfer_index(path,cycle,filing_headers=()):
    filing_headers=list(filing_headers)
    policy=AMBIGUOUS_VERSION_POLICY
    if policy not in POLICY_TAGS:raise ValueError(f'Unknown ambiguous version policy {policy!r}')
    headers=verified_headers(filing_headers)
    signature=sha(path)
    header_signature=hashlib.sha256(json.dumps(filing_headers,sort_keys=True).encode()).hexdigest()
    cache=path.parent/f'transfer-index-v{VERSION}{POLICY_TAGS[policy]}-{cycle}-{signature[:20]}-{header_signature[:12]}.json'
    ledger=cache.with_suffix('.sqlite')
    if cache.exists() and ledger.exists():
        cached=json.loads(cache.read_text(encoding='utf-8'))
        if cached.get('ledger_sha256')!=sha(ledger):raise ValueError('Transfer ledger checksum failed')
        return cached
    started=time.monotonic();counts=defaultdict(int)
    pending=ledger.with_suffix(f'.pending-{os.getpid()}-{time.monotonic_ns()}')
    connection=sqlite3.connect(pending)
    connection.execute('PRAGMA journal_mode=OFF')
    connection.execute('PRAGMA synchronous=OFF')
    connection.execute('CREATE TABLE records(version_key TEXT, source TEXT,destination TEXT,date TEXT,cents INTEGER,type TEXT,transaction_id TEXT,file_num INTEGER,memo TEXT,report TEXT,sub_id TEXT,amendment TEXT,raw_line TEXT,status TEXT,version_basis TEXT DEFAULT %s,tran_id_reused INTEGER DEFAULT 0,unverified_version INTEGER DEFAULT 0)'%"''")
    connection.execute('CREATE TABLE filing_headers(evidence_json TEXT)')
    connection.executemany('INSERT INTO filing_headers VALUES (?)',[(json.dumps(h,sort_keys=True),) for h in filing_headers])
    sql=('INSERT INTO records(version_key,source,destination,date,cents,type,transaction_id,'
         'file_num,memo,report,sub_id,amendment,raw_line,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)')
    unresolved=set(); unresolved_all=False; withheld=defaultdict(int)
    batch=[]
    try:
        # OTH files contain millions of unrelated receipts and expenditures.
        # First identify cash-related keys on disk, then retain EVERY observed
        # representation of those keys, including later noncash amendments.
        connection.execute('CREATE TABLE relevant_keys(version_key TEXT PRIMARY KEY) WITHOUT ROWID')
        with path.open(encoding='utf-8',errors='replace') as stream:
            for number,line in enumerate(stream,1):
                fields=line.rstrip('\n\r').split('|')
                if len(fields)==21 and fields[5] in CASH_CODES:
                    batch.append((transfer_key(fields,number),))
                if len(batch)>=10000:
                    connection.executemany('INSERT OR IGNORE INTO relevant_keys VALUES (?)',batch)
                    connection.commit();batch.clear()
        if batch:
            connection.executemany('INSERT OR IGNORE INTO relevant_keys VALUES (?)',batch);batch.clear()
        with path.open(encoding='utf-8',errors='replace') as stream:
            for line in stream:
                counts['source_rows']+=1
                fields=line.rstrip('\n\r').split('|')
                if len(fields)!=21:
                    counts['malformed_rows']+=1
                    if re.fullmatch(r'C\d{8}',fields[0]):unresolved.add(fields[0])
                    else:unresolved_all=True
                    continue
                source,amend,report=fields[:3];kind=fields[5];destination=fields[15]
                # Keep all transaction types and destinations until versions are
                # resolved. An amendment can change either, or mark a memo.
                if kind in CASH_CODES:counts['cash_transfer_rows']+=1
                key=transfer_key(fields,counts['source_rows'])
                if not connection.execute('SELECT 1 FROM relevant_keys WHERE version_key=?',(key,)).fetchone():continue
                sub=fields[20];raw_date=fields[13].zfill(8)
                try:date=datetime.strptime(raw_date,'%m%d%Y').date().isoformat()
                except ValueError:date=''
                try:
                    value=Decimal(fields[14])*100
                    if not value.is_finite() or value!=value.to_integral_value():raise ValueError('Invalid cents')
                    cents=int(value)
                    if abs(cents)>9223372036854775807:raise ValueError('Amount too large')
                except (ValueError,ArithmeticError):cents=None
                transaction=fields[16]
                try:file_num=int(fields[17] or 0)
                except ValueError:file_num=0
                batch.append((key,source,destination,date,cents,kind,transaction,file_num,fields[18],report,sub,amend,line.rstrip('\n\r'),'observed'))
                if len(batch)>=10000:
                    connection.executemany(sql,batch);connection.commit();batch.clear()
        if batch:connection.executemany(sql,batch)
        conflict=connection.execute("SELECT sub_id FROM records WHERE sub_id<>'' GROUP BY source,sub_id HAVING COUNT(DISTINCT raw_line)>1 LIMIT 1").fetchone()
        if conflict:
            raise ValueError(f'Conflicting FEC sub-ID {conflict[0]}; preserve and reconcile the source observations before indexing')
        connection.execute('CREATE INDEX records_version ON records(version_key)')
        connection.execute('CREATE INDEX records_edge ON records(source,destination)')
        connection.row_factory=sqlite3.Row
        from itertools import groupby
        # A version key holding ONE row has nothing to compete with, so it is
        # current by construction. That is the overwhelming majority of the
        # file; settling those in SQL keeps them out of the Python partition
        # entirely. The result is identical -- resolve_group() on a single row
        # returns exactly this, and the validity test below is the same test.
        connection.execute('CREATE TEMP TABLE singletons(version_key TEXT PRIMARY KEY) WITHOUT ROWID')
        connection.execute('INSERT INTO singletons SELECT version_key FROM records GROUP BY version_key HAVING COUNT(*)=1')
        counts['version_groups_file_number']+=connection.execute('SELECT COUNT(*) FROM singletons').fetchone()[0]
        alone='version_key IN (SELECT version_key FROM singletons)'
        connection.execute(f"UPDATE records SET status='current',version_basis='file_number' WHERE {alone}")
        cash=f"status='current' AND {alone} AND type IN ('24G','24K')"
        for test,name in ((f"date IS NULL OR date=''",'invalid_transfer_dates'),
                          ('cents IS NULL','invalid_transfer_amounts'),
                          (f'NOT ({DESTINATION_GLOB})','invalid_transfer_destinations')):
            counts[name]+=connection.execute(f'SELECT COUNT(*) FROM records WHERE {cash} AND ({test})').fetchone()[0]
        broken=f"{cash} AND (date IS NULL OR date='' OR cents IS NULL OR NOT ({DESTINATION_GLOB}))"
        for row in connection.execute(f'SELECT source,memo,cents FROM records WHERE {broken}'):
            unresolved.add(row['source'])
            if row['memo']!='X':withheld[row['source']]+=max(0,row['cents'] or 0)
        connection.execute(f"UPDATE records SET status='unresolved_record' WHERE {broken}")
        for _,items in groupby(connection.execute(f'SELECT rowid,* FROM records WHERE NOT {alone} ORDER BY version_key'),lambda r:r['version_key']):
            observed=list(items)
            # Noncash records still participate in a group containing cash.
            if not any(r['type'] in CASH_CODES for r in observed):continue
            unique={};repeats=[]
            for row in observed:
                if row['raw_line'] in unique:repeats.append((row['rowid'],))
                else:unique[row['raw_line']]=row
            if repeats:connection.executemany("UPDATE records SET status='duplicate' WHERE rowid=?",repeats)
            decisions,reused,route,ambiguous=resolve_group(list(unique.values()),headers,policy)
            counts['version_groups_'+route]+=1
            if ambiguous:
                counts['unresolved_version_groups']+=1
                counts['ambiguous_version_components']+=len(ambiguous)
                for component in ambiguous:
                    if policy in ('count_latest','count_all'):continue
                    unresolved.update(row['source'] for row in component)
                    # The share this committee cannot place is what a "latest
                    # filing wins" build would have counted here, not the gross
                    # contents of the group: the rows are competing claims.
                    cash=[row for row in component
                          if row['type'] in CASH_CODES and row['memo']!='X' and (row['cents'] or 0)>0]
                    if cash:
                        latest=max(cash,key=lambda row:(row['file_num'],row['sub_id'] or '',row['rowid']))
                        withheld[latest['source']]+=latest['cents']
            updates=[]
            for row,status,basis,unverified in decisions:
                if status=='current' and row['type'] in CASH_CODES:
                    invalid=False
                    for field,count in [('date','invalid_transfer_dates'),('cents','invalid_transfer_amounts')]:
                        if row[field] is None or row[field]=='':counts[count]+=1;invalid=True
                    if not COMMITTEE.fullmatch(row['destination'] or ''):
                        counts['invalid_transfer_destinations']+=1;invalid=True
                    if invalid:
                        status='unresolved_record';unresolved.add(row['source'])
                        if row['memo']!='X':withheld[row['source']]+=max(0,row['cents'] or 0)
                counts['tran_id_reused_rows']+=reused
                counts['unverified_version_rows']+=unverified
                updates.append((status,basis,reused,unverified,row['rowid']))
            connection.executemany('UPDATE records SET status=?,version_basis=?,tran_id_reused=?,'
                                   'unverified_version=? WHERE rowid=?',updates)
        counts['latest_rows']=connection.execute("SELECT COUNT(*) FROM records WHERE status='current'").fetchone()[0]
        counts['memo_rows']=connection.execute("SELECT COUNT(*) FROM records WHERE status='current' AND memo='X'").fetchone()[0]
        counts['superseded_or_duplicate_rows']=connection.execute("SELECT COUNT(*) FROM records WHERE status IN ('superseded','duplicate')").fetchone()[0]
        counts['unresolved_rows']=connection.execute("SELECT COUNT(*) FROM records WHERE status LIKE 'unresolved_%'").fetchone()[0]
        connection.commit()
        if connection.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Transfer ledger integrity failed')
        edges=[]
        active=connection.execute("SELECT * FROM records WHERE status='current' AND type IN ('24G','24K') AND memo<>'X' ORDER BY source,destination")
        for (source,destination),items in groupby(active,lambda r:(r['source'],r['destination'])):
            rows=list(items)
            evidence=[{k:r[k] for k in ('file_num','transaction_id','sub_id','date','cents','type')} for r in rows]
            edges.append(dict(cycle=cycle,source=source,destination=destination,amount=sum(r['cents'] for r in rows)/100,
                record_count=len(rows),first_date=min(r['date'] for r in rows),last_date=max(r['date'] for r in rows),
                records=json.dumps(evidence,separators=(',',':'))))
    finally:connection.close()
    if sha(path)!=signature:raise ValueError('Transfer source changed during indexing; output withheld')
    os.replace(pending,ledger)
    result={'version':VERSION,'source_sha256':signature,'cycle':cycle,'counts':dict(counts),
            'edges':edges,'ledger_path':str(ledger.resolve()),'ledger_sha256':sha(ledger),
            'filing_headers_sha256':header_signature,'unresolved_sources':sorted(unresolved),
            'ambiguous_version_policy':policy,
            # Per source committee, the outflow dollars this index cannot place.
            # They belong in the committee's denominator, not on a blacklist.
            'unresolved_source_amounts':{source:round(cents/100,2)
                                         for source,cents in sorted(withheld.items()) if cents>0},
            'unresolved_all_sources':unresolved_all,'built_seconds':round(time.monotonic()-started,3)}
    atomic_json(cache,result)
    return result


def reachable(roots,edges,max_depth=3,stop_nodes=()):
    return list(reachable_rows(roots,edges,max_depth,stop_nodes))


def reachable_rows(roots,edges,max_depth=3,stop_nodes=(),starts=None):
    """reachable() one row at a time (F14): the same rows in the same order, never all held.

    Each root's breadth-first walk emits one row per edge out of every committee it
    reaches (root x committee x edge). With max_depth=None that is millions of rows
    for a large industry, so callers aggregate them as they stream. `starts` maps a
    root to extra starting points [(committee, route)] - committees its previous-cycle
    money opened (F12 carryover) - walked after the root's own start; their rows carry
    via_carryover and the route that opened them.
    """
    adjacency=defaultdict(list)
    for edge in edges:
        if edge['amount']>0:adjacency[edge['source']].append(edge)
    visited=set();queue=deque((root,root,0,(root,),'',False) for root in sorted(roots))
    for root in sorted(starts or ()):
        if root not in roots:continue
        for committee,route in starts[root]:
            # The route's hops are committee IDs; the opened committee carries its label.
            hops=tuple(part.split(' [')[0] for part in route.split(' > ')) if route else (root,committee)
            queue.append((root,committee,len(hops)-1,hops,route,True))
    while queue:
        root,node,depth,path,route,carried=queue.popleft()
        if (root,node) in visited:continue
        visited.add((root,node))
        for edge in adjacency[node]:
            destination=edge['destination']
            status='candidate_committee' if destination in stop_nodes else 'cycle' if destination in path else 'depth_limit' if max_depth is not None and depth+1>=max_depth else 'followed'
            text=' > '.join((route,destination)) if route else ' > '.join((*path,destination))
            row={**{k:v for k,v in edge.items() if k!='records'},'root':root,'depth':depth+1,'path':text,'status':status}
            if carried:row['via_carryover']=True
            yield row
            if status=='followed':
                queue.append((root,destination,depth+1,(*path,destination),text if route else '',carried))


def record_list(value):
    return json.loads(value) if isinstance(value,str) and value else value or []


def cash_identity(row):
    """A transaction ID is meaningful only inside its specific filing."""
    if not row.get('transaction_id') or not row.get('file_num'):return None
    return (str(int(row['file_num'])),row['transaction_id'],row.get('date'),row.get('cents'),row.get('type'))


def allocate(funding,edges,activity,max_depth=3,stop_nodes=(),candidate_links=None,unresolved_nodes=(),
             unresolved_amounts=None):
    """Conserve each root's nonnegative funding over observed cash activity.

    This is a conditional pro-rata scenario, not traced dollars, causal benefit,
    or a lower bound. Opposition stays with the named target, never its rivals.

    `unresolved_nodes` blocks a node outright. That is for the cases where
    nothing about the committee is usable -- a malformed source file, a stale
    index, a candidate transfer that could not be matched at record level.
    Withheld ROWS are different: the committee's other edges are clean, so
    `unresolved_amounts` puts those dollars into its outflow denominator as an
    explicit share instead. One ambiguous row then costs its own size, not the
    committee's whole downstream.
    """
    adjacency=defaultdict(list);terminal=defaultdict(list)
    candidate_links=candidate_links or {}
    uncertain={node:'unresolved_transfer_records' for node in unresolved_nodes}
    withheld={node:float(value) for node,value in (unresolved_amounts or {}).items() if float(value)>0}
    grouped=defaultdict(float); unidentified=defaultdict(float)
    for row in activity:
        grouped[(row['source'],row['candidate'],row['direction'])]+=row['amount']
    for (source,candidate,direction),value in grouped.items():
        if value>0:
            if candidate:terminal[source].append((candidate,direction,value))
            else:unidentified[source]+=value
    used=set()
    for edge in edges:
        if edge['amount']<=0:continue
        remaining=edge['amount'];source=edge['source'];destination=edge['destination']
        if destination in stop_nodes:
            candidates=candidate_links.get(destination,set())
            compatible=[r for r in activity if r['source']==source and r['direction']=='Contribution / Other'
                        and r['amount']>0 and (not candidates or r['candidate'] in candidates)]
            counterparts={}
            for row in compatible:
                if len(candidates)!=1:continue
                records=record_list(row.get('transfer_records'))
                # Records must be part of the retained aggregate, not merely
                # present somewhere in the original filings.
                if abs(sum(r['cents'] for r in records)/100-row['amount'])>.005:continue
                for r in records:
                    identity=cash_identity(r)
                    if identity and r.get('destination')==destination:
                        counterparts[identity]=r['cents']/100
            records=record_list(edge.get('records'))
            matched=0
            if records and abs(sum(r['cents'] for r in records)/100-edge['amount'])<=.005:
                for r in records:
                    identity=cash_identity(r);key=(source,destination,identity)
                    if identity in counterparts and key not in used:
                        matched+=counterparts[identity];used.add(key)
            remaining=max(0,remaining-matched)
            if (compatible and remaining>.005) or matched<-.005 or matched>edge['amount']+.005:
                # Without a record-level match we cannot tell whether these
                # are two outflows or two representations of the same one.
                uncertain[source]='unverified_candidate_transfer_overlap'
        if remaining>0:adjacency[source].append((destination,remaining))
    result=[];coverage=[]
    for root,input_amount in sorted(funding.items()):
        total=max(0,input_amount);allocated=defaultdict(float);unresolved=defaultdict(float)
        pending=[(root,total,0,(root,))] if total else []
        processed=0
        while pending:
            node,amount,depth,path=pending.pop()
            processed+=1
            if processed>100000:
                unresolved['traversal_limit']+=amount+sum(item[1] for item in pending)
                break
            if node in uncertain:unresolved[uncertain[node]]+=amount;continue
            outgoing=adjacency[node];destinations=terminal[node];reserve=withheld.get(node,0.)
            unknown=unidentified.get(node,0.)
            denominator=sum(v for _,v in outgoing)+sum(v for _,_,v in destinations)+reserve+unknown
            if denominator<=0:unresolved['no_observed_outflow']+=amount;continue
            if reserve:unresolved['unresolved_transfer_records']+=amount*reserve/denominator
            if unknown:unresolved['unidentified_candidate_activity']+=amount*unknown/denominator
            for candidate,direction,value in destinations:allocated[candidate,direction]+=amount*value/denominator
            for destination,value in outgoing:
                portion=amount*value/denominator
                if destination in stop_nodes:unresolved['unmatched_candidate_transfer']+=portion
                elif destination in path:unresolved['cycle']+=portion
                elif depth>=max_depth:unresolved['depth_limit']+=portion
                else:pending.append((destination,portion,depth+1,(*path,destination)))
        assigned=sum(allocated.values());unknown=sum(unresolved.values())
        if abs(total-assigned-unknown)>max(.005,total*1e-10):raise ValueError('Transfer allocation does not conserve root funding')
        for (candidate,direction),value in allocated.items():
            result.append({'root':root,'candidate_id':candidate,'direction':direction,'estimated_amount':value})
        coverage.append({'root':root,'input_net_funding':input_amount,'model_funding':total,
                         'estimated_candidate_activity':assigned,'unresolved':unknown,
                         'negative_net_funding_excluded':min(0,input_amount),'unresolved_reasons':dict(unresolved)})
    return result,coverage


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--cycles',type=int,nargs='+',default=[2022,2024,2026])
    parser.add_argument('--filing-header-directory',type=Path,
                        help='Optional preserved FEC header evidence; read locally, without fetching.')
    args=parser.parse_args()
    headers=[json.loads(p.read_text(encoding='utf-8')) for p in sorted(args.filing_header_directory.glob('*.json'))
             if not p.name.endswith('.review.json')] if args.filing_header_directory else []
    for cycle in args.cycles:
        index=transfer_index(args.directory/f'oth{str(cycle)[-2:]}'/'itoth.txt',cycle,headers)
        publish_index(args.directory/f'oth{str(cycle)[-2:]}',index)
        print(json.dumps({'cycle':cycle,'counts':index['counts'],'edges':len(index['edges']),'seconds':index['built_seconds']}),flush=True)


if __name__=='__main__':main()
