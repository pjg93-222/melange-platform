"""Prepare a Terrier district pointer with the existing protected release key; never upload.

The data release must first be byte-verified and public. This command checks the
public release/tag/manifest before signing, validates with the native protocol,
and retains an independent sequence ledger. It never generates or exports keys.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from desktop_admin import Store, canonical

REPOSITORY = 'pjg93-222/Geo'


def require(value, message):
    if not value:
        raise ValueError(message)


def api(path, *, binary=False):
    command = ['gh', 'api', 'repos/' + REPOSITORY + '/' + path]
    if binary:
        command += ['--header', 'Accept: application/octet-stream']
    result = subprocess.check_output(command)
    return result if binary else json.loads(result)


def prepare(options):
    require(type(options.sequence) is int and 0 < options.sequence <= 2**53 - 1, 'Positive safe sequence required')
    require(re.fullmatch(r'district-testsets-\d{4}-\d{2}-\d{2}(?:-[a-z0-9][a-z0-9.-]{0,40})?', options.tag), 'Invalid data release tag')
    require(re.fullmatch(r'[a-f0-9]{40}', options.commit) and options.commit != '0' * 40, 'Full reviewed release commit required')
    store = Store(options.state)
    require(store.db.is_file() and store.config_path.is_file(), 'Existing publisher required; initialization is forbidden')
    trust = json.loads(options.trust.read_text(encoding='utf-8-sig'))
    keys = trust['updatePublicKeysByApp']['terrier']
    signing = store.config()['apps']['terrier']
    require(signing['active'] in keys and signing['publicKeys'][signing['active']] == keys[signing['active']], 'Existing signing key does not match installed Terrier trust')
    release = api('releases/tags/' + options.tag)
    require(release['tag_name'] == options.tag and release['draft'] is False and release['prerelease'] is True and
            release['target_commitish'] == options.commit, 'Verified public data prerelease required')
    tag = api('git/ref/tags/' + options.tag)
    require(tag['ref'] == 'refs/tags/' + options.tag and tag['object']['type'] == 'commit' and
            tag['object']['sha'] == options.commit, 'Data release tag differs from reviewed commit')
    assets = [asset for asset in release['assets'] if asset['name'] == 'manifest.json']
    require(len(assets) == 1 and 0 < assets[0]['size'] <= 2 * 1024 * 1024, 'Unique bounded manifest asset required')
    manifest = options.manifest.read_bytes()
    require(api('releases/assets/' + str(assets[0]['id']), binary=True) == manifest, 'Public manifest differs from reviewed local bytes')
    payload = {'schemaVersion': 1, 'purpose': 'terrier-public-district-data', 'appId': 'terrier',
               'repository': REPOSITORY, 'catalogSequence': options.sequence, 'releaseTag': options.tag,
               'releaseCommit': options.commit,
               'manifest': {'asset': 'manifest.json', 'bytes': len(manifest), 'sha256': hashlib.sha256(manifest).hexdigest()}}
    digest = hashlib.sha256(canonical(payload)).hexdigest()
    # Separate from both binary-release sequences and the private recipient dataset journal.
    connection = sqlite3.connect(store.root / 'github-district-catalogues.sqlite3', timeout=15)
    try:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('CREATE TABLE IF NOT EXISTS prepared(sequence INTEGER PRIMARY KEY, digest TEXT NOT NULL, envelope TEXT NOT NULL)')
        row = connection.execute('SELECT sequence,digest,envelope FROM prepared ORDER BY sequence DESC LIMIT 1').fetchone()
        require(not row or options.sequence > row[0] or (options.sequence == row[0] and digest == row[1]), 'District catalogue rollback or sequence reuse refused')
        envelope = json.loads(row[2]) if row and options.sequence == row[0] else store.sign('terrier', payload)
        require(base64.b64decode(envelope['payload'], validate=True) == canonical(payload), 'Prepared envelope differs from the requested catalogue identity')
        protocol = Path(__file__).resolve().parents[1] / 'client/github-district-protocol.cjs'
        script = "const fs=require('node:fs'),p=require(process.argv[1]),v=JSON.parse(fs.readFileSync(0,'utf8'));p.manifest(Buffer.from(v.manifest,'base64'),p.pointer(v.envelope,v.keys));process.stdout.write('verified');"
        result = subprocess.run([options.node, '-e', script, str(protocol)], input=json.dumps({'envelope': envelope, 'keys': keys,
                                'manifest': base64.b64encode(manifest).decode('ascii')}), text=True, capture_output=True, check=True)
        require(result.stdout == 'verified', 'Native district protocol verification failed')
        content = json.dumps(envelope, indent=2).encode('utf-8') + b'\n'
        require(not options.output.exists() or options.output.read_bytes() == content, 'Output already contains different pointer bytes')
        options.output.parent.mkdir(parents=True, exist_ok=True)
        if not options.output.exists():
            with options.output.open('xb') as stream:
                stream.write(content)
        connection.execute('INSERT OR IGNORE INTO prepared VALUES (?,?,?)', (options.sequence, digest, json.dumps(envelope)))
        connection.commit()
    finally:
        connection.close()
    return {'repository': REPOSITORY, 'catalogSequence': options.sequence, 'pointerDigest': digest,
            'manifestSha256': payload['manifest']['sha256'], 'releaseTag': options.tag}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--trust', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--sequence', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--node', default='node')
    print(json.dumps(prepare(parser.parse_args()), sort_keys=True))


if __name__ == '__main__':
    main()
