"""User-scoped Windows DPAPI storage. Never emit or log key values."""
import argparse
import ctypes
import functools
import json
import hashlib
import os
from pathlib import Path
import re
import tempfile
from contextlib import contextmanager
import time


def key_path():
    return Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Mentat' / 'credentials' / 'fec-keys.dpapi'


def normalize(values):
    result=[]
    for value in values:
        for line in str(value).splitlines():
            if line.strip().startswith('#'):continue
            for key in re.split(r'[,;|\s]+',line.strip()):
                if key and key not in result:result.append(key)
    return result


def environment_keys(environment=None):
    env=os.environ if environment is None else environment
    names=['FEC_API_KEY','FEC_API_KEYS']+sorted(k for k in env if re.fullmatch(r'FEC_API_KEY_\d+',k))
    return normalize([env.get(k,'') for k in names])


@functools.lru_cache(maxsize=None)
def _windows():
    """(wintypes, Blob), built on first use so this module also imports where there is no Windows."""
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    return wintypes,Blob


def crypt(value, decrypt=False):
    if os.name!='nt':raise RuntimeError('The encrypted key store requires Windows.')
    wintypes,Blob=_windows()
    buffer=ctypes.create_string_buffer(value)
    source=Blob(len(value),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    output=Blob()
    library=ctypes.WinDLL('crypt32',use_last_error=True)
    function=library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    function.restype=wintypes.BOOL
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output)):
        raise RuntimeError('Windows could not access the encrypted FEC key store for this user.')
    try:return ctypes.string_at(output.data,output.size)
    finally:
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.LocalFree.argtypes=[ctypes.c_void_p]
        kernel.LocalFree.restype=ctypes.c_void_p
        kernel.LocalFree(output.data)


def key_id(value):
    """Non-secret fingerprint. The only key identifier safe to print or log."""
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def load_keys(path=None, include_disabled=False):
    path=Path(path) if path else key_path()
    if not path.exists():return []
    data=json.loads(crypt(path.read_bytes(),decrypt=True))
    disabled=data.get('disabled',{})
    return [key for key in normalize(data['keys']) if include_disabled or
            key_id(key) not in disabled]


def inventory(path=None):
    """Every key on file with its fingerprint and status. Never returns key values."""
    path=Path(path) if path else key_path()
    if not path.exists():return []
    data=json.loads(crypt(path.read_bytes(),decrypt=True))
    disabled=data.get('disabled',{})
    return [{'key_id':key_id(key),'active':key_id(key) not in disabled,
             'status':disabled.get(key_id(key),'active')}
            for key in normalize(data['keys'])]


def set_disabled(identifiers,reason=None,path=None):
    """Recycle (reason=None) or quarantine keys already on file, by fingerprint."""
    path=Path(path) if path else key_path()
    with store_lock(path):
        data=json.loads(crypt(path.read_bytes(),decrypt=True))
        disabled=data.setdefault('disabled',{})
        known={entry['key_id'] for entry in inventory(path)}
        for value in identifiers:
            if value not in known:continue
            if reason is None:disabled.pop(value,None)
            else:disabled[value]=reason
        write_store(path,data)
    return [value for value in identifiers if value not in known]


@contextmanager
def store_lock(path):
    import msvcrt
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        deadline = time.monotonic() + 15
        while True:
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('The encrypted key store is busy; retry shortly.')
                time.sleep(.05)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def save_keys(values,path=None):
    path=Path(path) if path else key_path()
    with store_lock(path):
        return save_keys_locked(values, path)


def save_keys_locked(values, path):
    keys=normalize([*load_keys(path,include_disabled=True),*values])
    if not keys:raise ValueError('Provide at least one FEC API key.')
    path.parent.mkdir(parents=True,exist_ok=True)
    old=json.loads(crypt(path.read_bytes(),decrypt=True)) if path.exists() else {}
    write_store(path, {'version':1,'keys':keys,'disabled':old.get('disabled',{})})
    if load_keys(path,include_disabled=True)!=keys:raise RuntimeError('Encrypted key-store verification failed.')
    return len(keys)


def write_store(path,data):
    sealed=crypt(json.dumps(data).encode())
    descriptor,name=tempfile.mkstemp(prefix='fec-',suffix='.pending',dir=path.parent)
    try:
        with os.fdopen(descriptor,'wb') as stream:stream.write(sealed)
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def record_validation(results,path=None):
    path=Path(path) if path else key_path()
    with store_lock(path):
        record_validation_locked(results, path)


def record_validation_locked(results,path):
    data=json.loads(crypt(path.read_bytes(),decrypt=True))
    disabled=data.setdefault('disabled',{})
    for result in results:
        if result['status'] in ('http_401','http_403'):disabled[result['key_id']]=result['status']
        elif result['status']=='valid':disabled.pop(result['key_id'],None)
    write_store(path,data)


def status():
    keys=inventory()
    return {'stored_key_count':len(keys),
            'active_key_count':sum(entry['active'] for entry in keys),'encrypted':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--import-file',type=Path)
    parser.add_argument('--import-environment',action='store_true')
    parser.add_argument('--count',action='store_true')
    parser.add_argument('--list',action='store_true')
    parser.add_argument('--enable',nargs='+',metavar='ID',default=[])
    parser.add_argument('--enable-all',action='store_true')
    parser.add_argument('--disable',nargs='+',metavar='ID',default=[])
    args=parser.parse_args()
    values=[]
    if args.import_file:values.append(args.import_file.read_text(encoding='utf-8-sig'))
    if args.import_environment:values.extend(environment_keys())
    if values:save_keys(values)
    unknown=[]
    if args.enable_all:
        unknown+=set_disabled([entry['key_id'] for entry in inventory() if not entry['active']])
    if args.enable:unknown+=set_disabled(args.enable)
    if args.disable:unknown+=set_disabled(args.disable,reason='manual')
    count=len(load_keys())
    if args.count:print(count)
    elif args.list or args.enable_all or args.enable or args.disable:
        print(json.dumps({**status(),'path':str(key_path()),
                          'keys':inventory(),'unknown':unknown},indent=2))
    else:print(json.dumps({**status(),'path':str(key_path())}))


if __name__=='__main__':main()
