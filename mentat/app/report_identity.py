"""Published report names and cycle-specific lineage filenames."""
import re


def identity(name):
    match = re.fullmatch(r'(.+) Industry Spending (Overview|Data)(?: (20\d\d)[-–](20\d\d))?\.xlsx', name)
    if not match:
        return None
    industry, kind, start, end = match.groups()
    if start and (int(end) != int(start) + 1 or int(end) % 2):
        return None
    cycle = f'{start}-{end}' if start else ''
    return {'industry': industry, 'kind': kind.lower(), 'cycle_label': cycle,
            'label': f'{industry} {cycle}' if cycle else industry}


def companion_name(info):
    suffix = ' ' + info['cycle_label'] if info['cycle_label'] else ''
    return info['industry'] + ' Industry Spending Data' + suffix + '.xlsx'


def lineage_paths(cache, database_hash, cycle_label=''):
    token = '-' + cycle_label.replace('-', '') if cycle_label else ''
    return (cache / f'manifest{token}-{database_hash[:16]}.json',
            cache / f'industry_data{token}-{database_hash[:16]}.json')
