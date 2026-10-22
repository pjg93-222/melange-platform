"""Passive official FEC tables supplied by an explicit native input capability.

This module validates bytes and serves source rows. It discovers no ambient data,
fetches nothing, and does not authorize a publication or a native application.
"""
from array import array
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType

KIND = 'mentat.public-fec-tables.v1'
RECEIPT_KIND = 'mentat.fec-public-provenance.v1'
READER_IDS = ('mentat.public-data.catalogue', 'mentat.public-data.rows',
              'mentat.public-data.download', 'mentat.public-data.provenance')
CYCLES = (2022, 2024, 2026)
SCHEMAS = MappingProxyType({
    'cm': ('CMTE_ID','CMTE_NM','TRES_NM','CMTE_ST1','CMTE_ST2','CMTE_CITY','CMTE_ST','CMTE_ZIP','CMTE_DSGN','CMTE_TP','CMTE_PTY_AFFILIATION','CMTE_FILING_FREQ','ORG_TP','CONNECTED_ORG_NM','CAND_ID'),
    'cn': ('CAND_ID','CAND_NAME','CAND_PTY_AFFILIATION','CAND_ELECTION_YR','CAND_OFFICE_ST','CAND_OFFICE','CAND_OFFICE_DISTRICT','CAND_ICI','CAND_STATUS','CAND_PCC','CAND_ST1','CAND_ST2','CAND_CITY','CAND_ST','CAND_ZIP'),
    'ccl': ('CAND_ID','CAND_ELECTION_YR','FEC_ELECTION_YR','CMTE_ID','CMTE_TP','CMTE_DSGN','LINKAGE_ID'),
    'pas2': ('CMTE_ID','AMNDT_IND','RPT_TP','TRANSACTION_PGI','IMAGE_NUM','TRANSACTION_TP','ENTITY_TP','NAME','CITY','STATE','ZIP_CODE','EMPLOYER','OCCUPATION','TRANSACTION_DT','TRANSACTION_AMT','OTHER_ID','CAND_ID','TRAN_ID','FILE_NUM','MEMO_CD','MEMO_TEXT','SUB_ID'),
})
LABELS = MappingProxyType({'cm':'Committee master', 'cn':'Candidate master',
                           'ccl':'Candidate–committee linkage', 'pas2':'Committee transactions'})
MEMBERS = MappingProxyType({'cm':'cm.txt','cn':'cn.txt','ccl':'ccl.txt','pas2':'itpas2.txt'})
ROLES = tuple((f'mentat.fec.{table}.{cycle}', cycle, table, f'fec/{cycle}/{table}.psv')
              for cycle in CYCLES for table in SCHEMAS)
ROLE_MAP = MappingProxyType({role:(cycle, table, relative) for role,cycle,table,relative in ROLES})
PAGE_SIZE, STRIDE = 100, 256
MAX_LINE, MAX_FILE, MAX_TOTAL, MAX_RECORDS = 4096, 160 << 20, 512 << 20, 2_000_000
RECEIPT_PATH = 'fec/provenance.json'
HEX = re.compile(r'[a-f0-9]{64}\Z')
DOC_KEYS = frozenset(('role','cycle','table','sourceUrl','archiveBytes','archiveSha256',
    'memberName','memberBytes','memberSha256','records','maximumLineBytes','headerUrl','headerSha256'))


class PublicDataError(ValueError):
    """A fixed message, containing no local source path or row values."""


def source_url(table, cycle):
    return f'https://www.fec.gov/files/bulk-downloads/{cycle}/{table}{str(cycle)[-2:]}.zip'


def header_url(table):
    return f'https://www.fec.gov/files/bulk-downloads/data_dictionaries/{table}_header_file.csv'


def _digest(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise PublicDataError('Invalid public source digest.')
    return value


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise PublicDataError('Public source bounds are invalid.')
    return value


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _path(root, relative):
    # Every relative name here is compiled; no receipt/request value is joined.
    path = root
    parts = Path(relative).parts
    for index, part in enumerate(('', *parts)):
        if part:
            path = path / part
        info = path.lstat()
        directory = index < len(parts)
        if (path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400
                or (directory and not stat.S_ISDIR(info.st_mode))
                or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))):
            raise PublicDataError('Public inputs must be independent immutable files.')
    return path


def _hash(stream):
    stream.seek(0)
    digest = hashlib.sha256()
    while chunk := stream.read(65536):
        digest.update(chunk)
    stream.seek(0)
    return digest.hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PublicDataError('Duplicate public provenance field.')
        result[key] = value
    return result


def _receipt(raw):
    if len(raw) > 65536:
        raise PublicDataError('Public provenance is too large.')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(PublicDataError('Invalid public provenance number.')))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise PublicDataError('Invalid public provenance.') from None
    if (type(value) is not dict or set(value) != {'schemaVersion','kind','producerSha256','observedAt','documents'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or value['kind'] != RECEIPT_KIND or type(value['documents']) is not list or len(value['documents']) != 12):
        raise PublicDataError('Unsupported public provenance schema.')
    _digest(value['producerSha256'])
    if not isinstance(value['observedAt'],str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|\+00:00)',value['observedAt']):
        raise PublicDataError('Invalid public observation date.')
    by_role={}
    total=0
    for item in value['documents']:
        if type(item) is not dict or set(item) != DOC_KEYS or not isinstance(item['role'],str) or item['role'] not in ROLE_MAP:
            raise PublicDataError('Unknown public table role.')
        role=item['role']; cycle,table,_relative=ROLE_MAP[role]
        if (role in by_role or type(item['cycle']) is not int or item['cycle'] != cycle or item['table'] != table
                or item['sourceUrl'] != source_url(table,cycle) or item['memberName'] != MEMBERS[table]
                or item['headerUrl'] != header_url(table)):
            raise PublicDataError('Public table provenance differs from its fixed role.')
        for key in ('archiveSha256','memberSha256','headerSha256'):
            _digest(item[key])
        _integer(item['archiveBytes'],1,MAX_FILE)
        total += _integer(item['memberBytes'],0,MAX_FILE)
        _integer(item['records'],0,MAX_RECORDS)
        _integer(item['maximumLineBytes'],0,MAX_LINE)
        by_role[role]=item
    if set(by_role) != set(ROLE_MAP) or total > MAX_TOTAL:
        raise PublicDataError('The public input closure is incomplete or oversized.')
    return value,by_role


def _fields(raw, table):
    if len(raw) > MAX_LINE:
        raise PublicDataError('Public table line exceeds its bound.')
    if raw.endswith(b'\n'):
        raw=raw[:-1]
        if raw.endswith(b'\r'):
            raw=raw[:-1]
    try:
        text=raw.decode('utf-8')
    except UnicodeError:
        raise PublicDataError('Public table encoding is invalid.') from None
    if any(char in text for char in ('\r','\n','\x00')):
        raise PublicDataError('Public table contains unsupported controls.')
    fields=text.split('|')
    if len(fields) != len(SCHEMAS[table]):
        raise PublicDataError('Public table column count differs from its fixed schema.')
    return fields


def _closure(root, expected):
    allowed_dirs={Path(relative).parent.as_posix() for relative in expected}
    allowed_dirs.update(parent.as_posix() for relative in expected for parent in Path(relative).parents if parent.as_posix()!='.')
    found=set()
    for folder,dirs,files in os.walk(root,followlinks=False):
        for name in dirs:
            path=Path(folder)/name
            relative=path.relative_to(root).as_posix()
            info=path.lstat()
            if (relative not in allowed_dirs or path.is_symlink()
                    or getattr(info,'st_file_attributes',0)&0x400):
                raise PublicDataError('Unexpected public input directory or alias.')
        for name in files:
            relative=(Path(folder)/name).relative_to(root).as_posix()
            if relative not in expected:
                raise PublicDataError('Unexpected public input member.')
            found.add(relative)
    if found != expected:
        raise PublicDataError('The public input closure is incomplete.')


@dataclass(frozen=True)
class _Table:
    role: str
    cycle: int
    table: str
    relative: str
    identity: tuple
    sha256: str
    bytes: int
    records: int
    offsets: tuple


@dataclass(frozen=True)
class Catalogue:
    _root: Path
    _tables: object
    _seal: tuple
    _provenance_json: bytes
    source_digest: str
    source_fingerprint: str

    def _unchanged(self):
        try:
            _closure(self._root,{relative for relative,_identity in self._seal})
            for relative,identity in self._seal:
                if _stamp(_path(self._root,relative).lstat()) != identity:
                    raise PublicDataError('The public source changed. Reload the source.')
        except OSError:
            raise PublicDataError('The public source is unavailable.') from None

    @contextmanager
    def _open(self, table, *, preverify=True):
        if type(preverify) is not bool:
            raise PublicDataError('The public reader verification mode is invalid.')
        self._unchanged()
        try:
            path=_path(self._root,table.relative)
            flags=os.O_RDONLY | getattr(os,'O_BINARY',0) | getattr(os,'O_NOFOLLOW',0)
            with os.fdopen(os.open(path,flags),'rb') as stream:
                opened=_stamp(os.fstat(stream.fileno()))
                # Windows 3.12 path and handle ctime have different semantics;
                # compare shared identity fields, then retain both full stamps.
                if opened[:-1] != table.identity[:-1] or (preverify and _hash(stream) != table.sha256):
                    raise PublicDataError('The public source checksum or identity changed.')
                yield stream
                if _stamp(os.fstat(stream.fileno())) != opened or _hash(stream) != table.sha256:
                    raise PublicDataError('The public source checksum or identity changed.')
            self._unchanged()
        except OSError:
            raise PublicDataError('The public source is unavailable.') from None

    def _select(self, role):
        if not isinstance(role,str) or role not in self._tables:
            raise PublicDataError('Choose one of the fixed public tables.')
        return self._tables[role]

    def _identity(self):
        return {'sourceDigest':self.source_digest,'sourceFingerprint':self.source_fingerprint}

    def catalogue(self):
        self._unchanged()
        result={'available':True,'kind':KIND,**self._identity(),'pageSize':PAGE_SIZE,'tables':[
            {'id':t.role,'table':t.table,'cycle':t.cycle,'label':LABELS[t.table],
             'columns':list(SCHEMAS[t.table]),'records':t.records,'bytes':t.bytes,'sha256':t.sha256}
            for t in self._tables.values()]}
        self._unchanged()
        return result

    def rows(self, role, page=0):
        table=self._select(role)
        if type(page) is not int or page < 0 or page > max(0,(table.records-1)//PAGE_SIZE):
            raise PublicDataError('Public table page is out of range.')
        start=page*PAGE_SIZE
        rows=[]
        # A page is fully buffered until this context exits. The final complete
        # hash verifies it before rows can be returned to the HTTP handler.
        # Streaming downloads keep the default full hash before their yield.
        with self._open(table, preverify=False) as stream:
            block=start//STRIDE
            stream.seek(table.offsets[block] if table.offsets else 0)
            for _ in range(start-block*STRIDE):
                stream.readline(MAX_LINE+1)
            for _ in range(min(PAGE_SIZE,table.records-start)):
                rows.append(_fields(stream.readline(MAX_LINE+1),table.table))
        return {**self._identity(),'table':role,'columns':list(SCHEMAS[table.table]),'page':page,
                'pageSize':PAGE_SIZE,'totalRows':table.records,'firstRow':start+1 if rows else None,
                'previousPage':page-1 if page else None,'nextPage':page+1 if start+PAGE_SIZE < table.records else None,'rows':rows}

    def provenance(self, role):
        self._select(role)
        self._unchanged()
        value=json.loads(self._provenance_json)
        result={**self._identity(),'observedAt':value['observedAt'],'producerSha256':value['producerSha256'],
                'document':next(v for v in value['documents'] if v['role']==role)}
        self._unchanged()
        return result

    @contextmanager
    def download(self, role):
        table=self._select(role)
        with self._open(table) as stream:
            yield {'filename':f'{table.table}-{table.cycle}.txt','bytes':table.bytes,'sha256':table.sha256}, iter(lambda:stream.read(65536),b'')


def prepare(root, *, source_digest, source_fingerprint):
    """Construct a component from a native-selected immutable scoped view.

    Digests identify the already verified source; they are not authorization.
    The native compiled installer alone decides whether to inject this object.
    """
    _digest(source_digest); _digest(source_fingerprint)
    root=Path(root).absolute()
    expected={RECEIPT_PATH,*(relative for _role,_cycle,_table,relative in ROLES)}
    try:
        receipt_path=_path(root,RECEIPT_PATH)
        receipt_before=_stamp(receipt_path.lstat())
        if receipt_before[4] > 65536:
            raise PublicDataError('Public provenance is too large.')
        with receipt_path.open('rb') as stream:
            opened=_stamp(os.fstat(stream.fileno()))
            if opened[:-1] != receipt_before[:-1]:
                raise PublicDataError('Public provenance identity changed.')
            raw=stream.read(65537)
            if _stamp(os.fstat(stream.fileno())) != opened or _stamp(receipt_path.lstat()) != receipt_before:
                raise PublicDataError('Public provenance identity changed.')
        provenance,by_role=_receipt(raw)
        _closure(root,expected)
        tables={}; seal=[(RECEIPT_PATH,receipt_before)]
        for role,cycle,table,relative in ROLES:
            entry=by_role[role]; path=_path(root,relative); before=_stamp(path.lstat())
            if before[4] != entry['memberBytes']:
                raise PublicDataError('Public table size differs from its receipt.')
            offsets=array('Q'); digest=hashlib.sha256(); records=maximum=offset=0
            with path.open('rb') as stream:
                opened=_stamp(os.fstat(stream.fileno()))
                if opened[:-1] != before[:-1]:
                    raise PublicDataError('Public table identity changed.')
                while True:
                    line=stream.readline(MAX_LINE+1)
                    if not line:break
                    if records >= MAX_RECORDS:
                        raise PublicDataError('Public table record count exceeds its bound.')
                    _fields(line,table)
                    if records%STRIDE==0:offsets.append(offset)
                    digest.update(line);maximum=max(maximum,len(line));records+=1
                    offset+=len(line)
                if (_stamp(os.fstat(stream.fileno())) != opened or _stamp(_path(root,relative).lstat()) != before
                        or digest.hexdigest() != entry['memberSha256'] or records != entry['records']
                        or maximum != entry['maximumLineBytes']):
                    raise PublicDataError('Public table content differs from its receipt.')
            tables[role]=_Table(role,cycle,table,relative,before,entry['memberSha256'],before[4],records,tuple(offsets))
            seal.append((relative,before))
        catalogue=Catalogue(root,MappingProxyType(tables),tuple(seal),json.dumps(provenance,separators=(',',':')).encode(),source_digest,source_fingerprint)
        catalogue._unchanged()
        return catalogue
    except OSError:
        raise PublicDataError('The public source is unavailable.') from None
