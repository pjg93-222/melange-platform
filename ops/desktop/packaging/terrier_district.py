"""Read-only district validation, before any account/secret/migration writes."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

RUNTIME_ROLES = ('contacts.sqlite3', 'density.json', 'roads.sqlite3', 'scores.sqlite3', 'street.sqlite3', 'walk.sqlite3')


def validate_github_runtime(source, data, district):
    """Six immutable runtime inputs; inspect only headers and source metadata."""
    if not Path(source).is_absolute() or not Path(data).is_absolute():
        raise ValueError('Absolute source and data paths are required.')
    original = Path(data)
    if original.is_symlink() or not original.is_dir():
        raise ValueError('The GitHub district cache must be a regular directory.')
    source, data = Path(source).resolve(strict=True), original.resolve(strict=True)
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    from tools import districts
    definition = districts.get(district)
    before = {}
    for role in RUNTIME_ROLES:
        file = data / role
        if file.is_symlink() or not file.is_file() or file.resolve().parent != data:
            raise ValueError('A GitHub district runtime role is missing or unsafe.')
        info = file.stat()
        before[role] = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if role.endswith('.sqlite3'):
            if any(file.with_name(role + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
                raise ValueError('A GitHub district database has SQLite sidecars.')
            with file.open('rb') as incoming:
                header = incoming.read(100)
            if len(header) != 100 or header[:16] != b'SQLite format 3\0' or header[18:20] != b'\x01\x01':
                raise ValueError('A GitHub district database must use rollback mode.')
            key = 'contacts_source_sha256' if role == 'scores.sqlite3' else 'source_sha256'
            if role != 'roads.sqlite3':
                # Walk's existing store uses meta/raw strings; the other stores
                # use metadata/JSON. Preserve each installed reader's format.
                table = 'meta' if role == 'walk.sqlite3' else 'metadata'
                with closing(sqlite3.connect(file.as_uri() + '?mode=ro&immutable=1', uri=True)) as connection:
                    connection.execute('PRAGMA query_only=ON')
                    rows = connection.execute('SELECT value FROM ' + table + ' WHERE key=?', (key,)).fetchall()
                if len(rows) != 1 or (rows[0][0] if role == 'walk.sqlite3' else json.loads(rows[0][0])) != definition['sha256']:
                    raise ValueError('A GitHub runtime store belongs to another source.')
    districts.check_data_dir(definition, data)
    for role in RUNTIME_ROLES:
        file = data / role
        info = file.stat()
        if file.is_symlink() or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != before[role]:
            raise ValueError('GitHub district data changed during validation.')
        if role.endswith('.sqlite3') and any(file.with_name(role + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
            raise ValueError('A GitHub district database acquired SQLite sidecars.')
    return {'district': definition['id'], 'validated': True}


def validate_district(source, data, district, *, legacy=False, state_root=None, github_data=False):
    source, data = Path(source).resolve(), Path(data).resolve()
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    from tools import districts
    definition = districts.get(district)
    if github_data and (not legacy or state_root is None):
        raise ValueError('GitHub cache requires the original approved owner state.')
    state = Path(state_root).resolve() if github_data else data
    if github_data:
        if state == data or state.is_relative_to(data) or data.is_relative_to(state):
            raise ValueError('GitHub district data must be separate from approved owner state.')
        validate_github_runtime(source, data, district)
    if legacy:
        for name in ('street.sqlite3', 'scores.sqlite3', 'walk.sqlite3'):
            filename = data / name
            if not filename.is_file() or filename.is_symlink() or filename.resolve().parent != data:
                raise ValueError('The selected district is missing its existing data or account files.')
        for name in ('accounts.sqlite3', '.secret'):
            filename = state / name
            if not filename.is_file() or filename.is_symlink() or filename.resolve().parent != state:
                raise ValueError('The selected district is missing its approved owner state.')
        with closing(sqlite3.connect((state / 'accounts.sqlite3').as_uri() + '?mode=ro', uri=True)) as connection:
            if connection.execute('PRAGMA quick_check').fetchone() != ('ok',):
                raise ValueError('The selected district account database failed its integrity check.')
    districts.check_data_dir(definition, data)
    return {'district': definition['id'], 'validated': True}


def main(owner=False, github_runtime=False):
    try:
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            raise ValueError('District validation request is too large.')
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ValueError('Invalid district validation request.')
        if github_runtime:
            if owner or set(request) != {'sourceRoot', 'dataRoot', 'district'}:
                raise ValueError('Invalid GitHub runtime metadata request.')
            print(json.dumps(validate_github_runtime(request['sourceRoot'], request['dataRoot'], request['district'])))
            return 0
        keys = {'sourceRoot', 'dataRoot', 'district', 'legacy'}
        github = set(request) == keys | {'stateRoot', 'githubDistrictData'}
        if (set(request) != keys and not github) or not isinstance(request['legacy'], bool):
            raise ValueError('Invalid district validation request.')
        if github and (owner or request['githubDistrictData'] is not True or request['legacy'] is not True or not isinstance(request['stateRoot'], str)):
            raise ValueError('Invalid GitHub owner state validation request.')
        result = validate_district(request['sourceRoot'], request['dataRoot'], request['district'], legacy=request['legacy'],
                                   state_root=request.get('stateRoot'), github_data=github)
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
