# -*- coding: utf-8 -*-
"""ONE-SHOT REPAIR of the interview turn bleed -- V2, TRUNCATE ONLY (Paul: "GO on the
truncate-only v2", 2026-09-19).

THE DEFECT.  fec/members/fetch_interviews.py:202 could not see two turn-tag shapes the networks
write (a backtick, O`DONNELL:, and any lower-case parenthetical, WALLACE (voice-over):), so the
NEXT speaker's words stayed inside the PREVIOUS member's row.  Measured over the whole corpus in
audits/laundry_list_closeout_20260918/measR1d: 5,835 of 153,576 stored INTERVIEW rows carry
another speaker's words.  fec/members/turn_tags.py now holds the repaired grammar and the
collector uses it for new rows (dial IV_TURN_TAGS).  THERE IS NO RAW TRANSCRIPT ARCHIVE, so
history cannot be re-collected; but the tags survive inside the stored text, so history can be
repaired by cutting that text at the first foreign tag.  This tool does that, once.

WHY V2 EXISTS.  V1 also CREATED a row for every later turn whose speaker resolved to a roster
member.  An independent adversarial grade of all 64 of those rows
(audits/laundry_list_closeout_20260918/gradeN12/receipts/FINDINGS.txt) found 18 of them were not
the speaker they named: 3 were pure chyron text, 5 attributed a Summit Metro Parks conservation
officer to a Representative who shares his surname, and 10 carried the programme host's words.
V2 therefore does NOT create rows.  It only ever SHORTENS one, and only when it is sure.

WHAT IT DOES, per flagged row
  * UPDATE  the row's `text` to everything BEFORE the first FOREIGN turn tag, and recompute
            `nwords` with it -- the corpus invariant nwords == len(text.split()) holds for
            153,576 of 153,576 INTERVIEW rows and must keep holding.
  * LEDGER  the ORIGINAL row verbatim (every column, as JSON) plus the whole removed string and
            its per-tag breakdown.  Nothing is ever lost: reconstruct(kept_raw, removed_raw)
            returns the original text byte for byte and --restore writes every ledgered row back.

WHAT IT REFUSES TO TOUCH  (each rule carries the receipt that measured it)
  a. a row whose kept text would hold fewer than turn_tags.MIN_TURN_WORDS words -- the
     collector's own floor for writing a turn at all (fetch_interviews.py:230).
  b. a row that ANY hand label, gold item, identity contract or reviewed record points at
     (audits/laundry_list_closeout_20260918/fixN12v2/v2_00_skipset.py builds the inventory).
  c. a row the LIVE interview credit-union stance channel reads today
     (fec/stance/extract_cu_interviews.py's own date+keyword gate), so interviews_cu.csv and
     everything downstream of it stay byte-identical.
  d. every non-INTERVIEW row, and every ALL-CAPS row -- the 7,117 Washington Journal rows are
     unmeasured and the grammar's heading guard skips them whole.
  e. a row carrying a tag whose owner is AMBIGUOUS.  turn_tags.tag_ownership is lenient about
     the row's own member and strict about everyone else; anything in between stops the row.

WHAT IT REFUSES TO RUN AS
  * --apply without the label-safety inventory (--protected).
  * --apply without an existing backup whose byte size equals the database's size at start-up.
  * --apply with a .pipeline_run.lock* beside the database, or with a non-empty -wal file (a
    file-copy backup of a WAL database with unflushed frames is not a backup).

WHAT IT DOES NOT DO.  It never touches a stance, category or gold LABEL: those are the owner's.
It does change what the labels POINT AT, which is why rules (b) and (c) exist.

IMPORT IS SAFE.  Everything runs under `if __name__ == '__main__'`.  No module-level I/O, no
environment read, no roster load, no database open at import time.

  python fec/members/migrate_interview_turn_bleed.py --dry-run --db <p> --out <dir> \
      --protected <skipset.json> [--ledger <path>]
  python fec/members/migrate_interview_turn_bleed.py --apply  --db <p> --backup <p> \
      --protected <skipset.json>
  python fec/members/migrate_interview_turn_bleed.py --restore --db <p>
"""
import argparse
import collections
import datetime
import glob
import hashlib
import io
import json
import os
import random
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from members import turn_tags as TT   # pure grammar, no I/O  # noqa: E402

TOOL_VERSION = 'interview-turn-bleed-v2-truncate-only'
LEDGER = 'speeches_turn_split_ledger'
SAMPLE_SEED = 20260919
SAMPLE_N = 40
IV = 'INTERVIEW'

LEDGER_SCHEMA = """CREATE TABLE IF NOT EXISTS %s(
 ledger_id INTEGER PRIMARY KEY,
 applied_at TEXT NOT NULL,
 tool_version TEXT NOT NULL,
 speech_rowid INTEGER NOT NULL,
 detector_classes TEXT NOT NULL,
 original_row_json TEXT NOT NULL,
 original_text_sha256 TEXT NOT NULL,
 kept_raw TEXT NOT NULL,
 kept_text TEXT NOT NULL,
 kept_nwords INTEGER NOT NULL,
 removed_raw TEXT NOT NULL,
 removed_json TEXT NOT NULL,
 restored_at TEXT)""" % LEDGER

COLUMNS = ('member', 'date', 'source', 'chamber', 'package', 'nwords', 'text')

SKIP_SHORT_KEPT = 'kept_text_under_min_turn_words'      # rule (a)
SKIP_NO_MEMBER_WORDS = 'row_has_no_member_words'        # rule (a), the degenerate case
SKIP_LABEL_BOUND = 'label_or_gold_record_points_here'   # rule (b)
SKIP_CU_EXTRACT = 'credit_union_stance_channel_row'     # rule (c)
SKIP_AMBIGUOUS_TAG = 'ambiguous_tag_owner'              # rule (e)
# precedence: an external constraint outranks a judgement about the text itself
SKIP_REASONS = (SKIP_LABEL_BOUND, SKIP_CU_EXTRACT, SKIP_AMBIGUOUS_TAG,
                SKIP_NO_MEMBER_WORDS, SKIP_SHORT_KEPT)


class Refused(Exception):
    """A precondition the operator must satisfy; never raised for data reasons."""


class Protection(object):
    """The rowids rules (b) and (c) forbid this tool from touching.  Built read-only, outside
    this module, by audits/laundry_list_closeout_20260918/fixN12v2/v2_00_skipset.py."""

    __slots__ = ('label_bound', 'cu_extract')

    def __init__(self, label_bound=(), cu_extract=()):
        self.label_bound = frozenset(label_bound or ())
        self.cu_extract = frozenset(cu_extract or ())

    def reason(self, rowid):
        if rowid in self.label_bound:
            return SKIP_LABEL_BOUND
        if rowid in self.cu_extract:
            return SKIP_CU_EXTRACT
        return None

    def __len__(self):
        return len(self.label_bound | self.cu_extract)

    @classmethod
    def load(cls, path):
        with io.open(path, encoding='utf-8') as fh:
            d = json.load(fh)
        missing = [k for k in ('label_rowids_interview', 'cu_rowids') if not d.get(k)]
        if missing:
            # A file that silently protects nothing is worse than no file: it would let --apply
            # run with rules (b) and (c) switched off while LOOKING protected.
            raise Refused('%s carries no %s: this is not the label-safety inventory. Rebuild it '
                          'with audits/laundry_list_closeout_20260918/fixN12v2/v2_00_skipset.py.'
                          % (os.path.basename(path), ' and no '.join(missing)))
        return cls(label_bound=d['label_rowids_interview'], cu_extract=d['cu_rowids'])


# ------------------------------------------------------------------ database access
def open_ro(path):
    con = sqlite3.connect('file:%s?mode=ro' % path.replace('\\', '/'), uri=True, timeout=300)
    con.execute('PRAGMA query_only=ON')
    return con


def open_rw(path):
    # isolation_level=None: this tool owns its transaction boundaries explicitly.
    con = sqlite3.connect(path, timeout=300, isolation_level=None)
    return con


def sha256_text(s):
    return hashlib.sha256((s or '').encode('utf-8', 'replace')).hexdigest()


def load_roster(path):
    with io.open(path, encoding='utf-8') as fh:
        return [m['member'] for m in json.load(fh)]


def build_lexicon(con, roster=()):
    """SPEAKER_LEX: the roster surnames plus every surname the corpus itself shows as the last
    name token of a qualifying multi-token ALL-CAPS tag (measR1d U2_row_census.py:141-154)."""
    texts = (t for (t,) in con.execute("SELECT text FROM speeches WHERE source='%s'" % IV))
    return TT.speaker_lexicon(texts) | TT.roster_surnames(roster)


# ------------------------------------------------------------------ planning (read-only)
def plan_row(row, lexicon, protection, colonless=True):
    """-> a plan dict for one row, or None when the row carries neither a FOREIGN nor an
    AMBIGUOUS turn tag (a clean row, or one whose only tags are the member's own).

    `row` is (rowid, member, date, source, chamber, package, nwords, text).  A plan with
    `cut_at` set describes a TRUNCATION: kept_raw is text[:cut] and everything from `cut` on is
    removed.  A plan with cut_at None is an AMBIGUOUS-ONLY row -- nothing is cut, and it exists
    so the row appears in the residue list where the owner can see it.
    """
    rowid, member, date, source, chamber, package, nwords, text = row
    if source != IV or not text:
        return None                              # rule (d)
    hits = TT.find_turn_tags(text, member, lexicon, chamber, colonless)
    cut_index = next((i for i, h in enumerate(hits) if h.ownership == TT.FOREIGN), None)
    ambiguous = [h for h in hits if h.ownership == TT.AMBIGUOUS]
    if cut_index is None and not ambiguous:
        return None            # no tag at all, or only the member's own tags
    if cut_index is None:
        cut = None
        kept_raw, kept_text, removed_raw, segments = text, text, '', []
    else:
        spans = TT.split_spans(text, member, lexicon, chamber, colonless)
        cut = hits[cut_index].start
        kept_raw, kept_text = text[:cut], text[:cut].strip()
        removed_raw = text[cut:]
        segments = []
        for (tag, seg_text, start, end, cls), hit in zip(spans[1:], hits):
            if start < cut:
                continue
            segments.append({
                'tag': tag, 'class': cls, 'ownership': hit.ownership, 'start': start, 'end': end,
                'raw': text[start:end], 'text': seg_text, 'nwords': len(seg_text.split()),
            })
    plan = {
        'rowid': rowid, 'member': member, 'date': date, 'chamber': chamber, 'package': package,
        'nwords': nwords, 'text': text, 'text_sha256': sha256_text(text),
        'cut_at': cut,
        'cut_tag': hits[cut_index].tag if cut_index is not None else None,
        'cut_class': hits[cut_index].cls if cut_index is not None else None,
        'classes': sorted({s['class'] for s in segments}),
        'all_tags': [{'tag': h.tag, 'class': h.cls, 'ownership': h.ownership, 'at': h.start}
                     for h in hits],
        'kept_raw': kept_raw, 'kept_text': kept_text, 'kept_nwords': len(kept_text.split()),
        'removed_raw': removed_raw, 'removed_chars': len(removed_raw),
        'removed_nwords': len(removed_raw.split()),
        'segments': segments, 'skip': None, 'skip_all': [],
    }
    reasons = []
    ext = protection.reason(rowid) if protection is not None else None
    if ext:
        reasons.append(ext)
    if ambiguous:
        reasons.append(SKIP_AMBIGUOUS_TAG)          # rule (e): ANY ambiguity stops the row
    if cut is None:
        pass                                        # nothing to cut; the row is residue only
    elif not kept_text:
        reasons.append(SKIP_NO_MEMBER_WORDS)        # rule (a), degenerate
    elif plan['kept_nwords'] < TT.MIN_TURN_WORDS:
        reasons.append(SKIP_SHORT_KEPT)             # rule (a)
    plan['skip_all'] = sorted(reasons, key=SKIP_REASONS.index)
    plan['skip'] = plan['skip_all'][0] if plan['skip_all'] else None
    return plan


def plan_all(con, lexicon, protection, colonless=True, only=None):
    """Every plan, in rowid order.  READ-ONLY: the connection may be a mode=ro one."""
    plans = []
    q = ('SELECT rowid,member,date,source,chamber,package,nwords,text FROM speeches '
         "WHERE source='%s' ORDER BY rowid" % IV)
    for row in con.execute(q):
        if only is not None and row[0] not in only:
            continue
        plan = plan_row(row, lexicon, protection, colonless)
        if plan is not None:
            plans.append(plan)
    return plans


def residue(plans):
    """Every row the detector flagged and the tool then refused to change, with its reason."""
    return [{'rowid': p['rowid'], 'member': p['member'], 'date': p['date'],
             'chamber': p['chamber'], 'package': p['package'], 'reason': p['skip'],
             'reasons': p['skip_all'], 'cut_tag': p['cut_tag'], 'cut_class': p['cut_class'],
             'kept_nwords': p['kept_nwords'], 'removed_nwords': p['removed_nwords'],
             'ambiguous_tags': [t['tag'] for t in p['all_tags']
                                if t['ownership'] == TT.AMBIGUOUS]}
            for p in plans if p['skip']]


def reconstruct(kept_raw, removed_raw):
    """The original text, byte for byte, from what the ledger stored."""
    return (kept_raw or '') + (removed_raw or '')


# ------------------------------------------------------------------ apply / restore
def check_apply_preconditions(db_path, backup_path, db_size_at_start):
    locks = glob.glob(os.path.join(os.path.dirname(os.path.abspath(db_path)),
                                   '.pipeline_run.lock*'))
    if locks:
        raise Refused('a pipeline lock is present beside the database: %s -- wait for the run to '
                      'finish, this tool must never write while the chain is writing'
                      % ', '.join(os.path.basename(p) for p in locks))
    wal = db_path + '-wal'
    if os.path.exists(wal) and os.path.getsize(wal) > 0:
        raise Refused('%s is %d bytes: the database has unflushed WAL frames, so a file-copy '
                      'backup is not a backup. Close every reader/writer and checkpoint '
                      '(PRAGMA wal_checkpoint(TRUNCATE)) before applying.'
                      % (os.path.basename(wal), os.path.getsize(wal)))
    if not backup_path:
        raise Refused('--apply requires --backup <path-to-verified-backup>')
    if not os.path.exists(backup_path):
        raise Refused('the backup %s does not exist' % backup_path)
    size = os.path.getsize(backup_path)
    if size != db_size_at_start:
        raise Refused('the backup size (%d bytes) does not match the database size at start-up '
                      '(%d bytes): take the backup again with the database closed'
                      % (size, db_size_at_start))
    return True


def ensure_ledger(con):
    con.execute(LEDGER_SCHEMA)
    con.execute('CREATE INDEX IF NOT EXISTS ix_%s_rowid ON %s(speech_rowid)' % (LEDGER, LEDGER))


def ledgered_rowids(con):
    """Rows already migrated and not since restored."""
    try:
        return {r[0] for r in con.execute('SELECT speech_rowid FROM %s WHERE restored_at IS NULL'
                                          % LEDGER)}
    except sqlite3.OperationalError:
        return set()


def apply_plans(con, plans, now):
    """Do the writes for a list of plans.  The CALLER owns the transaction.

    There is exactly one write against `speeches` here and it can only SHORTEN a text.
    """
    counters = collections.Counter()
    for plan in plans:
        if plan['skip']:
            counters['skipped_' + plan['skip']] += 1
            counters['rows_skipped'] += 1
            continue
        original = dict(zip(COLUMNS, (plan['member'], plan['date'], IV, plan['chamber'],
                                      plan['package'], plan['nwords'], plan['text'])))
        original['rowid'] = plan['rowid']
        con.execute('INSERT INTO %s(applied_at,tool_version,speech_rowid,detector_classes,'
                    'original_row_json,original_text_sha256,kept_raw,kept_text,kept_nwords,'
                    'removed_raw,removed_json,restored_at) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)' % LEDGER,
                    (now, TOOL_VERSION, plan['rowid'], ','.join(plan['classes']),
                     json.dumps(original, ensure_ascii=False, sort_keys=True),
                     plan['text_sha256'], plan['kept_raw'], plan['kept_text'],
                     plan['kept_nwords'], plan['removed_raw'],
                     json.dumps(plan['segments'], ensure_ascii=False)))
        con.execute('UPDATE speeches SET text=?, nwords=? WHERE rowid=?',
                    (plan['kept_text'], plan['kept_nwords'], plan['rowid']))
        counters['rows_truncated'] += 1
        counters['chars_removed'] += plan['removed_chars']
        counters['words_removed'] += plan['removed_nwords']
    return counters


def run_apply(db_path, backup_path, protection=None, roster_path=None, colonless=True,
              roster=None):
    if protection is None:
        raise Refused('--apply needs the label-safety inventory: pass --protected <json> built '
                      'by audits/laundry_list_closeout_20260918/fixN12v2/v2_00_skipset.py, so '
                      'rules (b) and (c) can be enforced. Running without it is not offered.')
    size_at_start = os.path.getsize(db_path)
    check_apply_preconditions(db_path, backup_path, size_at_start)
    roster = list(roster) if roster is not None else load_roster(
        roster_path or default_roster(db_path))
    con = open_rw(db_path)
    try:
        con.execute('BEGIN IMMEDIATE')
        ensure_ledger(con)
        done = ledgered_rowids(con)
        lexicon = build_lexicon(con, roster)
        plans = [p for p in plan_all(con, lexicon, protection, colonless)
                 if p['rowid'] not in done]
        counters = apply_plans(con, plans,
                               datetime.datetime.now().isoformat(timespec='seconds'))
        rows_after = con.execute('SELECT COUNT(*) FROM speeches').fetchone()[0]
        con.execute('COMMIT')
    except Exception:
        con.execute('ROLLBACK')
        raise
    finally:
        con.close()
    out = dict(counters)
    for k in ('rows_truncated', 'rows_skipped', 'chars_removed', 'words_removed'):
        out.setdefault(k, 0)
    out['rows_inserted'] = 0          # V2 creates no rows, ever
    out['rows_deleted'] = 0
    out['rows_after'] = rows_after
    out['already_migrated'] = len(done)
    return out


def run_restore(db_path):
    con = open_rw(db_path)
    counters = collections.Counter()
    try:
        con.execute('BEGIN IMMEDIATE')
        try:
            rows = con.execute('SELECT ledger_id,original_row_json FROM %s '
                               'WHERE restored_at IS NULL ORDER BY ledger_id' % LEDGER).fetchall()
        except sqlite3.OperationalError:
            rows = []
        now = datetime.datetime.now().isoformat(timespec='seconds')
        for ledger_id, original_json in rows:
            original = json.loads(original_json)
            con.execute('UPDATE speeches SET member=?,date=?,source=?,chamber=?,package=?,'
                        'nwords=?,text=? WHERE rowid=?',
                        tuple(original[c] for c in COLUMNS) + (original['rowid'],))
            counters['rows_restored'] += 1
            con.execute('UPDATE %s SET restored_at=? WHERE ledger_id=?' % LEDGER, (now, ledger_id))
        con.execute('COMMIT')
    except Exception:
        con.execute('ROLLBACK')
        raise
    finally:
        con.close()
    out = dict(counters)
    out.setdefault('rows_restored', 0)
    out['rows_deleted'] = 0           # V2 created no rows, so restore removes none
    return out


def default_roster(db_path):
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), 'data', 'engine',
                        'congestion.json')


# ------------------------------------------------------------------ dry run
def run_dry(db_path, out_dir, protection, roster_path=None, colonless=True, roster=None,
            ledger_path=None):
    """Write the complete ledger, a summary and the residue list.  Changes nothing, anywhere."""
    os.makedirs(out_dir, exist_ok=True)
    roster = list(roster) if roster is not None else load_roster(
        roster_path or default_roster(db_path))
    con = open_ro(db_path)
    lexicon = build_lexicon(con, roster)
    plans = plan_all(con, lexicon, protection, colonless)
    n_interview = con.execute(
        "SELECT COUNT(*) FROM speeches WHERE source='%s'" % IV).fetchone()[0]
    n_rows = con.execute('SELECT COUNT(*) FROM speeches').fetchone()[0]
    iv_words = con.execute(
        "SELECT COALESCE(SUM(nwords),0) FROM speeches WHERE source='%s'" % IV).fetchone()[0]
    con.close()

    ledger_path = ledger_path or os.path.join(out_dir, 'dry_run_ledger.jsonl')
    os.makedirs(os.path.dirname(os.path.abspath(ledger_path)), exist_ok=True)
    counters = collections.Counter()
    by_class = collections.Counter()
    by_cut_class = collections.Counter()
    by_ownership = collections.Counter()
    chars_removed = words_removed = words_kept = words_before = 0
    every_reconstructs = True
    with io.open(ledger_path, 'w', encoding='utf-8', newline='\n') as fh:
        for plan in plans:
            counters['rows_flagged'] += 1
            for t in plan['all_tags']:
                by_ownership[t['ownership']] += 1
            if plan['skip']:
                counters['rows_skipped'] += 1
                for r in plan['skip_all']:
                    counters['skipped_' + r] += 1
                counters['skipped_binding_' + plan['skip']] += 1
            else:
                counters['rows_truncated'] += 1
                chars_removed += plan['removed_chars']
                words_removed += plan['removed_nwords']
                words_kept += plan['kept_nwords']
                words_before += int(plan['nwords'] or 0)
                by_cut_class[plan['cut_class']] += 1
                for cls in plan['classes']:
                    by_class[cls] += 1
            recon = reconstruct(plan['kept_raw'], plan['removed_raw']) == plan['text']
            tiles = ''.join(s['raw'] for s in plan['segments']) == plan['removed_raw']
            every_reconstructs = every_reconstructs and recon and tiles
            fh.write(json.dumps({
                'rowid': plan['rowid'], 'member': plan['member'], 'date': plan['date'],
                'chamber': plan['chamber'], 'package': plan['package'],
                'skip': plan['skip'], 'skip_all': plan['skip_all'],
                'cut_at': plan['cut_at'], 'cut_tag': plan['cut_tag'],
                'cut_class': plan['cut_class'], 'classes': plan['classes'],
                'all_tags': plan['all_tags'],
                'original': {'nwords': plan['nwords'], 'text': plan['text'],
                             'sha256': plan['text_sha256']},
                'kept': {'nwords': plan['kept_nwords'], 'text': plan['kept_text'],
                         'raw': plan['kept_raw']},
                'removed': {'raw': plan['removed_raw'], 'nwords': plan['removed_nwords'],
                            'segments': plan['segments']},
                'reconstructs': recon and tiles,
            }, ensure_ascii=False) + '\n')
    res = residue(plans)
    with io.open(os.path.join(out_dir, 'v2_residue.jsonl'), 'w', encoding='utf-8',
                 newline='\n') as fh:
        for r in res:
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')
    summary = {
        'tool_version': TOOL_VERSION, 'db': db_path, 'db_bytes': os.path.getsize(db_path),
        'colonless_class_E': bool(colonless),
        'protection_rows': len(protection) if protection is not None else 0,
        'speeches_rows': n_rows, 'interview_rows': n_interview, 'interview_words': iv_words,
        'rows_flagged': counters['rows_flagged'],
        'rows_would_truncate': counters['rows_truncated'],
        'rows_would_skip': counters['rows_skipped'],
        'rows_would_insert': 0,
        'chars_removed': chars_removed, 'words_removed': words_removed,
        'words_kept_on_truncated_rows': words_kept,
        'words_before_on_truncated_rows': words_before,
        'share_of_words_kept': round(words_kept / float(words_before), 6) if words_before else None,
        'share_of_interview_words_removed': round(words_removed / float(iv_words), 6)
        if iv_words else None,
        'by_detector_class_on_truncated_rows': dict(by_class),
        'by_cut_class': dict(by_cut_class),
        'tags_by_ownership': dict(by_ownership),
        'counters': {k: v for k, v in sorted(counters.items())},
        'every_row_reconstructs': every_reconstructs,
        'ledger_path': os.path.abspath(ledger_path),
    }
    with io.open(os.path.join(out_dir, 'v2_dry_run_summary.json'), 'w', encoding='utf-8',
                 newline='\n') as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1, sort_keys=True)
    rnd = random.Random(SAMPLE_SEED)
    pool = sorted((p for p in plans if not p['skip']), key=lambda p: p['rowid'])
    sample = rnd.sample(pool, SAMPLE_N) if len(pool) >= SAMPLE_N else pool
    with io.open(os.path.join(out_dir, 'v2_dry_run_sample40.txt'), 'w', encoding='utf-8',
                 newline='\n') as fh:
        for i, plan in enumerate(sorted(sample, key=lambda p: p['rowid']), 1):
            fh.write('%2d. rowid=%-7s %s  %-26s tag=%r [%s]\n'
                     % (i, plan['rowid'], plan['date'], plan['member'][:26], plan['cut_tag'],
                        plan['cut_class']))
            fh.write('    KEEP (%d w)  ...%s\n' % (plan['kept_nwords'], plan['kept_text'][-150:]))
            fh.write('    CUT  (%d w)  %s...\n\n' % (plan['removed_nwords'],
                                                     plan['removed_raw'][:230]))
    return summary, plans


# ------------------------------------------------------------------ cli
def main(argv=None):
    ap = argparse.ArgumentParser(description='repair the stored interview turn bleed (N12 v2)')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='(default) report only, change nothing')
    mode.add_argument('--apply', action='store_true', help='write the repair in ONE transaction')
    mode.add_argument('--restore', action='store_true', help='undo the repair from the ledger')
    ap.add_argument('--db', required=True)
    ap.add_argument('--backup', help='path to a verified backup; required for --apply')
    ap.add_argument('--out', help='directory for the dry-run summary, residue and sample')
    ap.add_argument('--ledger', help='where to write the dry-run ledger (default: inside --out)')
    ap.add_argument('--protected', help='the label-safety inventory json; required for --apply')
    ap.add_argument('--roster', help='congestion.json (default: <db dir>/data/engine/congestion.json)')
    ap.add_argument('--no-colonless', action='store_true',
                    help='leave detector class E (colon-less honorific tags) out')
    args = ap.parse_args(argv)
    colonless = not args.no_colonless
    prot = Protection.load(args.protected) if args.protected else None
    if args.apply:
        print(json.dumps(run_apply(args.db, args.backup, prot, args.roster, colonless),
                         indent=1, sort_keys=True))
    elif args.restore:
        print(json.dumps(run_restore(args.db), indent=1, sort_keys=True))
    else:
        if not args.out:
            ap.error('--dry-run needs --out <dir>')
        if prot is None:
            ap.error('--dry-run needs --protected <json> too: a dry run without rules (b) and '
                     '(c) would not describe what --apply does')
        summary, _plans = run_dry(args.db, args.out, prot, args.roster, colonless,
                                  ledger_path=args.ledger)
        print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    try:
        sys.exit(main())
    except Refused as _refusal:
        # A precondition the operator must satisfy: say so in one line and change nothing.
        print('REFUSED: %s' % _refusal)
        sys.exit(2)
