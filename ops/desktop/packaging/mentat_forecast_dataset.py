"""Typed forecast data adapter; no source code, waivers or account state export.

Activation requires the Electron coordinator's backend-closed lease. A durable
marker blocks normal startup until both existing model pointers are coherent.
"""
import base64
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import time
import uuid

import native_access_state

KIND='mentat.forecast.v1'
ROLES={'selection':4096,'giving-model':16*1024*1024,'pac-model':16*1024*1024}
POINTERS={'giving':'giving-models-current.json','pac':'pac-giving-current.json'}
MARKER='.desktop-forecast-transaction.json'


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def identifier(value):
    if not isinstance(value,str) or str(uuid.UUID(value))!=value or uuid.UUID(value).int==0:
        raise ValueError('Invalid forecast transaction identifier')
    return value


def exact(value,keys):
    if not isinstance(value,dict) or set(value)!=set(keys):raise ValueError('Invalid forecast dataset schema')


def _pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate forecast data field')
        result[key]=value
    return result


def parse(raw):
    return json.loads(raw,object_pairs_hook=_pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite forecast data')))


def read_bounded(path,limit):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>limit:raise ValueError('Invalid forecast data object')
    before=path.stat();raw=path.read_bytes();after=path.stat()
    if len(raw)>limit or (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):raise ValueError('Forecast data changed during read')
    parse(raw)
    return raw


def atomic(path,raw):
    path=Path(path);temporary=path.with_name(path.name+'.'+secrets.token_hex(12)+'.tmp')
    try:
        with temporary.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def contained(root,relative,create=False):
    root=Path(root).resolve(strict=True);parts=Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(part in {'','.','..'} or ':' in part for part in parts):raise ValueError('Unsafe forecast path')
    current=root
    for part in parts[:-1]:
        current=current/part
        if create:current.mkdir(exist_ok=True)
        if current.is_symlink() or not current.is_dir() or not current.resolve().is_relative_to(root):raise ValueError('Forecast path redirects outside model root')
    target=current/parts[-1]
    if target.is_symlink() or target.exists() and (not target.is_file() or not target.resolve().is_relative_to(root)):raise ValueError('Unsafe forecast target')
    return target


def selection(raw):
    value=parse(raw);exact(value,{'schemaVersion','purpose','giving','pac'})
    exact(value['giving'],{'sha256','modelVersion','modelSchemaVersion'});exact(value['pac'],{'sha256','modelId'})
    if value['schemaVersion']!=1 or value['purpose']!='mentat-forecast-selection' or type(value['giving']['modelSchemaVersion']) is not int or value['giving']['modelSchemaVersion'] not in {1,2} or value['pac']['modelId']!='pac-r12-origin-half-r8-nowcast-v1':raise ValueError('Unsupported forecast selection')
    for item in (value['giving'],value['pac']):
        if not isinstance(item['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',item['sha256']):raise ValueError('Invalid forecast digest')
    version=value['giving']['modelVersion']
    if not isinstance(version,str) or not 1<=len(version)<=160 or any(ord(c)<32 or ord(c)==127 for c in version):raise ValueError('Invalid forecast model version')
    return value


def pointer_bytes(value):
    giving=value['giving'];pac=value['pac']
    return {'giving':canonical({'schema_version':1,'active':f"versions/{giving['sha256']}/giving-models-v{giving['modelSchemaVersion']}.json",'sha256':giving['sha256'],'model_version':giving['modelVersion'],'model_schema_version':giving['modelSchemaVersion']}),
            'pac':canonical({'schema_version':1,'active':f"pac-versions/{pac['sha256']}/pac-giving-v1.json",'sha256':pac['sha256'],'model_id':pac['modelId']})}


@contextmanager
def readers(code_root,model_root):
    code_root=Path(code_root).resolve(strict=True);app=code_root/'app'
    if not (app/'giving_forecast.py').is_file() or not (app/'pac_giving_forecast.py').is_file():raise ValueError('Installed Mentat reader code is required')
    names=('MENTAT_APP_DATA_ROOT','MENTAT_GIVING_MODELS','MENTAT_PAC_GIVING_POINTER');old={name:os.environ.get(name) for name in names}
    old_path=list(sys.path);sys.path.insert(0,str(app));os.environ['MENTAT_APP_DATA_ROOT']=str(Path(model_root).resolve(strict=True))
    os.environ.pop('MENTAT_GIVING_MODELS',None);os.environ.pop('MENTAT_PAC_GIVING_POINTER',None)
    try:
        def load(name):
            spec=importlib.util.spec_from_file_location('desktop_dataset_'+name+'_'+uuid.uuid4().hex,app/(name+'.py'))
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
        yield load('giving_forecast'),load('pac_giving_forecast')
    finally:
        sys.path[:]=old_path
        for name,value in old.items():
            if value is None:os.environ.pop(name,None)
            else:os.environ[name]=value


def read_selection(code_root,model_root):
    with readers(code_root,model_root) as (giving,pac):
        model=giving.models();pac_catalog=pac.catalog();giving_path=giving.model_path()
        pac_pointer=parse(read_bounded(Path(model_root)/POINTERS['pac'],4096));pac_path=contained(model_root,pac_pointer['active'])
        giving_raw=read_bounded(giving_path,ROLES['giving-model']);pac_raw=read_bounded(pac_path,ROLES['pac-model'])
        chosen={'schemaVersion':1,'purpose':'mentat-forecast-selection','giving':{'sha256':digest(giving_raw),'modelVersion':model['model_version'],'modelSchemaVersion':model['schema_version']},'pac':{'sha256':digest(pac_raw),'modelId':pac.MODEL_ID}}
        if chosen['giving']['sha256']!=model['sha256'] or chosen['pac']['sha256']!=pac_catalog['bundle_sha256']:raise ValueError('Forecast model changed during validation')
        selection(canonical(chosen))
        offered=giving._v2_offered(model)[0] if model['schema_version']==2 else list(model['industries'].values())
        return chosen,{'selection':canonical(chosen),'giving-model':giving_raw,'pac-model':pac_raw},{'offeredIndustries':len(offered),'pacSectors':len(pac_catalog['sectors'])}


def export_selection(code_root,model_root,exports_root,dataset_id,revision_id,parents):
    identifier(dataset_id);identifier(revision_id)
    if not isinstance(parents,list) or len(parents)>1:raise ValueError('Forecast merge is unsupported')
    for parent in parents:identifier(parent)
    chosen,objects,counts=read_selection(code_root,model_root);root=Path(exports_root).resolve(strict=True)
    metadata=[]
    for role,raw in objects.items():
        sha=digest(raw);target=contained(root,sha+'.bin')
        if target.exists():
            if target.read_bytes()!=raw:raise ValueError('Existing immutable forecast export changed')
        else:atomic(target,raw)
        metadata.append({'role':role,'sha256':sha,'size':len(raw)})
    return {'proposal':{'schemaVersion':1,'datasetId':dataset_id,'revisionId':revision_id,'parentRevisionIds':parents,'districtId':None,'kind':KIND,'adapterVersion':1,'objects':metadata,'counts':{'files':3}},'readerCounts':counts}


def stage_selection(code_root,objects,stage_root):
    exact(objects,ROLES);raw={role:read_bounded(file,ROLES[role]) for role,file in objects.items()};chosen=selection(raw['selection'])
    for family,role in [('giving','giving-model'),('pac','pac-model')]:
        if digest(raw[role])!=chosen[family]['sha256']:raise ValueError('Forecast selection digest mismatch')
    root=Path(stage_root).resolve(strict=True);pointers=pointer_bytes(chosen)
    for family,role in [('giving','giving-model'),('pac','pac-model')]:
        pointer=parse(pointers[family]);target=contained(root,pointer['active'],create=True)
        if target.exists() and target.read_bytes()!=raw[role]:raise ValueError('Staged immutable forecast model changed')
        if not target.exists():atomic(target,raw[role])
        atomic(contained(root,POINTERS[family]),pointers[family])
    verified,_,counts=read_selection(code_root,root)
    if verified!=chosen:raise ValueError('Forecast readers rejected selected model identity')
    return chosen,pointers,counts


def _plan_hash(plan):
    return digest(canonical({key:value for key,value in plan.items() if key!='phase'}))


def _transaction_root(profile,transaction_id):
    identifier(transaction_id);root=Path(profile).resolve(strict=True)/'forecast-transactions'
    root.mkdir(exist_ok=True)
    if root.is_symlink() or not root.resolve().is_relative_to(Path(profile).resolve()):raise ValueError('Unsafe forecast transaction root')
    target=root/transaction_id;target.mkdir(exist_ok=True)
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):raise ValueError('Unsafe forecast transaction directory')
    return target


def _validate_plan(plan,model_root,transaction_id):
    exact(plan,{'schemaVersion','kind','transactionId','revisionId','datasetId','eventId','modelRootHash','identity','old','new','event','phase'})
    if plan['schemaVersion']!=1 or plan['kind']!=KIND or plan['transactionId']!=transaction_id or plan['modelRootHash']!=native_access_state.path_hash(model_root) or plan['phase'] not in {'prepared','committed','rolledback'}:raise ValueError('Forecast transaction binding changed')
    for key in ('transactionId','revisionId','datasetId','eventId'):identifier(plan[key])
    exact(plan['identity'],{'serverId','appId','deviceId','principalId','accountIdentity'})
    if plan['identity']['appId']!='mentat':raise ValueError('Forecast transaction belongs to another application')
    for key in ('serverId','deviceId','principalId'):identifier(plan['identity'][key])
    if plan['identity']['accountIdentity']!='recipient_'+plan['identity']['principalId'].replace('-',''):raise ValueError('Forecast account binding changed')
    for side in ('old','new'):
        exact(plan[side],POINTERS)
        for item in plan[side].values():
            exact(item,{'exists','bytes','sha256'})
            if type(item['exists']) is not bool or not isinstance(item['bytes'],str) or len(item['bytes'])>8192:raise ValueError('Invalid forecast pointer backup')
            decoded=base64.b64decode(item['bytes'],validate=True)
            if digest(decoded)!=item['sha256'] or not item['exists'] and decoded:raise ValueError('Invalid forecast pointer backup hash')
            if decoded:parse(decoded)
    event=plan['event'];exact(event,{'eventId','type','occurredAt','districtId','status','counts','datasetId','revisionId'})
    if event['eventId']!=plan['eventId'] or event['datasetId']!=plan['datasetId'] or event['revisionId']!=plan['revisionId'] or event['type']!='dataset_imported' or event['status']!='succeeded' or event['districtId'] is not None or event['counts']!={'files':3} or type(event['occurredAt']) is not int or event['occurredAt']<946684800:raise ValueError('Invalid forecast activity intent')
    return plan


def _current_matches(path,item):
    if not path.exists():return not item['exists']
    return item['exists'] and digest(read_bounded(path,6144))==item['sha256']


def _finish(code_root,model_root,profile,plan,checkpoint=lambda _:None):
    model_root=Path(model_root).resolve(strict=True);txroot=_transaction_root(profile,plan['transactionId']);_validate_plan(plan,model_root,plan['transactionId'])
    if plan['phase']=='rolledback':return {'activated':False,'activityIntentDurable':False}
    for family,name in POINTERS.items():
        target=contained(model_root,name)
        if not (_current_matches(target,plan['old'][family]) or _current_matches(target,plan['new'][family])):raise ValueError('Local forecast publication changed during pending import')
    if plan['phase']!='committed':
        for family,name in POINTERS.items():
            atomic(contained(model_root,name),base64.b64decode(plan['new'][family]['bytes']))
            checkpoint('after_'+family+'_pointer')
        read_selection(code_root,model_root)
        plan['phase']='committed';native_access_state.write(txroot,'state',plan);checkpoint('after_domain_commit')
    marker=contained(model_root,MARKER)
    if marker.exists():marker.unlink()
    return {'activated':True,'activityIntentDurable':True,'activityPending':True,'event':plan['event'],'identity':plan['identity']}


def activate_selection(code_root,model_root,profile,objects,revision,transaction,identity,checkpoint=lambda _:None):
    exact(transaction,{'transactionId','eventId','priorRevisionId'});identifier(transaction['transactionId']);identifier(transaction['eventId'])
    if revision['kind']!=KIND or revision['districtId'] is not None:raise ValueError('Wrong forecast revision scope')
    root=Path(model_root).resolve(strict=True);txroot=_transaction_root(profile,transaction['transactionId']);existing=native_access_state.read(txroot,'state')
    if existing:
        if existing['revisionId']!=revision['revisionId'] or existing['eventId']!=transaction['eventId'] or existing['identity']!=identity:raise ValueError('Forecast transaction identity changed')
        return _finish(code_root,root,profile,existing,checkpoint)
    marker=contained(root,MARKER)
    if marker.exists():raise ValueError('A forecast import needs recovery before another can begin')
    with tempfile.TemporaryDirectory(prefix='forecast-stage-',dir=txroot) as directory:
        chosen,new,counts=stage_selection(code_root,objects,directory)
        for family in POINTERS:
            relative=parse(new[family])['active'];source=Path(directory)/relative;target=contained(root,relative,create=True)
            if target.exists() and target.read_bytes()!=source.read_bytes():raise ValueError('Existing immutable forecast bundle changed')
            if not target.exists():atomic(target,source.read_bytes())
    def record(raw,exists=True):return {'exists':exists,'bytes':base64.b64encode(raw).decode(),'sha256':digest(raw)}
    old={family:record(read_bounded(contained(root,name),6144)) if contained(root,name).exists() else record(b'',False) for family,name in POINTERS.items()}
    event={'eventId':transaction['eventId'],'type':'dataset_imported','occurredAt':int(time.time()),'districtId':None,'status':'succeeded','counts':{'files':3},'datasetId':revision['datasetId'],'revisionId':revision['revisionId']}
    plan={'schemaVersion':1,'kind':KIND,'transactionId':transaction['transactionId'],'revisionId':revision['revisionId'],'datasetId':revision['datasetId'],'eventId':transaction['eventId'],'modelRootHash':native_access_state.path_hash(root),'identity':identity,'old':old,'new':{key:record(value) for key,value in new.items()},'event':event,'phase':'prepared'}
    _validate_plan(plan,root,transaction['transactionId']);native_access_state.write(txroot,'state',plan)
    atomic(marker,canonical({'schemaVersion':1,'transactionId':transaction['transactionId'],'modelRootHash':plan['modelRootHash'],'planHash':_plan_hash(plan)}));checkpoint('after_prepare')
    return _finish(code_root,root,profile,plan,checkpoint)


def recover_pending_forecast(code_root,model_root,profile):
    root=Path(model_root).resolve(strict=True);marker=contained(root,MARKER)
    if not marker.exists():return {'pending':False}
    value=parse(read_bounded(marker,4096));exact(value,{'schemaVersion','transactionId','modelRootHash','planHash'})
    if value['schemaVersion']!=1 or value['modelRootHash']!=native_access_state.path_hash(root):raise ValueError('Forecast startup recovery belongs to another model root')
    txroot=_transaction_root(profile,value['transactionId']);plan=native_access_state.read(txroot,'state');_validate_plan(plan,root,value['transactionId'])
    if value['planHash']!=_plan_hash(plan):raise ValueError('Forecast recovery plan changed')
    return _finish(code_root,root,profile,plan)


def recover_activation(code_root,model_root,profile,revision,transaction,identity):
    txroot=_transaction_root(profile,transaction['transactionId']);plan=native_access_state.read(txroot,'state');_validate_plan(plan,model_root,transaction['transactionId'])
    if plan['revisionId']!=revision['revisionId'] or plan['eventId']!=transaction['eventId'] or plan['identity']!=identity:raise ValueError('Forecast recovery identity changed')
    return _finish(code_root,model_root,profile,plan)
