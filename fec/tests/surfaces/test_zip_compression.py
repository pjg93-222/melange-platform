import hashlib
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from surfaces import make_email_package as package
from surfaces.zip_compression import recompress_zip, raw_deflate, _classic_entry, UINT32_LIMIT

requires_zopfli = unittest.skipUnless(importlib.util.find_spec('zopfli'), 'optional zopfli dependency unavailable')


class ZipCompressionTests(unittest.TestCase):
    @requires_zopfli
    def test_roundtrip_preserves_names_crc_bytes_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp)/'source.zip', Path(tmp)/'strong.zip'
            contents = {'Moneyball.html': b'<html>record</html>' * 200,
                        'nested/café.html': 'Unicode content — ★'.encode()*70,
                        'empty.txt': b'', 'binary.dat': bytes(range(256))*10}
            with zipfile.ZipFile(source, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                archive.comment = b'archive comment'
                for name, data in contents.items():
                    info = zipfile.ZipInfo(name, (2026, 9, 13, 10, 20, 30))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.comment = b'entry comment'
                    info.extra = struct.pack('<HH4s', 0xCAFE, 4, b'test')
                    info.external_attr = 0o100640 << 16
                    archive.writestr(info, data)
            source_hash = hashlib.sha256(source.read_bytes()).digest()
            proof = recompress_zip(source, target)
            self.assertEqual(proof['entries_verified'], 4)
            self.assertEqual(source_hash, hashlib.sha256(source.read_bytes()).digest())
            with zipfile.ZipFile(source) as before, zipfile.ZipFile(target) as after:
                self.assertIsNone(after.testzip())
                self.assertEqual(after.namelist(), list(contents))
                self.assertEqual(after.comment, before.comment)
                for name, data in contents.items():
                    self.assertEqual(after.read(name), data)
                    self.assertEqual(after.getinfo(name).CRC, zlib.crc32(data) & UINT32_LIMIT)
                    for key in ('date_time', 'external_attr', 'extra', 'comment', 'flag_bits'):
                        self.assertEqual(getattr(before.getinfo(name), key), getattr(after.getinfo(name), key))

    def test_normal_package_below_cap_remains_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'page.html').write_bytes(b'ordinary content'*50)
            expected, actual = root/'expected.zip', root/'actual.zip'
            with zipfile.ZipFile(expected, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                archive.write(root/'page.html', arcname='Moneyball.html')
            with patch('surfaces.zip_compression.recompress_zip') as fallback:
                package.build_zip(actual, root, (('page.html', 'Moneyball.html'),))
            fallback.assert_not_called()
            self.assertEqual(actual.read_bytes(), expected.read_bytes())

    @requires_zopfli
    def test_fallback_activates_only_after_normal_limit_and_preserves_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = b''.join((f'<div id="record-{i%137}">Member {i%19}: supportive actions</div>\n'.encode()) for i in range(2500))
            (root/'page.html').write_bytes(data)
            normal, stronger, final = root/'normal.zip', root/'stronger.zip', root/'final.zip'
            with zipfile.ZipFile(normal, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                archive.write(root/'page.html', arcname='Moneyball.html')
            recompress_zip(normal, stronger)
            self.assertLess(stronger.stat().st_size, normal.stat().st_size)
            budget = (stronger.stat().st_size + normal.stat().st_size)//2
            with patch.object(package, 'BUDGET_BYTES', budget):
                package.build_zip(final, root, (('page.html', 'Moneyball.html'),))
            self.assertLessEqual(final.stat().st_size, budget)
            with zipfile.ZipFile(final) as archive:
                self.assertEqual(archive.read('Moneyball.html'), data)
            self.assertFalse(list(root.glob('*.tmp')))

    @requires_zopfli
    def test_fallback_still_over_limit_keeps_previous_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'page.html').write_bytes(b'content'*100)
            target = root/'result.zip'
            target.write_bytes(b'previous verified package')
            with patch.object(package, 'BUDGET_BYTES', 1):
                with self.assertRaisesRegex(RuntimeError, 'exceeds 14 MiB'):
                    package.build_zip(target, root, ('page.html',))
            self.assertEqual(target.read_bytes(), b'previous verified package')
            self.assertFalse(list(root.glob('*.tmp')))

    @requires_zopfli
    def test_invalid_zlib_envelope_is_rejected(self):
        with patch('zopfli.zlib.compress', return_value=b'invalid compressed output'):
            with self.assertRaisesRegex(ValueError, 'envelope'):
                raw_deflate(b'input')

    def test_source_crc_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp)/'source.zip', Path(tmp)/'out.zip'
            with zipfile.ZipFile(source, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('page.html', b'original content')
            damaged = bytearray(source.read_bytes())
            struct.pack_into('<I', damaged, damaged.index(b'PK\x01\x02')+16, 0)
            source.write_bytes(damaged)
            with self.assertRaises(zipfile.BadZipFile):
                recompress_zip(source, target)

    def test_zip64_and_unsupported_flags_are_rejected_without_large_allocation(self):
        info = zipfile.ZipInfo('large.html')
        info.header_offset = 0
        info.file_size = UINT32_LIMIT
        with self.assertRaisesRegex(ValueError, 'ZIP64'):
            _classic_entry(info)
        info.file_size = 0
        info.extra = struct.pack('<HH', 1, 0)
        with self.assertRaisesRegex(ValueError, 'ZIP64'):
            _classic_entry(info)
        info.extra = b''
        info.header_offset = 0
        info.flag_bits = 1
        with self.assertRaisesRegex(ValueError, 'Encrypted'):
            _classic_entry(info)


if __name__ == '__main__':
    unittest.main()
