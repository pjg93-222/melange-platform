# -*- coding: utf-8 -*-
"""Build a review queue for tweets whose linked media may carry material stance evidence."""
from __future__ import annotations

import csv
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import ROOT


URL = re.compile(r'https://t\.co/\S+', re.I)


def stripped_text(value):
    return re.sub(r'\s+', ' ', URL.sub('', str(value or ''))).strip()


def build_queue(root=ROOT):
    final_path = os.path.join(root, 'data', 'stance', 'tweet_stance_final.csv')
    final = {}
    if os.path.exists(final_path):
        with io.open(final_path, encoding='utf-8-sig', newline='') as handle:
            final = {str(row.get('quorum_id') or ''): row for row in csv.DictReader(handle)}
    rows = []
    with io.open(os.path.join(root, 'data', 'stance', 'quorum_member_tweets.csv'), encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            text = row.get('Content') or ''
            links = URL.findall(text)
            if not links:
                continue
            clean = stripped_text(text)
            stance = final.get(str(row.get('Quorum ID') or ''), {})
            tier = (stance.get('tier') or '').lower()
            if len(clean) >= 100 and tier != 'review':
                continue
            rows.append({
                'quorum_id': row.get('Quorum ID'),
                'member': row.get('member'),
                'date': row.get('dt'),
                'source_url': row.get('Source URL'),
                'media_links': ';'.join(links),
                'current_stance': stance.get('stack_stance') or row.get('stance'),
                'current_tier': stance.get('tier') or '',
                'text_without_media_link': clean,
                'review_status': 'needs-media-resolution',
            })
    out_path = os.path.join(root, 'data', 'stance', 'tweet_image_review_queue.csv')
    fields = list(rows[0]) if rows else [
        'quorum_id', 'member', 'date', 'source_url', 'media_links', 'current_stance',
        'current_tier', 'text_without_media_link', 'review_status',
    ]
    with io.open(out_path + '.tmp', 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(out_path + '.tmp', out_path)
    print('wrote %s (%d image-review candidates)' % (out_path, len(rows)))


if __name__ == '__main__':
    build_queue()
