"""Stronger lossless ZIP DEFLATE for the bounded email-package fallback.

Classic single-disk ZIP records follow PKWARE APPNOTE 6.3.10 sections
4.3.7, 4.3.12 and 4.3.16:
https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT
No zipfile internals or process-wide compressor replacement are used.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import zipfile
import zlib

UINT32_LIMIT = 0xFFFFFFFF
UINT16_LIMIT = 0xFFFF
LOCAL = struct.Struct('<I5H3I2H')
CENTRAL = struct.Struct('<I6H3I5H2I')
END = struct.Struct('<I4H2IH')


def raw_deflate(data, *, iterations=5):
    """Validate the zlib envelope before extracting its raw DEFLATE stream."""
    try:
        from zopfli.zlib import compress
    except ImportError as exc:
        raise RuntimeError('Oversized email ZIP requires zopfli==0.4.3; see fec/requirements-honors.txt') from exc
    encoded = compress(data, numiterations=iterations)
    if (len(encoded) < 6 or encoded[0] & 15 != 8 or encoded[0] >> 4 > 7
            or encoded[1] & 32 or int.from_bytes(encoded[:2], 'big') % 31):
        raise ValueError('Invalid or dictionary-based zopfli zlib envelope')
    if zlib.decompress(encoded) != data:
        raise ValueError('Zopfli zlib output differs from its input')
    raw = encoded[2:-4]
    decoder = zlib.decompressobj(-15)
    decoded = decoder.decompress(raw) + decoder.flush()
    if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail or decoded != data:
        raise ValueError('Zopfli raw DEFLATE output differs from its input')
    return raw


def _classic_entry(info):
    if (info.file_size >= UINT32_LIMIT or info.compress_size >= UINT32_LIMIT
            or info.header_offset >= UINT32_LIMIT or info.extract_version >= 45):
        raise ValueError('ZIP64 and files/offsets at or above 4 GiB are outside email ZIP scope')
    if info.is_dir() or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise ValueError('Email fallback accepts only ordinary stored/DEFLATE file entries')
    if info.flag_bits & ~0x800:
        raise ValueError('Encrypted, streaming or special-flag ZIP entries are outside email ZIP scope')
    offset = 0
    while offset < len(info.extra):
        if offset + 4 > len(info.extra):
            raise ValueError('Malformed ZIP extra field')
        kind, size = struct.unpack_from('<HH', info.extra, offset)
        if kind == 1:
            raise ValueError('ZIP64 extra fields are outside email ZIP scope')
        offset += 4 + size
        if offset > len(info.extra):
            raise ValueError('Truncated ZIP extra field')


def recompress_zip(source, destination, *, iterations=5):
    """Re-encode a generated ZIP, preserving extracted bytes and entry metadata.

    The caller owns atomic replacement and the unchanged 14 MiB budget gate.
    Every output entry is independently extracted and SHA-256 checked before
    this function returns. It explicitly does not implement ZIP64 or encryption.
    """
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError('ZIP recompression requires a separate output path')
    expected = []
    with zipfile.ZipFile(source) as original:
        infos = original.infolist()
        if len(infos) >= UINT16_LIMIT or source.stat().st_size >= UINT32_LIMIT:
            raise ValueError('ZIP64/archive size is outside email ZIP scope')
        if len({info.filename for info in infos}) != len(infos):
            raise ValueError('Duplicate archive names are not supported')
        for info in infos:
            _classic_entry(info)
        central_rows = []
        with destination.open('wb') as output:
            for info in infos:
                data = original.read(info)  # zipfile verifies the source CRC.
                compressed = raw_deflate(data, iterations=iterations) if data else b''
                method = zipfile.ZIP_DEFLATED if data else zipfile.ZIP_STORED
                filename = info.filename.encode('utf-8' if info.flag_bits & 0x800 else 'cp437')
                extra, comment = info.extra, info.comment
                if any(len(value) > UINT16_LIMIT for value in (filename, extra, comment)):
                    raise ValueError('ZIP metadata exceeds classic ZIP limits')
                start = output.tell()
                if max(start, len(data), len(compressed)) >= UINT32_LIMIT:
                    raise ValueError('ZIP64/4 GiB output is outside email ZIP scope')
                year, month, day, hour, minute, second = info.date_time
                dos_time = hour << 11 | minute << 5 | second // 2
                dos_date = (year - 1980) << 9 | month << 5 | day
                crc = zlib.crc32(data) & UINT32_LIMIT
                output.write(LOCAL.pack(0x04034B50, 20, info.flag_bits, method,
                    dos_time, dos_date, crc, len(compressed), len(data), len(filename), len(extra)))
                output.write(filename + extra)
                output.write(compressed)
                central_rows.append(CENTRAL.pack(0x02014B50,
                    info.create_system << 8 | info.create_version, 20, info.flag_bits, method,
                    dos_time, dos_date, crc, len(compressed), len(data), len(filename),
                    len(extra), len(comment), 0, info.internal_attr, info.external_attr, start)
                    + filename + extra + comment)
                expected.append((info.filename, hashlib.sha256(data).hexdigest(), crc, info))
            central_start = output.tell()
            for row in central_rows:
                output.write(row)
            central_size = output.tell() - central_start
            if output.tell() + END.size + len(original.comment) >= UINT32_LIMIT:
                raise ValueError('ZIP64/4 GiB directory is outside email ZIP scope')
            output.write(END.pack(0x06054B50, 0, 0, len(infos), len(infos), central_size,
                                  central_start, len(original.comment)))
            output.write(original.comment)
        archive_comment = original.comment
    with zipfile.ZipFile(destination) as verified:
        if verified.namelist() != [row[0] for row in expected] or verified.comment != archive_comment:
            raise ValueError('Recompressed ZIP names/comment differ')
        for name, digest, crc, prior in expected:
            info = verified.getinfo(name)
            if hashlib.sha256(verified.read(name)).hexdigest() != digest or info.CRC != crc:
                raise ValueError('Recompressed ZIP content/CRC differs: ' + name)
            for key in ('date_time', 'external_attr', 'internal_attr', 'extra', 'comment', 'file_size', 'flag_bits'):
                if getattr(info, key) != getattr(prior, key):
                    raise ValueError('Recompressed ZIP metadata differs: ' + name + ' / ' + key)
    return {'encoder': 'zopfli', 'iterations': iterations, 'entries_verified': len(expected),
            'before_bytes': source.stat().st_size, 'after_bytes': destination.stat().st_size}
