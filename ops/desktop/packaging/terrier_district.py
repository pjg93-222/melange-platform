"""Read-only district validation, before any account/secret/migration writes."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys


def validate_district(source, data, district, *, legacy=False):
    source, data = Path(source).resolve(), Path(data).resolve()
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    from tools import districts
    definition = districts.get(district)
    if legacy:
        for name in ('accounts.sqlite3', '.secret', 'street.sqlite3', 'scores.sqlite3', 'walk.sqlite3'):
            filename = data / name
            if not filename.is_file() or filename.is_symlink() or filename.resolve().parent != data:
                raise ValueError('The selected district is missing its existing data or account files.')
        with closing(sqlite3.connect((data / 'accounts.sqlite3').as_uri() + '?mode=ro', uri=True)) as connection:
            if connection.execute('PRAGMA quick_check').fetchone() != ('ok',):
                raise ValueError('The selected district account database failed its integrity check.')
    districts.check_data_dir(definition, data)
    return {'district': definition['id'], 'validated': True}


def main(owner=False):
    try:
        request = json.loads(sys.stdin.buffer.read(16385))
        if set(request) != {'sourceRoot', 'dataRoot', 'district', 'legacy'} or not isinstance(request['legacy'], bool):
            raise ValueError('Invalid district validation request.')
        result = validate_district(request['sourceRoot'], request['dataRoot'], request['district'], legacy=request['legacy'])
        if owner:
            if not request['legacy']:
                raise ValueError('An explicit existing account mapping is required.')
            database = Path(request['dataRoot']).resolve() / 'accounts.sqlite3'
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
                rows = connection.execute("SELECT id, username, is_active, is_staff FROM auth_user WHERE username = 'admin'").fetchall()
            if len(rows) != 1 or rows[0][2:] != (1, 1) or rows[0][0] < 1:
                raise ValueError('The explicitly mapped existing administrator is unavailable.')
            result.update(localUserId=str(rows[0][0]), localUsername=rows[0][1])
        print(json.dumps(result))
        return 0
    except Exception:
        print(json.dumps({'error': 'The selected district data could not be verified.'}))
        return 1
