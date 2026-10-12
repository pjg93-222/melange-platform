"""Keep the local server's existing dashboard alias on the current generation."""
from pathlib import Path
import hashlib,json,os,shutil,sys,tempfile
import importlib.util
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from paths import ROOT
from moneyball_core import dump_atomic

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

# The preview can serve the clean research workspace instead of a copy of Moneyball.html:
# build_clean_workspace.py writes its shell to the alias, and staffer_live_server.py serves that
# shell for every workspace route when this marker is in its first 128 bytes. Copying
# Moneyball.html over the shell would take the workspace off "/", so this step rebuilds the shell
# and its data from the current generation instead. Neither path writes Moneyball.html.
CLEAN_MARKER=b'<!-- melange-clean-workspace-v2 -->'
CLEAN_MODE='clean-workspace-v2'
CLEAN_BUILDER='fec/surfaces/build_clean_workspace.py'
# The generation artifacts build_clean_workspace.main() reads; the receipt binds their hashes.
CLEAN_INPUTS=('member_desk_bundle.json','cu-member-desk.html','Moneyball Dashboard.html','lobby-bills.html',
              'lobby-champions.html','lobby-forecast.html','lobby-interactions.html','lobby-network.html',
              'lobby-screen.html','outputs/higgsfield-tab-mockups-2026-09-21/standardized/navigation-icons.json')
RECEIPT='data/engine/live_dashboard_alias_receipt.json'

def is_clean_workspace(path):
    try:
        with open(path,'rb') as handle:return CLEAN_MARKER in handle.read(128)
    except OSError:return False

def clean_inputs(root):return {name:digest(root/name) for name in CLEAN_INPUTS if (root/name).is_file()}

def clean_workspace_current(root):
    """True when the alias is the clean workspace shell that its receipt binds to this generation."""
    root=Path(root);alias=root/'Moneyball_Redesign.html'
    try:
        receipt=json.loads((root/RECEIPT).read_text(encoding='utf-8'))
        return bool(is_clean_workspace(alias) and receipt.get('mode')==CLEAN_MODE and receipt.get('status')=='PASS'
                    and receipt.get('sha256')==digest(alias) and receipt.get('source_sha256')==digest(root/'Moneyball.html')
                    and receipt.get('workspace_inputs_sha256')==clean_inputs(root))
    except (OSError,ValueError,AttributeError):return False

def rebuild_clean_workspace(root,source,alias,guard):
    builder=root/CLEAN_BUILDER
    if not builder.is_file():raise ValueError('The served clean workspace has no builder: '+CLEAN_BUILDER)
    source_hash=digest(source);prior=digest(alias);inputs=clean_inputs(root)
    spec=importlib.util.spec_from_file_location('build_clean_workspace',builder)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.main()
    if not is_clean_workspace(alias):raise ValueError('The clean workspace build did not write its shell')
    if digest(source)!=source_hash or clean_inputs(root)!=inputs:raise ValueError('Dashboard changed during alias synchronization')
    receipt={'schema':'live-dashboard-alias-v1','status':'PASS','checked_at':datetime.now(timezone.utc).isoformat(),
             'mode':CLEAN_MODE,'source':str(source),'alias':str(alias),'sha256':digest(alias),'previous_alias_sha256':prior,
             'source_sha256':source_hash,'workspace_inputs_sha256':inputs,'builder':str(builder),
             'prepublication_source_guard':guard['status'] if guard else 'LEGACY_NO_AUTOMATIC_SOURCE'}
    dump_atomic(receipt,str(root/RECEIPT))
    return receipt

def current_receipt(root):
    """The alias receipt while clean_workspace_current(root) holds, else None."""
    root=Path(root)
    if not clean_workspace_current(root):return None
    try:return json.loads((root/RECEIPT).read_text(encoding='utf-8'))
    except (OSError,ValueError):return None

def rebind_after_rebuild(root,before):
    """Re-bind a receipt that was current (current_receipt() before the rebuild) to the shell a presentation rebuild
    (build_clean_workspace.py run directly) just wrote. The generation it guarded must be unchanged: Moneyball.html
    and every workspace input hash as before. Never binds a generation the guarded sync step did not."""
    root=Path(root);alias=root/'Moneyball_Redesign.html'
    if not before:return {'status':'SKIP_NOT_CURRENT'}
    if not is_clean_workspace(alias):raise ValueError('The clean workspace build did not write its shell')
    if before.get('source_sha256')!=digest(root/'Moneyball.html') or before.get('workspace_inputs_sha256')!=clean_inputs(root):
        return {'status':'SKIP_GENERATION_CHANGED'}
    receipt=dict(before,checked_at=datetime.now(timezone.utc).isoformat(),sha256=digest(alias),
                 previous_alias_sha256=before.get('sha256'),
                 rebound_by='build_clean_workspace.py: presentation rebuild of the generation this receipt guarded')
    dump_atomic(receipt,str(root/RECEIPT))
    return receipt


def sync(root):
    root=Path(root);source=root/'Moneyball.html';alias=root/'Moneyball_Redesign.html'
    if not alias.exists():return {'status':'SKIP_ALIAS_ABSENT'}
    if not source.is_file() or source.stat().st_size==0:raise ValueError('Current generated dashboard is missing')
    guard=None
    from legislation import sentinel_admissions as SA
    if (root/SA.RELATIVE).is_file() or (root/'data/engine/sentinel_admission_policy.json').is_file():
        from audit.audit_source_to_dashboard import verify
        guard=verify(root,check_alias=False)
        if guard['status']!='PASS':
            raise ValueError('Dashboard publication refused: '+'; '.join(guard.get('errors',[])))
    if is_clean_workspace(alias):return rebuild_clean_workspace(root,source,alias,guard)
    source_hash=digest(source);prior=digest(alias)
    if source_hash!=prior:
        backup=root/'backups/Moneyball_Redesign.previous-generation.html';backup.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(alias,backup)
        fd,name=tempfile.mkstemp(prefix=alias.name+'.',suffix='.candidate',dir=root);os.close(fd)
        try:
            shutil.copyfile(source,name)
            if digest(Path(name))!=source_hash or digest(source)!=source_hash or digest(alias)!=prior:raise ValueError('Dashboard changed during alias synchronization')
            os.replace(name,alias)
        finally:
            if os.path.exists(name):os.unlink(name)
    receipt={'schema':'live-dashboard-alias-v1','status':'PASS','checked_at':datetime.now(timezone.utc).isoformat(),
             'source':str(source),'alias':str(alias),'sha256':source_hash,'previous_alias_sha256':prior,
             'prepublication_source_guard':guard['status'] if guard else 'LEGACY_NO_AUTOMATIC_SOURCE'}
    dump_atomic(receipt,str(root/'data/engine/live_dashboard_alias_receipt.json'))
    return receipt

if __name__=='__main__':print(json.dumps(sync(ROOT)))
