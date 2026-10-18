"""Prove a signed Windows installer is the hosted unsigned build plus only an appended Authenticode signature.

Authenticode signing of a PE changes exactly three things: the optional-header CheckSum (4 bytes), the
IMAGE_DIRECTORY_ENTRY_SECURITY entry (8 bytes), and the file tail, where the certificate table is appended after
at most 7 zero padding bytes. Everything else, including an NSIS installer's appended payload, is unchanged.
This check binds the locally signed bytes to the unsigned bytes whose hash the hosted build recorded.
Standard library only; run with `python -I`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys

CHUNK = 1 << 20


class BindingError(ValueError):
    """The signed file is not the unsigned file plus an appended signature."""


def _layout(head: bytes):
    """Offsets of CheckSum and of the security directory entry, from the PE headers in `head`."""
    if head[:2] != b'MZ' or len(head) < 0x40:
        raise BindingError('not a PE file')
    pe = struct.unpack_from('<I', head, 0x3C)[0]
    optional = pe + 24
    if head[pe:pe + 4] != b'PE\0\0' or len(head) < optional + 2:
        raise BindingError('missing PE signature')
    magic = struct.unpack_from('<H', head, optional)[0]
    directories = {0x10B: optional + 96, 0x20B: optional + 112}.get(magic)
    if directories is None:
        raise BindingError('unknown optional header magic')
    security = directories + 4 * 8
    if struct.unpack_from('<I', head, directories - 4)[0] <= 4 or len(head) < security + 8:
        raise BindingError('no security directory slot')
    return optional + 64, security


def verify(unsigned_path: str, signed_path: str) -> dict:
    with open(unsigned_path, 'rb') as unsigned, open(signed_path, 'rb') as signed:
        head_u, head_s = unsigned.read(4096), signed.read(4096)
        checksum, security = _layout(head_u)
        if _layout(head_s) != (checksum, security):
            raise BindingError('PE header layout differs')
        if struct.unpack_from('<II', head_u, security) != (0, 0):
            raise BindingError('the unsigned input already carries a signature')
        table, size = struct.unpack_from('<II', head_s, security)
        unsigned.seek(0, 2)
        signed.seek(0, 2)
        length_u, length_s = unsigned.tell(), signed.tell()
        if not (length_u <= table < length_u + 8 and size >= 8 and table + size == length_s):
            raise BindingError('the certificate table is not appended at the end of the unsigned bytes')
        masked = set(range(checksum, checksum + 4)) | set(range(security, security + 8))
        unsigned.seek(0)
        signed.seek(0)
        hash_u, hash_s = hashlib.sha256(), hashlib.sha256()
        offset = 0
        while offset < length_u:
            a, b = unsigned.read(CHUNK), signed.read(min(CHUNK, length_u - offset))
            if len(a) != len(b) or not a:
                raise BindingError('short read')
            if a != b:
                for index in range(len(a)):
                    if a[index] != b[index] and offset + index not in masked:
                        raise BindingError(f'content differs at byte {offset + index}')
            hash_u.update(a)
            hash_s.update(b)
            offset += len(a)
        padding = signed.read(table - length_u)
        if padding.strip(b'\0'):
            raise BindingError('non-zero padding before the certificate table')
        hash_s.update(padding)
        tail = signed.read()
        if len(tail) != size:
            raise BindingError('short read of the certificate table')
        hash_s.update(tail)
    # The digests of exactly the bytes compared above, so a caller can bind them to the hashes it publishes.
    return {'unsignedBytes': length_u, 'signedBytes': length_s, 'certificateTableOffset': table, 'certificateTableBytes': size,
            'unsignedSha256': hash_u.hexdigest(), 'signedSha256': hash_s.hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unsigned', required=True)
    parser.add_argument('--signed', required=True)
    args = parser.parse_args(argv)
    try:
        result = verify(args.unsigned, args.signed)
    except (BindingError, OSError, struct.error) as error:
        print('Binding refused: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
