"""Synthetic PE files: the binding accepts only "unsigned bytes + appended certificate table"."""
import hashlib
import json
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from authenticode_binding import BindingError, main, verify


def unsigned_pe(magic=0x10B, payload=b'NSIS-payload' * 300):
    optional = 0x40 + 24
    directories = optional + (96 if magic == 0x10B else 112)
    head = bytearray(directories + 16 * 8)
    head[:2] = b'MZ'
    struct.pack_into('<I', head, 0x3C, 0x40)
    head[0x40:0x44] = b'PE\0\0'
    struct.pack_into('<H', head, optional, magic)
    struct.pack_into('<I', head, directories - 4, 16)
    return bytes(head) + payload + b'\x01'  # odd length: signing must pad


def sign(data, certificate=b'\x30' * 40, padding=b'\0'):
    optional = 0x40 + 24
    magic = struct.unpack_from('<H', data, optional)[0]
    security = optional + (96 if magic == 0x10B else 112) + 32
    out = bytearray(data)
    struct.pack_into('<I', out, optional + 64, 0x1234ABCD)
    pad = (-len(out)) % 8
    out += padding * pad
    struct.pack_into('<II', out, security, len(out), len(certificate))
    return bytes(out + certificate)


def files(tmp_path, unsigned, signed):
    u, s = tmp_path / 'unsigned.exe', tmp_path / 'signed.exe'
    u.write_bytes(unsigned)
    s.write_bytes(signed)
    return str(u), str(s)


@pytest.mark.parametrize('magic', [0x10B, 0x20B])
def test_signed_copy_of_the_unsigned_bytes_is_accepted(tmp_path, magic, capsys):
    data = unsigned_pe(magic)
    signed = sign(data)
    result = verify(*files(tmp_path, data, signed))
    assert result['unsignedBytes'] == len(data) and result['certificateTableBytes'] == 40
    # The reported digests are of exactly the compared bytes, for the caller to bind to its published hashes.
    assert result['unsignedSha256'] == hashlib.sha256(data).hexdigest()
    assert result['signedSha256'] == hashlib.sha256(signed).hexdigest()
    assert main(['--unsigned', str(tmp_path / 'unsigned.exe'), '--signed', str(tmp_path / 'signed.exe')]) == 0
    assert json.loads(capsys.readouterr().out) == result


def test_any_change_outside_the_signature_fields_is_refused(tmp_path):
    data = unsigned_pe()
    for position in (2, 0x40 + 24 + 10, len(data) - 1):  # DOS header, optional header, NSIS payload tail
        signed = bytearray(sign(data))
        signed[position] ^= 0xFF
        with pytest.raises(BindingError, match='content differs'):
            verify(*files(tmp_path, data, bytes(signed)))


def test_signature_layout_violations_are_refused(tmp_path):
    data = unsigned_pe()
    with pytest.raises(BindingError, match='already carries'):
        verify(*files(tmp_path, sign(data)[:len(data)], sign(data)))
    with pytest.raises(BindingError, match='padding'):
        verify(*files(tmp_path, data, sign(data, padding=b'\x07')))
    with pytest.raises(BindingError, match='appended'):
        verify(*files(tmp_path, data, sign(data) + b'trailing'))
    with pytest.raises(BindingError, match='appended'):
        verify(*files(tmp_path, data + b'x' * 64, sign(data)))
    with pytest.raises(BindingError, match='not a PE'):
        verify(*files(tmp_path, b'ZZ' + data[2:], sign(data)))
