"""Copy installed tool binaries only; never browser profiles or developer modules.

The receipt deliberately leaves redistribution review incomplete.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--node', type=Path, required=True)
    parser.add_argument('--browser', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    native = options.output.resolve()
    native.mkdir(parents=True, exist_ok=True)
    node = native / 'Node'
    browser = native / 'Chromium'
    if node.exists() or browser.exists():
        raise RuntimeError('Choose unused Node and Chromium output directories')
    environment = {key: value for key, value in os.environ.items() if key.upper() not in {'NODE_OPTIONS', 'NODE_PATH'}}
    version = subprocess.check_output([str(options.node), '--version'], env=environment, text=True).strip()
    if not version.startswith('v') or any(c not in 'v0123456789.' for c in version):
        raise RuntimeError('Unexpected Node version')
    license_url = f'https://raw.githubusercontent.com/nodejs/node/{version}/LICENSE'
    license_text = urllib.request.urlopen(license_url, timeout=30).read()
    if not license_text.startswith(b'Node.js is licensed'):
        raise RuntimeError('Unexpected Node license content')
    node.mkdir()
    shutil.copy2(options.node, node / 'node.exe')
    (node / 'LICENSE.txt').write_bytes(license_text)
    if not (options.browser / 'chrome.exe').is_file():
        raise RuntimeError('Browser program directory missing chrome.exe')
    forbidden = {'Default', 'Local State', 'User Data', 'Cookies', 'Login Data', 'History'}
    if any((options.browser / name).exists() for name in forbidden):
        raise RuntimeError('Browser source contains a user profile')
    shutil.copytree(options.browser, browser)
    receipt = {'nodeVersion': version, 'nodeLicenseSource': license_url,
               'nodeSha256': hashlib.file_digest((node / 'node.exe').open('rb'), 'sha256').hexdigest(),
               'browserSha256': hashlib.file_digest((browser / 'chrome.exe').open('rb'), 'sha256').hexdigest(),
               'redistributionApproved': False,
               'remainingReview': 'Browser third-party credits/terms, native redistribution and source obligations'}
    (native / 'node-browser-inventory.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps({'nativeRoot': str(native), **receipt}))


if __name__ == '__main__':
    main()
