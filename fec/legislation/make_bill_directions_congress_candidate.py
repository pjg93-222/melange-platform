# -*- coding: utf-8 -*-
"""Build a user-gated Congress-qualified bill-directions CSV candidate."""
import argparse
import csv
import hashlib
import os
import sys
import tempfile


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from legislation import bill_coverage_audit as BCA  # noqa: E402  (import-safe pure helpers)
import bill_rules as BR  # noqa: E402

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except AttributeError:
    pass


WORKTREE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APPROVED_OUT = os.path.join(WORKTREE_ROOT, 'audits', 'legfix2')
OUTPUT_NAME = 'bill_directions.with_congress.csv.candidate'
USER_GATE_COMMENT = (
    '# USER-GATED CANDIDATE: adoption requires Paul approval; fields beginning '
    '= + - @ are prefixed with a single quote'
)


class CandidateError(RuntimeError):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _is_physically_contained(path, root):
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _congress_sort(value):
    text = str(value or '').strip()
    return (
        0 if text.isdigit() else 1,
        int(text) if text.isdigit() else 0,
        text,
    )


def neutralize_csv_field(value):
    """Prevent spreadsheet formula interpretation without changing other fields."""
    text = '' if value is None else str(value)
    return "'" + text if text.startswith(('=', '+', '-', '@')) else text


def resolve_candidate_rows(source_rows, archive_rows):
    """Return expanded candidate rows and original-row resolution counts."""
    instances_by_type = BCA.archive_instances_by_type(archive_rows)
    valid_instances_by_type = {
        key: [
            item for item in instances
            if item['congress']
            and item['congress'] != BCA.NON_INTEGRAL_CONGRESS
        ]
        for key, instances in instances_by_type.items()
    }
    output = []
    counts = {
        'ARCHIVE_UNIQUE': 0,
        'AMBIGUOUS': 0,
        'AMBIGUOUS-TITLES': 0,
        'ABSENT': 0,
    }
    for source_row in source_rows:
        row = dict(source_row)
        typed = BR.bill_type_key(row.get('Bill'))
        instances = valid_instances_by_type.get(typed, []) if typed else []
        congresses = sorted(
            {item['congress'] for item in instances},
            key=_congress_sort,
        )
        if not instances:
            counts['ABSENT'] += 1
            candidate = dict(row)
            candidate['Congress'] = ''
            candidate['Resolution'] = 'ABSENT'
            output.append(candidate)
        elif len(congresses) > 1:
            counts['AMBIGUOUS'] += 1
            for congress in congresses:
                candidate = dict(row)
                candidate['Congress'] = congress
                candidate['Resolution'] = 'AMBIGUOUS'
                output.append(candidate)
        elif len(instances) > 1:
            counts['AMBIGUOUS-TITLES'] += 1
            for instance in instances:
                candidate = dict(row)
                candidate['Congress'] = instance['congress']
                candidate['Resolution'] = 'AMBIGUOUS-TITLES'
                output.append(candidate)
        else:
            counts['ARCHIVE_UNIQUE'] += 1
            candidate = dict(row)
            candidate['Congress'] = instances[0]['congress']
            candidate['Resolution'] = 'ARCHIVE_UNIQUE'
            output.append(candidate)
    return output, counts


def read_bill_directions(path):
    with open(path, encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise CandidateError('bill_directions.csv has no header')
        fieldnames = list(reader.fieldnames)
        if 'Congress' in fieldnames or 'Resolution' in fieldnames:
            raise CandidateError(
                'input already carries Congress or Resolution columns')
        return fieldnames, list(reader)


def atomic_write_candidate(
    path, original_fields, rows, archive_name, archive_sha256
):
    path = os.path.realpath(os.path.abspath(path))
    worktree_root = os.path.realpath(WORKTREE_ROOT)
    expected_path = os.path.realpath(
        os.path.join(APPROVED_OUT, OUTPUT_NAME))
    if (
        path != expected_path
        or not _is_physically_contained(path, worktree_root)
    ):
        raise CandidateError(
            'candidate write path physically escaped its envelope: %s' % path)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix='.' + os.path.basename(path) + '.',
        suffix='.tmp',
        dir=parent,
    )
    output_fields = list(original_fields) + ['Congress', 'Resolution']
    try:
        with os.fdopen(
            fd, 'w', encoding='utf-8', newline=''
        ) as handle:
            writer = csv.writer(handle, lineterminator='\n')
            writer.writerow(
                [USER_GATE_COMMENT] + [''] * (len(output_fields) - 1))
            writer.writerow([
                '# ARCHIVE PROVENANCE: filename=%s sha256=%s'
                % (archive_name, archive_sha256)
            ] + [''] * (len(output_fields) - 1))
            writer.writerow([
                neutralize_csv_field(value) for value in output_fields])
            for row in rows:
                writer.writerow([
                    neutralize_csv_field(row.get(field, ''))
                    for field in output_fields
                ])
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Build a user-gated Congress-qualified directions candidate')
    parser.add_argument(
        '--data-root',
        required=True,
        help='read-only data root containing bill_directions.csv and archive xlsx',
    )
    parser.add_argument(
        '--out',
        default=APPROVED_OUT,
        help='must resolve to the worktree audits/legfix2 directory',
    )
    args = parser.parse_args(argv)
    data_root = os.path.realpath(os.path.abspath(args.data_root))
    worktree_root = os.path.realpath(WORKTREE_ROOT)
    approved_out = os.path.realpath(APPROVED_OUT)
    out = os.path.realpath(os.path.abspath(args.out))
    if (
        out != approved_out
        or not _is_physically_contained(out, worktree_root)
    ):
        print(
            'ERROR: --out must be %s' % APPROVED_OUT,
            file=sys.stderr,
        )
        return 2
    directions_path = os.path.join(data_root, 'data', 'legislation', 'bill_directions.csv')
    if not os.path.isfile(directions_path):
        print(
            'ERROR: bill_directions.csv missing under data root',
            file=sys.stderr,
        )
        return 2
    try:
        archive_name, archive_rows = BCA.load_archive(data_root)
        if not archive_name:
            raise CandidateError(
                'no congress_bills_archive_*.xlsx under data root')
        archive_path = os.path.realpath(
            os.path.join(data_root, archive_name))
        archive_sha256 = sha256_file(archive_path)
        original_fields, source_rows = read_bill_directions(directions_path)
        candidate_rows, counts = resolve_candidate_rows(
            source_rows, archive_rows)
        output_path = os.path.join(out, OUTPUT_NAME)
        atomic_write_candidate(
            output_path,
            original_fields,
            candidate_rows,
            archive_name,
            archive_sha256,
        )
    except (CandidateError, OSError, ValueError, KeyError) as exc:
        print('ERROR: %s' % exc, file=sys.stderr)
        return 2
    print(
        'candidate summary: archive_unique=%d ambiguous=%d '
        'ambiguous_titles=%d absent=%d output_rows=%d'
        % (
            counts['ARCHIVE_UNIQUE'],
            counts['AMBIGUOUS'],
            counts['AMBIGUOUS-TITLES'],
            counts['ABSENT'],
            len(candidate_rows),
        )
    )
    print('candidate written: %s' % output_path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
