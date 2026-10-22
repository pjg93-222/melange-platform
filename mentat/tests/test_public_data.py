"""Generated passive source fixtures. No real reports, donors or models."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

APP=Path(__file__).resolve().parents[1]/'app'
sys.path.insert(0,str(APP))
import public_data as data


def fixture(root, records=301):
    docs=[]
    for role,cycle,table,relative in data.ROLES:
        path=root/relative;path.parent.mkdir(parents=True,exist_ok=True)
        count=records if role=='mentat.fec.cm.2022' else 1
        lines=[]
        for index in range(count):
            fields=[f'fixture-{index}-{col}' for col in range(len(data.SCHEMAS[table]))]
            fields[1]='<script>fixture & café</script>'
            lines.append(('|'.join(fields)+'\r\n').encode('utf-8'))
        raw=b''.join(lines);path.write_bytes(raw)
        docs.append({'role':role,'cycle':cycle,'table':table,'sourceUrl':data.source_url(table,cycle),
            'archiveBytes':100,'archiveSha256':'a'*64,'memberName':data.MEMBERS[table],
            'memberBytes':len(raw),'memberSha256':hashlib.sha256(raw).hexdigest(),'records':count,
            'maximumLineBytes':max(map(len,lines),default=0),'headerUrl':data.header_url(table),'headerSha256':'b'*64})
    receipt={'schemaVersion':1,'kind':data.RECEIPT_KIND,'producerSha256':'c'*64,
             'observedAt':'2026-10-22T05:23:07+00:00','documents':docs}
    (root/data.RECEIPT_PATH).write_text(json.dumps(receipt),encoding='utf-8')
    return receipt


def prepare(root):
    return data.prepare(root,source_digest='d'*64,source_fingerprint='e'*64)


class PublicDataTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.receipt=fixture(self.root)

    def change(self, edit):
        edit(self.receipt)
        (self.root/data.RECEIPT_PATH).write_text(json.dumps(self.receipt),encoding='utf-8')

    def test_fixed_complete_catalogue_and_exact_source_identity(self):
        cat=prepare(self.root);value=cat.catalogue()
        self.assertEqual(len(value['tables']),12)
        self.assertEqual(value['sourceDigest'],'d'*64)
        self.assertEqual(value['sourceFingerprint'],'e'*64)
        self.assertNotIn(str(self.root),json.dumps(value))
        with self.assertRaises(TypeError):cat._tables['new']=None

    def test_pages_keep_source_native_fields_and_boundaries(self):
        cat=prepare(self.root)
        first=cat.rows('mentat.fec.cm.2022');last=cat.rows('mentat.fec.cm.2022',3)
        self.assertEqual((len(first['rows']),first['firstRow'],first['nextPage']), (100,1,1))
        self.assertEqual(first['rows'][0][1],'<script>fixture & café</script>')
        self.assertEqual((len(last['rows']),last['firstRow'],last['previousPage'],last['nextPage']),(1,301,2,None))
        self.assertEqual(last['rows'][0][0],'fixture-300-0')
        for bad in (-1,4,True,'0',10**100):
            with self.subTest(page=bad),self.assertRaises(data.PublicDataError):cat.rows('mentat.fec.cm.2022',bad)

    def test_empty_table_has_one_empty_page(self):
        fixture(self.root,0);cat=prepare(self.root)
        page=cat.rows('mentat.fec.cm.2022')
        self.assertEqual((page['rows'],page['firstRow'],page['totalRows'],page['nextPage']),([],None,0,None))

    def test_variable_utf8_line_lengths_and_crlf_keep_exact_sparse_page_offsets(self):
        value=fixture(self.root,701);lines=[]
        for index in range(701):
            fields=[f'fixture-{index}-{column}' for column in range(15)]
            fields[1]='café-'+('variable-'*(index%9))
            lines.append(('|'.join(fields)+('\r\n' if index%2 else '\n')).encode('utf-8'))
        raw=b''.join(lines);(self.root/'fec/2022/cm.psv').write_bytes(raw)
        value['documents'][0].update(memberBytes=len(raw),memberSha256=hashlib.sha256(raw).hexdigest(),maximumLineBytes=max(map(len,lines)))
        (self.root/data.RECEIPT_PATH).write_text(json.dumps(value),encoding='utf-8')
        cat=prepare(self.root)
        for page in (0,2,3,5,6,7):
            result=cat.rows('mentat.fec.cm.2022',page)
            for index,row in enumerate(result['rows'],page*100):
                self.assertEqual(row[0],f'fixture-{index}-0')
                self.assertEqual(row[1],'café-'+('variable-'*(index%9)))

    def test_download_is_exact_and_provenance_has_only_sanitized_fields(self):
        cat=prepare(self.root);role='mentat.fec.cm.2022'
        with cat.download(role) as (meta,chunks):
            raw=b''.join(chunks)
        self.assertEqual(meta['filename'],'cm-2022.txt')
        self.assertEqual(raw,(self.root/'fec/2022/cm.psv').read_bytes())
        self.assertEqual(hashlib.sha256(raw).hexdigest(),meta['sha256'])
        proof=cat.provenance(role)
        self.assertEqual(proof['document']['sourceUrl'],data.source_url('cm',2022))
        self.assertNotIn(str(self.root),json.dumps(proof))

    def test_buffered_page_keeps_one_full_hash_after_parsing_before_return(self):
        cat=prepare(self.root);events=[];original_hash=data._hash;original_fields=data._fields
        def hashed(stream):
            events.append('full-hash');return original_hash(stream)
        def fields(raw,table):
            events.append('buffer-row');return original_fields(raw,table)
        with patch.object(data,'_hash',hashed),patch.object(data,'_fields',fields):
            result=cat.rows('mentat.fec.cm.2022')
        self.assertEqual(len(result['rows']),100)
        self.assertEqual(events,['buffer-row']*100+['full-hash'])

    def test_failed_buffered_page_posthash_never_returns_and_download_still_preverifies(self):
        cat=prepare(self.root);entered=False
        with patch.object(data,'_fields',wraps=data._fields) as fields,patch.object(data,'_hash',return_value='0'*64) as hashed:
            with self.assertRaises(data.PublicDataError):cat.rows('mentat.fec.cm.2022')
            self.assertEqual(fields.call_count,100);self.assertEqual(hashed.call_count,1)
            with self.assertRaises(data.PublicDataError):
                with cat.download('mentat.fec.cm.2022'):
                    entered=True
            self.assertFalse(entered);self.assertEqual(hashed.call_count,2)

    def test_download_retains_full_pre_and_posthash_and_midstream_mutation_refuses(self):
        cat=prepare(self.root)
        with patch.object(data,'_hash',wraps=data._hash) as hashed:
            with cat.download('mentat.fec.cm.2022') as (_meta,chunks):raw=b''.join(chunks)
            self.assertEqual(hashed.call_count,2)
        self.assertEqual(raw,(self.root/'fec/2022/cm.psv').read_bytes())
        path=self.root/'fec/2022/cm.psv';before=path.stat()
        with self.assertRaises(data.PublicDataError):
            with cat.download('mentat.fec.cm.2022') as (_meta,chunks):
                next(chunks);path.write_bytes(b'X'+raw[1:])
                os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))

    def test_buffered_page_selected_file_mutation_is_detected_before_return(self):
        cat=prepare(self.root);path=self.root/'fec/2022/cm.psv';raw=path.read_bytes();before=path.stat()
        original=data._fields;changed=False
        def fields(line,table):
            nonlocal changed
            if not changed:
                changed=True;path.write_bytes(b'X'+raw[1:]);os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
            return original(line,table)
        with patch.object(data,'_fields',fields),self.assertRaises(data.PublicDataError):cat.rows('mentat.fec.cm.2022')

    def test_unknown_roles_are_lexically_refused_before_any_path_probe(self):
        cat=prepare(self.root)
        with patch.object(Path,'lstat',side_effect=AssertionError('no filesystem probe')):
            for role in ('../private','C:/private','\\\\host\\private','https://invalid','mentat.fec.cm.2028'):
                for reader in (cat.rows,cat.provenance,lambda role:cat.download(role).__enter__()):
                    with self.subTest(role=role),self.assertRaises(data.PublicDataError):reader(role)

    def test_missing_extra_and_empty_extra_directory_refuse(self):
        for change in ('missing','extra','directory'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);fixture(root,1)
                if change=='missing':(root/'fec/2026/cn.psv').unlink()
                elif change=='extra':(root/'unknown').write_bytes(b'fixture')
                else:(root/'unrelated').mkdir()
                with self.assertRaises(data.PublicDataError):prepare(root)

    def test_receipt_fixed_role_schema_urls_and_resource_bounds(self):
        changes=[lambda r:r['documents'].pop(),lambda r:r['documents'].append(r['documents'][0]),
            lambda r:r['documents'][0].update(role='../private'),lambda r:r['documents'][0].update(sourceUrl='https://invalid/'),
            lambda r:r['documents'][0].update(memberName='../../private'),lambda r:r.update(localPath='private'),
            lambda r:r['documents'][0].update(memberBytes=True),lambda r:r['documents'][0].update(records=data.MAX_RECORDS+1),
            lambda r:r['documents'][0].update(maximumLineBytes=data.MAX_LINE+1)]
        for edit in changes:
            with self.subTest(edit=edit),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);value=fixture(root,1);edit(value)
                (root/data.RECEIPT_PATH).write_text(json.dumps(value),encoding='utf-8')
                with self.assertRaises(data.PublicDataError):prepare(root)

    def test_invalid_receipt_json_and_oversize_refuse(self):
        path=self.root/data.RECEIPT_PATH
        for raw in (b'\xff',b'{"a":1,"a":2}',b'{"x":NaN}',b'['*4000,b' '*65537):
            path.write_bytes(raw)
            with self.subTest(raw_length=len(raw)),self.assertRaises(data.PublicDataError):prepare(self.root)

    def test_invalid_table_encoding_width_hash_size_and_long_lines_refuse(self):
        for raw in (b'\xff\n',b'a|b\n',b'x'*4097+b'\n',b'\x00|'*14+b'\x00\n'):
            with self.subTest(length=len(raw)),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);value=fixture(root,1);path=root/'fec/2022/cm.psv';path.write_bytes(raw)
                item=value['documents'][0]
                item.update(memberBytes=len(raw),memberSha256=hashlib.sha256(raw).hexdigest(),records=1,maximumLineBytes=len(raw))
                (root/data.RECEIPT_PATH).write_text(json.dumps(value),encoding='utf-8')
                with self.assertRaises(data.PublicDataError):prepare(root)
        path=self.root/'fec/2022/cm.psv';raw=path.read_bytes();path.write_bytes(b'X'+raw[1:])
        with self.assertRaises(data.PublicDataError):prepare(self.root)

    def test_whole_closure_checked_for_each_reader_and_source_switch_separates_identity(self):
        cat=prepare(self.root)
        (self.root/'fec/2026/ccl.psv').write_bytes(b'changed')
        for reader in (cat.catalogue,lambda:cat.rows('mentat.fec.cm.2022'),lambda:cat.provenance('mentat.fec.cm.2022')):
            with self.assertRaises(data.PublicDataError):reader()
        fixture(self.root,1)
        one=prepare(self.root);two=data.prepare(self.root,source_digest='1'*64,source_fingerprint='2'*64)
        self.assertNotEqual(one.rows('mentat.fec.cm.2022')['sourceDigest'],two.rows('mentat.fec.cm.2022')['sourceDigest'])

    def test_hardlink_and_foreign_replacement_are_refused_without_writes(self):
        path=self.root/'fec/2022/cm.psv';sentinel=self.root.parent/(self.root.name+'-link')
        os.link(path,sentinel);self.addCleanup(lambda:sentinel.unlink(missing_ok=True))
        before=sentinel.read_bytes()
        with self.assertRaises(data.PublicDataError):prepare(self.root)
        self.assertEqual(sentinel.read_bytes(),before)
        sentinel.unlink();cat=prepare(self.root);raw=path.read_bytes();path.unlink();path.write_bytes(raw)
        with self.assertRaises(data.PublicDataError):cat.rows('mentat.fec.cm.2022')
        self.assertEqual(path.read_bytes(),raw)

    def test_mid_read_sibling_mutation_is_detected(self):
        cat=prepare(self.root);original=data._fields;changed=False
        def fields(raw,table):
            nonlocal changed
            if not changed:
                changed=True;(self.root/'fec/2026/cn.psv').write_bytes(b'changed')
            return original(raw,table)
        with patch.object(data,'_fields',fields),self.assertRaises(data.PublicDataError):cat.rows('mentat.fec.cm.2022')

    def test_actual_directory_junction_and_late_hardlink_are_refused(self):
        import subprocess
        import shutil
        folder=self.root/'fec/2026'
        with tempfile.TemporaryDirectory() as outside:
            sentinel=Path(outside)/'original.fixture';sentinel.write_bytes(b'preserve')
            shutil.rmtree(folder)
            if os.name=='nt':
                made=subprocess.run(['cmd.exe','/c','mklink','/J',str(folder),outside],
                    capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(made.returncode,0)
            else:folder.symlink_to(outside,target_is_directory=True)
            try:
                with self.assertRaises(data.PublicDataError):prepare(self.root)
                self.assertEqual(sentinel.read_bytes(),b'preserve')
                self.assertEqual([p.name for p in Path(outside).iterdir()],['original.fixture'])
            finally:
                if os.name=='nt':folder.rmdir()
                else:folder.unlink()
        fixture(self.root,1);cat=prepare(self.root)
        linked=self.root.parent/(self.root.name+'-late-link')
        os.link(self.root/'fec/2022/cm.psv',linked)
        try:
            with self.assertRaises(data.PublicDataError):cat.rows('mentat.fec.cm.2022')
            self.assertEqual(linked.stat().st_nlink,2)
        finally:linked.unlink()


if __name__=='__main__':unittest.main()
