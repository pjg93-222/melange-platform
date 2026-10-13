# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.abspath(__file__)));from paths import ROOT as MB_ROOT  # centralized root
"""WEEKLY REFRESH (user 2026-07-02: "if the UI could be updated every week that would be great").
Unattended data-refresh + full pipeline + dashboard regeneration, designed for Windows Task
Scheduler (registered as 'MoneyballWeeklyRefresh', Sundays 06:00; remove with
`schtasks /Delete /TN MoneyballWeeklyRefresh /F`). Also fine to run by hand anytime.

Steps (each logged to data/engine/weekly_refresh.log; scheduled source collectors,
collection-integrity gates, and the canonical pipeline fail hard before publication):
  1. CREC floor speeches, incremental (last 14 days; resumable, non-session days cached)
  2. CHRG finance hearings, incremental (last 60 days; publication lags)
  3. big5_traits          — traits on the grown speech corpus
  4. social_interaction_layer + build_multilayer_network — refresh the social edge layer
  5. build_media_blend    — re-blend press+social into the workbook raw layer (skipped gracefully
                            if Excel has the workbook open/locked)
  6. run_pipeline         — the whole chain: stance, rank authority, engines, ALL dashboards, audit
X/Truth/Bluesky collectors are NOT run unattended (keys/discovery are interactive); run
tweet_collector.py + process_quorum.py manually when new exports/keys exist, then this script."""
import sys,os,subprocess,datetime,json,time,atexit,signal,re,ast
import bounded_subprocess   # shared bounded launcher (fec/ is sys.path[0], set on line 2)
import compute_engine       # per-phase engine (ops/compute/engines.json): host or compute container; all 'windows' = today's code path
from pipeline_lock import (
    LOCK_OWNER_PID_ENV, LOCK_TOKEN_ENV, child_lock_environment, pipeline_run_lock,
)
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
FE=os.path.dirname(os.path.abspath(__file__))
LOG=os.path.join(MB_ROOT,'data/engine/weekly_refresh.log')
def log(msg):
    line='[%s] %s'%(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),msg)
    print(line)
    with open(LOG,'a',encoding='utf-8') as f: f.write(line+'\n')
# A dedicated whole-refresh lock coordinates the Sunday task with the temporary collector
# supervisor, which can also invoke weekly_refresh after a backfill.  Task Scheduler's IgnoreNew
# applies only within one task and therefore cannot prevent those two independent owners.
_WEEKLY_LOCK_PATH=os.path.join(MB_ROOT,'.weekly_refresh.lock')
def _acquire_weekly_lock():
    handle=open(_WEEKLY_LOCK_PATH,'a+b')
    handle.seek(0,os.SEEK_END)
    if handle.tell()==0:
        handle.write(b'0'); handle.flush()
    handle.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except (OSError,IOError):
        handle.close()
        log('WEEKLY REFRESH BLOCKED — another scheduled/supervisor refresh owns the shared lock')
        sys.exit(75)
    return handle
def _release_weekly_lock():
    try:
        _WEEKLY_LOCK_HANDLE.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(_WEEKLY_LOCK_HANDLE.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(_WEEKLY_LOCK_HANDLE.fileno(),fcntl.LOCK_UN)
        _WEEKLY_LOCK_HANDLE.close()
    except BaseException:
        pass
_WEEKLY_LOCK_HANDLE=_acquire_weekly_lock()
atexit.register(_release_weekly_lock)
# Hold the canonical writer lease for the ENTIRE collector-to-publication transaction.  The former
# weekly-only lock prevented duplicate schedules but did not exclude a standalone run_pipeline;
# that allowed collectors to mutate social_media.db while another pipeline was auditing it.
try:
    _PIPELINE_LOCK_CONTEXT=pipeline_run_lock(owner_kind='weekly_refresh')
    _PIPELINE_LOCK_LEASE=_PIPELINE_LOCK_CONTEXT.__enter__()
except RuntimeError as _lock_error:
    log('WEEKLY REFRESH BLOCKED — %s'%_lock_error)
    sys.exit(75)
os.environ.update(child_lock_environment(_PIPELINE_LOCK_LEASE))
def _release_pipeline_lock():
    os.environ.pop(LOCK_TOKEN_ENV,None)
    os.environ.pop(LOCK_OWNER_PID_ENV,None)
    try:
        _PIPELINE_LOCK_CONTEXT.__exit__(None,None,None)
    except BaseException:
        pass
atexit.register(_release_pipeline_lock)
# chain=container hands the writer lease to the container for the length of the chain step. The container judges a lease by
# its own pid space (pipeline_lock.host_identity), so it can neither adopt this process's lease nor take over its file; it takes a
# fresh one (heartbeat-backed, which a Windows peer honours) once this process has removed its own. The weekly lock above stays
# held throughout, and failing to take the lease back afterwards is a hard abort: nothing runs between steps without a lease.
def _lease_handoff_out():
    os.environ.pop(LOCK_TOKEN_ENV,None)
    os.environ.pop(LOCK_OWNER_PID_ENV,None)
    _PIPELINE_LOCK_CONTEXT.__exit__(None,None,None)
    log('   chain container: writer lease released to the container; this process takes it back when the container exits')
def _lease_handoff_in():
    global _PIPELINE_LOCK_CONTEXT,_PIPELINE_LOCK_LEASE
    try:
        _PIPELINE_LOCK_CONTEXT=pipeline_run_lock(owner_kind='weekly_refresh')
        _PIPELINE_LOCK_LEASE=_PIPELINE_LOCK_CONTEXT.__enter__()
    except RuntimeError as _lock_error:
        _hard_abort('chain container lease',[],'writer lease not taken back after the container: %s'%_lock_error)
    os.environ.update(child_lock_environment(_PIPELINE_LOCK_LEASE))
# Glass Box (house rule: every process emits to the shared stream or it is not acceptable).
# The weekly refresh was the last unattended producer writing only to a log file, so a Sunday
# that aborted — or never started — was invisible to the dashboard. Reuse the SHIPPED emitter
# (surfaces/glassbox_emit.py) rather than re-deriving the schema: it already does the atomic
# temp + os.replace write and swallows its own failures, so observation can never take down
# the refresh. Missing emitter -> no-op stubs, exactly as run_pipeline.py does.
try:
    from surfaces.glassbox_emit import emit as _gb, new_run_id as _gb_run, new_correlation_id as _gb_corr
except Exception:
    def _gb(*a,**k): return None
    def _gb_run(prefix='moneyball'): return ''
    def _gb_corr(): return ''
_GB_RUN=_gb_run('moneyball-weekly-refresh')
_GB_CORR=_gb_corr()
def _emit(kind,status,summary,**kw):
    kw.setdefault('run_id',_GB_RUN); kw.setdefault('correlation_id',_GB_CORR)
    kw.setdefault('source','moneyball-weekly-refresh')
    return _gb(kind,status,summary,**kw)
# Machine-readable health marker: anything (dashboard, sentinel, a later agent) can read the
# refresh's state without parsing this script's prose log. Atomic same-directory replace so a
# poller never reads a half-written file.
STATUS=os.path.join(MB_ROOT,'data/engine/weekly_refresh_status.json')
_STATUS={'last_start':None,'last_outcome':None,'failures':[],'rank_moves':None}
def _write_status(**kw):
    _STATUS.update(kw)
    # HEARTBEAT. Without these two fields a reader cannot tell a live RUNNING from one frozen by a
    # death that never reached a terminal _write_status (Ctrl-C raises KeyboardInterrupt, which
    # step()'s `except Exception` does not catch; Task Scheduler's ExecutionTimeLimit uses
    # TerminateProcess, which runs nothing at all). collector_supervisor's GATE-1 can only detect
    # that case because it holds its own launch timestamp; nothing else can.
    _STATUS['pid']=os.getpid()
    _STATUS['last_update']=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        tmp='%s.%d.tmp'%(STATUS,os.getpid())
        with open(tmp,'w',encoding='utf-8') as f: json.dump(_STATUS,f,ensure_ascii=False,indent=1,sort_keys=True)
        os.replace(tmp,STATUS)
    except Exception as e:
        try: log('status marker write failed: %s'%str(e)[:120])
        except BaseException: pass
def _finalize_status():
    """Stamp a terminal outcome for every exit path that reaches atexit but never wrote one:
    KeyboardInterrupt, an unhandled exception, SystemExit from a signal handler that raced.
    Only RUNNING* is rewritten, so COMPLETE / HARD_FAILURE / KILLED_SIGBREAK are untouched, and
    the new value is NOT in collector_supervisor's accepted set -- GATE-1 keeps failing closed."""
    if str(_STATUS.get('last_outcome') or '').startswith('RUNNING'):
        _write_status(last_outcome='ABORTED_NO_TERMINAL_OUTCOME')
atexit.register(_finalize_status)
# ---- KNOWN Paul-gated audit items -------------------------------------------------------
# The ratified list and its parser moved to fec\audit_failure_gate.py on 2026-08-28 so that this
# refresh (GATE-1) and collector_supervisor's GATE-2 — which runs audit_full DIRECTLY as its last
# step and was previously unable to pass at all while these two standing items fire — share ONE
# source of truth. Re-exported under the original names so nothing downstream had to move.
from audit_failure_gate import KNOWN_GATED_FAILURES,audit_failures as _audit_failures,tolerate_known_gated
def _audit_failure_gate(stdout,stderr):
    """Decide whether a hard chain failure is ONLY the known Paul-gated audit dissent.
    Returns (tolerate,names). Every uncertain path returns tolerate=False. The chain-tail guard
    below is applied by step(), not here."""
    return tolerate_known_gated(stdout,stderr,log=log)
# ---- chain-tail guard ---------------------------------------------------------------------
# The tolerance above is safe ONLY because audit_full.py is the LAST entry of run_pipeline's
# CHAIN list: every producer has already succeeded by the time the audit verdict lands, so a
# lone audit dissent cannot be masking an unrun producer. If anyone ever appends a chain step
# AFTER audit_full.py, that invariant breaks silently. Re-verify it on every tolerate decision
# by TEXTUALLY parsing run_pipeline.py's source — never import run_pipeline (module-scope code
# there walks and validates the chain, i.e. import triggers execution in this repo). Parse
# defensively; any uncertainty fails CLOSED (returns False), same posture as _audit_failures.
_RUN_PIPELINE_PATH=os.path.join(FE,'run_pipeline.py')
_CHAIN_BLOCK_RE=re.compile(r'^CHAIN\s*=\s*\[(.*?)\n\]',re.M|re.S)
_CHAIN_ENTRY_RE=re.compile(r"\(\s*'([^']+)'\s*,\s*\[[^\]]*\]\s*\)")
def _chain_tail_is_audit_full():
    """Return True only if run_pipeline.py's CHAIN list's LAST ('name',[...]) entry is
    audit_full.py. Any parse uncertainty (unreadable file, no CHAIN=[...] block, no tuples
    found) returns False."""
    try:
        with open(_RUN_PIPELINE_PATH,encoding='utf-8') as f: src=f.read()
    except Exception as e:
        log('   chain-tail guard: cannot read run_pipeline.py (%s)'%type(e).__name__)
        return False
    block=_CHAIN_BLOCK_RE.search(src)
    if not block:
        log('   chain-tail guard: no "CHAIN = [ ... ]" block found in run_pipeline.py')
        return False
    entries=_CHAIN_ENTRY_RE.findall(block.group(1))
    if not entries:
        log('   chain-tail guard: no (\'name\',[...]) tuples parsed from the CHAIN block')
        return False
    return entries[-1]=='audit_full.py'
# ---- orphaned-child guard -----------------------------------------------------------------
# step() creates every child with CREATE_NEW_PROCESS_GROUP, so a console CTRL+C never reaches it.
# On any abnormal exit (KeyboardInterrupt, unhandled exception, SIGBREAK) the interpreter runs the
# atexit handlers below, which DELETE the pipeline writer lease -- while the child that inherited
# that lease (a collector, or run_pipeline.py mid compute_pillars --write) keeps running. The next
# scheduled run then acquires a free lease and a second writer starts on the same artifacts. Kill
# the live child's tree FIRST. Registered here so LIFO ordering puts it ahead of both lock
# releases (registered at module lines 66 and 84).
# The NT tree kill is bounded_subprocess.kill_tree's BOUNDED `taskkill /F /T` (it was an
# unbounded subprocess.run): this runs inside atexit, while the lease is still held, so a killer
# that hung here would hang the process exactly where it may least afford to.
_LIVE_CHILD=[None]
def _adopt_live_child(proc):
    """run_bounded's on_start hook: publish the live child to the guard above the moment it
    exists, BEFORE the wait begins, so an abort in the window between launch and the first wait
    still finds a tree to kill. step()'s own finally clears the slot again on every path."""
    _LIVE_CHILD[0]=proc
def _kill_live_child():
    proc=_LIVE_CHILD[0]
    if proc is None or proc.poll() is not None: return
    try: log('KILLING orphaned step child pid %d before the writer lease is released'%proc.pid)
    except BaseException: pass
    try:
        if os.name=='nt':
            bounded_subprocess.kill_tree(proc)
        else:
            os.killpg(proc.pid,9)
        proc.wait(timeout=60)
    except BaseException: pass
    finally:
        _LIVE_CHILD[0]=None
atexit.register(_kill_live_child)
_tolerated=[]
log('===== WEEKLY REFRESH START =====')
_started_at=datetime.datetime.now()
_refresh_complete=False
_rank_moves=None
_write_status(last_start=_started_at.strftime('%Y-%m-%d %H:%M:%S'),last_outcome='RUNNING',failures=[],rank_moves=None,
              owner_pid=os.getpid(),active_step='starting',last_heartbeat=_started_at.isoformat())
_emit('refresh.run','running','WEEKLY REFRESH START',stage='weekly_refresh')
def _email_report():
    try:
        import refresh_email
        refresh_email.send_report(LOG,_started_at,'SUCCESS' if _refresh_complete else 'FAILED')
    except BaseException as e:
        try: log('EMAIL REPORT FAILED - %s'%type(e).__name__)
        except BaseException: pass
atexit.register(_email_report)
def _console_kill(signum,frame):   # CTRL_CLOSE/logoff/shutdown arrive as SIGBREAK; ~5s grace before TerminateProcess
    try:
        log('KILLED — console close/logoff/shutdown (SIGBREAK)')
        _write_status(last_outcome='KILLED_SIGBREAK')
        _emit('refresh.run','failed','KILLED — console close/logoff/shutdown (SIGBREAK)',stage='weekly_refresh',
              duration_ms=int((datetime.datetime.now()-_started_at).total_seconds()*1000))
    except BaseException: pass
    sys.exit(1)
if hasattr(signal,'SIGBREAK'): signal.signal(signal.SIGBREAK,_console_kill)
try:
    _engines=compute_engine.snapshot()   # engines.json is read once: an unreadable file is a pre-step failure, and no step changes engine mid-run
    if 'container' in _engines.values(): log('compute engines: %s'%', '.join('%s=%s'%kv for kv in sorted(_engines.items())))
    # Only the scripts step() launches are producers; unrelated helpers may share a basename.
    # From 2026-09-10 two __init__.py under fec/ made this walk a FATAL pre-step failure, so the
    # refresh died in 0.2 s before step 1 (found by the 2026-09-19 sandbox run). Same rule as
    # run_pipeline.py's walk; the names come from this file's own step() calls, read as text.
    with open(os.path.abspath(__file__),encoding='utf-8') as _self_src:
        _step_producer_names={
            os.path.basename(_call.args[1].elts[0].value)
            for _call in ast.walk(ast.parse(_self_src.read()))
            if isinstance(_call,ast.Call) and getattr(_call.func,'id',None)=='step'
            and len(_call.args)>1 and isinstance(_call.args[1],ast.List) and _call.args[1].elts
            and isinstance(_call.args[1].elts[0],ast.Constant)
            and isinstance(_call.args[1].elts[0].value,str)}
    if not _step_producer_names:
        raise RuntimeError('no step() producers parsed from %s'%os.path.basename(__file__))
    _producer_paths={}
    for _producer_dir,_producer_dirs,_producer_files in os.walk(FE):
        _producer_dirs[:]=sorted(
            _dir for _dir in _producer_dirs
            if _dir not in ('tests','attic','__pycache__')
            and not _dir.startswith('_quarantine')
        )
        for _producer_file in sorted(_producer_files):
            if not _producer_file.endswith('.py'):
                continue
            if _producer_file not in _step_producer_names:
                continue
            _producer_full_path=os.path.join(_producer_dir,_producer_file)
            if _producer_file in _producer_paths:
                raise RuntimeError('duplicate producer basename %s: %s and %s'%
                                   (_producer_file,_producer_paths[_producer_file],
                                    _producer_full_path))
            _producer_paths[_producer_file]=_producer_full_path
except BaseException as e:
    try:
        log('FATAL pre-step failure: %s'%str(e)[:200])
        _write_status(last_outcome='FATAL_PRESTEP')
        _emit('refresh.run','failed','FATAL pre-step failure: %s'%str(e)[:200],stage='weekly_refresh',
              detail='%s: %s'%(type(e).__name__,str(e)[:1000]))
    except BaseException: pass
    raise
def _producer_path(_producer_name):
    _producer_basename=os.path.basename(_producer_name)
    if _producer_basename not in _producer_paths:
        raise RuntimeError('weekly refresh producer script not found: %s'%_producer_name)
    return _producer_paths[_producer_basename]
def _hard_abort(name,failures,why):
    log('HARD FAILURE at %s — %s'%(name,why))
    # Name the failing STEP in the status marker. Every hard step that passes no `gate=` reaches
    # here with failures=[], so the marker used to record last_outcome='HARD_FAILURE' with
    # failures=[] and the step name existed only in weekly_refresh.log — an unattended supervisor
    # tick could see that the refresh died but not where (observed all day 2026-08-28).
    # SEMANTICS, deliberately unchanged for existing readers: `failures` is only ever consulted
    # alongside `last_outcome`. The single programmatic consumer, collector_supervisor's GATE-1
    # receipt (fec\collect\collector_supervisor.py:211), reads it exclusively on the COMPLETE /
    # COMPLETE_WITH_KNOWN_GATED_FAILURES path, where it still carries exactly the tolerated audit
    # ck() names and nothing else. On HARD_FAILURE the list now leads with the failing step name
    # (followed by any gate-parsed novel audit names) instead of being empty.
    _reported=[name]+[f for f in failures if f!=name]
    _emit('refresh.run','failed','HARD FAILURE at %s — %s'%(name,why),stage=name,
          detail='; '.join(_reported)[:2000],
          duration_ms=int((datetime.datetime.now()-_started_at).total_seconds()*1000),
          metadata={'step':name,'reason':why,'failures':list(failures),'tolerated':False})
    _write_status(last_outcome='HARD_FAILURE',failures=_reported)
    sys.exit(1)
# WHY THE WAIT IS SHAPED THIS WAY. This is an UNATTENDED job holding the pipeline writer lease
# for its whole run, so every wait in it must end. The old shape could not: on a timeout it ran an
# unbounded taskkill and then an unbounded proc.wait(), and a child wedged in kernel I/O (measured
# here: a process stuck on OneDrive placeholder I/O stayed unkillable for about an hour) pinned the
# refresh open, kept the lease, and made Task Scheduler skip every later run. PIPEs added a second
# hold: a detached grandchild that inherited one keeps the reader alive after the child is gone.
# bounded_subprocess.run_bounded is the fix — REAL temp files, wait(timeout), bounded tree kill.
def step(name,args,timeout=3600,hard=False,gate=None,partial=None):
    t=time.time()
    _write_status(active_step=name,last_heartbeat=datetime.datetime.now().isoformat(),owner_pid=os.getpid())
    try:
        flags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0
        spec=compute_engine.launch(_producer_path(args[0]),args[1:],cwd=FE)   # engine 'windows' (the default): [sys.executable, producer, *args], env None
        handoff=spec.phase=='chain'
        if spec.container: log('   %s runs in container %s'%(name,spec.container))
        if handoff: _lease_handoff_out()
        try:
            run=bounded_subprocess.run_bounded(spec.argv,
                                               timeout=timeout,env=spec.env,cwd=FE,creationflags=flags,
                                               start_new_session=os.name!='nt',
                                               on_start=_adopt_live_child)
        finally:
            _LIVE_CHILD[0]=None   # released on EVERY path: success, timeout, launch failure
            compute_engine.reap(spec)   # a container whose client was killed keeps running; --rm removes one that ended
            if handoff: _lease_handoff_in()
        stdout,stderr=run.stdout,run.stderr
        if run.timed_out:
            log('%-28s TIMEOUT after %ds%s'%(name,timeout,' — aborting' if hard else ' — continuing'))
            log('   '+run.timeout_note)
            if hard: _hard_abort(name,[],'timeout after %ds'%timeout)
            return False   # gate is NOT consulted on a timeout: 124 is never a known dissent
        tail=(stdout or '').strip().splitlines()[-1:] or ['']
        ok=run.returncode==0
        log('%-28s %s %5.0fs  %s'%(name,'OK' if ok else '!!',time.time()-t,tail[0][:110]))
        if not ok:
            log('   stderr: '+(stderr or '')[-300:].replace('\n',' | '))
            if hard and partial is not None and partial(run.returncode,t):
                return True   # a PARTIAL its owner proves excusable; the hard step after it rules
            if hard:
                tolerate,failures=gate(stdout,stderr) if gate else (False,[])
                if tolerate and not _chain_tail_is_audit_full():
                    log('   chain tail is no longer audit_full.py — tolerance refused, legacy hard abort')
                    tolerate=False
                if tolerate:
                    log('KNOWN Paul-gated audit items — refresh continues')
                    for f in failures: log('   tolerated: %s'%f)
                    _tolerated.extend(failures)
                    _emit('refresh.tolerated_failure','completed',
                          'tolerated %d known Paul-gated audit failure(s) at %s'%(len(failures),name),
                          stage=name,detail='; '.join(failures)[:2000],
                          duration_ms=int((time.time()-t)*1000),
                          metadata={'step':name,'failures':list(failures),'tolerated':True})
                    _write_status(last_outcome='RUNNING_AFTER_KNOWN_FAILURE',failures=list(_tolerated))
                    return True   # soft-complete: audit_full is the chain's LAST step, so every
                                  # producer already wrote; only the audit's verdict dissented
                _hard_abort(name,failures,'step exited %s'%run.returncode)
        return ok
    except Exception as e:   # a launch failure (missing producer) still lands here; run_bounded
                             # handles TimeoutExpired itself and never re-raises it to step()
        log('%-28s ERROR %s'%(name,str(e)[:120]))
        if hard: _hard_abort(name,[],'%s: %s'%(type(e).__name__,str(e)[:120]))
        return False
# ---- press PARTIAL excusal --------------------------------------------------------------------
# refresh_press_delta.R exits 3 whenever ANY body is still pending (:843, :990), and its pending
# set (press_backlog.R:260-282) knows nothing of Paul's 2026-09-19 three-run tombstones (kept by
# D-159), so a run the intake proof accepts still exits 3 and this hard step aborted every Sunday
# from 2026-09-20 to 10-11 before the intake could rule. Exit 3 is excused ONLY for the run THIS
# step sealed, and only through build_press_engine_feed.inspect_run -- the test the intake's
# discover_run applies, whose completion proof is the intake's own -- so 'press intake audit'
# below audits exactly this run and stays the hard authority. Any doubt falls through to the
# legacy hard abort: handed a refused day, discover_run audits an OLDER sealed run instead
# (outputs/press_drain_20261011.log: the 10-11 press intake audited press_refresh_20260904).
def _press_partial_excused(returncode,started):
    if returncode!=3: return False
    root=os.path.join(MB_ROOT,'outputs','press_refresh_'+datetime.date.fromtimestamp(started).strftime('%Y%m%d'))
    try:
        from collect.build_press_engine_feed import inspect_run
        completed,receipt=inspect_run(root)
        if receipt.get('status')!='PARTIAL' or completed.timestamp()<started-1:   # 1 s: whole-second R stamps
            raise ValueError('%s is not a PARTIAL run sealed by this step'%os.path.basename(root))
    except Exception as e:
        log('   press PARTIAL not excused — %s: %s'%(type(e).__name__,str(e)[:300]))
        return False
    log('   press PARTIAL excused: all %s pending body URL(s) tombstoned — press intake audit rules'%receipt.get('pending_body_urls'))
    _emit('refresh.step','partial','press delta refresh: %s pending body URL(s) excused by the intake proof'%receipt.get('pending_body_urls'),
          stage='press delta refresh',metadata={'run_root':root,'pending_body_urls':receipt.get('pending_body_urls')})
    return True
try:   # user directive 2026-07-06: hold a system wake lock for the refresh's lifetime (the 01:10
    import ctypes   # machine sleep stalled collectors 5h; a mid-refresh sleep would do the same)
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    log('keep-awake: system wake lock held for this refresh')
except Exception as _e:
    log('keep-awake unavailable: %s' % str(_e)[:60])
today=datetime.date.today()
yesterday=today-datetime.timedelta(days=1)
prev=None
try: prev=json.load(open(os.path.join(MB_ROOT,'ranks_python.json')))['rank']
except Exception: pass
_deferred_collection_failures=[]
if not step('CREC floor speeches',['collect/fetch_congressional_record.py','crec','--since',str(today-datetime.timedelta(days=14))],timeout=3000):
    _deferred_collection_failures.append('CREC floor speeches')
if not step('CHRG finance hearings',['collect/fetch_congressional_record.py','chrg','--since',str(today-datetime.timedelta(days=400))],timeout=3000):   # coverage audit 2026-07-06: hearing transcripts publish MONTHS late — a 60-day window structurally missed most of them forever ('ok' markers keep the wide window cheap)
    _deferred_collection_failures.append('CHRG finance hearings')
# Open-universe bill discovery.  The collector retains canonical IDs for every
# Advanced Search candidate and never scores them; the workbook sync later in
# run_pipeline publishes the checkpoint with pending/adjudicated review state.
if not step('Congress.gov bill discovery',[
    'legislation/refresh_legislation_discovery.py',
],timeout=21600,hard=False):
    # NOT deferred, and never a hard abort: discovery yields pending-only candidates that
    # cannot score, so a failed or timed-out walk leaves every scored input untouched. The
    # collector resumes (--resume), so the stale candidate set simply carries to next week;
    # aborting the whole refresh over it would strand complete press/interview evidence.
    log('   FAIL: Congress.gov bill discovery incomplete -- last-good candidate set retained')
    _deferred_collection_failures.append('Congress.gov bill discovery')
if not step('Official bill evidence and automatic scoring admission',[
    'legislation/refresh_sentinel_admissions.py',
],timeout=14400,hard=False):
    _deferred_collection_failures.append('Official bill evidence and automatic scoring admission')
if not step('FEC source money refresh',['collect/refresh_fec_money.py'],timeout=7200,hard=False):
    _deferred_collection_failures.append('FEC source money refresh')
if not step('Congress rollcall source refresh',['legislation/refresh_vote_sources.py'],timeout=1800,hard=False):
    _deferred_collection_failures.append('Congress rollcall source refresh')
# Start the bill update before the slower interview and press legs. Its result is a hard final-
# publication prerequisite, but the failure is deferred until independent press acceptance has
# finished. Omitting an explicit --receipt uses the producer's root-resolved default; the old
# relative argument wrote the receipt under fec/data while production consumers checked root data.
_bill_repository_ok=step('Congress.gov bill repository',[
    'legislation/update_bill_repository.py',
],timeout=14400,hard=False)
if not _bill_repository_ok:
    _deferred_collection_failures.append('Congress.gov bill repository')
# User-directed end-to-end refresh: update the official bulk inputs and let
# the network-free hash gate rebuild the forecast when those inputs change.
os.environ.setdefault('BILLSTATUS_BULK_REFRESH','1')
os.environ.setdefault('COSPONSOR_FORECAST_REFRESH','1')
if not step('GovInfo BILLSTATUS bulk refresh',[
    'legislation/refresh_billstatus_bulk.py',
],timeout=3600,hard=False):
    _deferred_collection_failures.append('GovInfo BILLSTATUS bulk refresh')
# interview accrual (N-gate audit 2026-07-10): the refresh re-DERIVED interaction features every
# week but nothing GREW the interview corpus — reactivity coverage (round-4 gate: MEM_N>=250,
# now 203) was frozen forever. Incremental resumes are cheap (per-URL 'ok' cache). Every scheduled
# source now has an explicit completion result. Each failure blocks the final dashboard, but is
# deferred long enough for an
# independently valid press run to be audited and promoted into its cumulative ledger. Historical
# Fox/MS NOW sources are still re-enumerated: their collector check time is reported separately
# from the terminal date of the newest transcript the source actually exposes.
if not step('interviews: CNN (14d)',['fetch_interviews.py','cnn','--since',str(today-datetime.timedelta(days=14)),'--until',str(yesterday),'--strict'],timeout=1800,hard=False):
    _deferred_collection_failures.append('interviews: CNN (14d)')
if not step('interviews: MTP',['fetch_interviews.py','mtp','--strict'],timeout=1200,hard=False):
    _deferred_collection_failures.append('interviews: MTP')
if not step('interviews: CBS',['fetch_interviews.py','cbs','--strict'],timeout=1200,hard=False):
    _deferred_collection_failures.append('interviews: CBS')
if not step('interviews: ABC',['fetch_interviews.py','abc','--strict'],timeout=1200,hard=False):
    _deferred_collection_failures.append('interviews: ABC')
if not step('interviews: PBS (21d)',['fetch_interviews.py','pbs','--since',str(today-datetime.timedelta(days=21)),'--until',str(yesterday),'--strict'],timeout=1800,hard=False):
    _deferred_collection_failures.append('interviews: PBS (21d)')
if not step('interviews: NPR (14d)',['fetch_interviews.py','npr','--since',str(today-datetime.timedelta(days=14)),'--until',str(yesterday),'--strict'],timeout=1800,hard=False):
    _deferred_collection_failures.append('interviews: NPR (14d)')
if not step('interviews: Fox archive audit',['fetch_interviews.py','fox','--strict'],timeout=1800,hard=False):
    _deferred_collection_failures.append('interviews: Fox archive audit')
if not step('interviews: MS NOW',['fetch_interviews.py','msnow','--strict'],timeout=3600,hard=False):   # full archive walk measured 25m and previously crossed 30m under contention; strict failure remains, but legitimate progress gets headroom
    _deferred_collection_failures.append('interviews: MS NOW')
if not step('interviews: Washington Journal (21d)',[
    'refresh_cspan_wj.py','--since',str(today-datetime.timedelta(days=21)),
    '--until',str(yesterday),
],timeout=5400,hard=False):
    _deferred_collection_failures.append('interviews: Washington Journal (21d)')
# Revision-bound biography text is a display-only source, but a partial full-roster fetch would
# silently blank member desks. Defer the hard publication refusal until the independent press run
# has had the same chance to finish and promote as every other collection prerequisite.
if not step('Wikipedia biographies (full roster)',[
    'wikipedia_biographies.py','--strict',
],timeout=1800,hard=False):
    _deferred_collection_failures.append('Wikipedia biographies (full roster)')
# The press refresh uses a new dated checkpoint root each week.  It is a hard publication gate:
# silently falling back to a prior week's sealed run would claim a synchronized refresh while
# actually republishing stale press evidence.
# A fresh full-universe press walk legitimately exceeds four hours when the remaining
# checkpoint groups contain slow or archival URLs.  The collector is resumable and writes
# per-URL checkpoints inside each member group, but the measured full-text pass was still
# producing valid checkpoints at the twelve-hour boundary.  Keep a bounded fail-closed
# deadline (rather than making the network leg unbounded), with enough headroom for one
# complete healthy pass instead of repeatedly replaying partially completed groups.
step('press delta refresh',['refresh_press_stream.py'],timeout=129600,hard=True,partial=_press_partial_excused)
step('press intake audit',['audit_collection.py','--scope','press','--output','data/engine/press_collection_intake_receipt.json'],timeout=1800,hard=True)
step('press engine feed',['build_press_engine_feed.py','--intake-receipt','data/engine/press_collection_intake_receipt.json'],timeout=1800,hard=True)
step('fold promoted press runs',['fold_promoted_press_runs.py'],timeout=1800,hard=True)
# Unrelated collection failures block every shared/full-corpus consumer, but only after the press
# transaction above has completed. This prevents a clogged interview, hearing, floor, or bill pipe
# from stranding independently accepted press evidence in a dated checkpoint.
if _deferred_collection_failures:
    _hard_abort('deferred collection prerequisites',_deferred_collection_failures,
                'failed before full-corpus publication; press acceptance completed independently')
step('collection intake audit',['audit_collection.py','--scope','all','--output','data/engine/collection_intake_receipt.json'],timeout=1800,hard=True)
step('accepted audio intake',['b5_audio_manifest_adapter.py'],timeout=3600,hard=True)
# Only a complete bill repository may update the verified collector-to-ledger
# sidecar.  This stays before every bill-dependent dashboard producer.
step('collector roster verification',[
    'legislation/verify_collector_roster_updates.py',
],timeout=900,hard=True)
# Workbook persistence and dashboard consumption run inside run_pipeline, where
# the sync is ordered immediately after this verification gate and before
# build_archive_legislative.  Keeping a single writer avoids double-importing
# the 30-sheet workbook during the weekly transaction.
# orphaned producers (audit 2026-08-25): nothing ran these CSV regenerators, so they must live here — after the DB-updating collectors above, before run_pipeline's stance stacks below that consume them.
step('CU hearings extract',['stance/extract_cu_hearings.py'],timeout=600)
step('CU interviews extract',['stance/extract_cu_interviews.py'],timeout=600)
step('CU bluesky extract',['stance/extract_cu_bluesky.py'],timeout=600)
step('Big Five traits',['big5_traits.py'],timeout=1800)
step('interaction features',['interaction_features.py'],timeout=900)      # conversational dynamics (feeds dominance disposition)
step('behavior panel',['behavior_panel.py'],timeout=900)                  # nomological fix 2026-07-04: dispositions measured from behavior
step('behavioral dispositions',['behavioral_dispositions.py'],timeout=900)  # must precede receptivity (run_pipeline chain)
step('Big5 v2 (behavior-calibrated)',['big5_lexcal_v2.py'],timeout=900)     # text instrument, honest held-out; desk radar source
step('Big5 v3 (hybrid)',['big5_hybrid.py'],timeout=900)                     # lexical+interaction+persona; wins expected post-nickfix corpus
step('social interaction layer',['social_interaction_layer.py'],timeout=900)
# C-2 (audit 2026-07-08): refresh the press-derived engagement + HCoI edge layers BEFORE build_multilayer
# folds them into the weekly AUG network. Previously neither producer was in the weekly chain, so a frozen
# (days-stale) press_engagement.json / press_hcoi.json got re-baked into every otherwise-fresh AUG, silently
# mixing provenance. Both read the external R tone CSV (Draft Systems\cu_press_stance_tone.csv), so they are
# order-independent of the other Python steps; step() is tolerant, so a missing/locked CSV just leaves the
# prior layer in place (same graceful degradation as before, now refreshed when the CSV is present).
step('press engagement layer',['press_engagement_layer.py'],timeout=600)
step('press HCoI layer',['press_hcoi.py'],timeout=600)
step('multilayer network',['build_multilayer_network.py'],timeout=900)
step('co-appearance layer',['co_appearance_layer.py'],timeout=600)                 # joint interview-episode pairings (user 2026-07-05)
# New document windows are classified before the network-free pipeline rebuild.
# Successful classifications checkpoint individually; provider failure blocks publication.
step('reference layer (classify new evidence)',['member_reference_layer.py','--score','--go'],timeout=14400,hard=True)
step('press stance stack',['press_stance_stack.py'],timeout=1800)   # re-score the (grown) corpus so the sidecar never goes stale (frozen-archive lesson); new articles missing Fable cache fields degrade gracefully to reader+DEBATE features
step('FULL PIPELINE + dashboards',['run_pipeline.py'],timeout=7200,hard=True,gate=_audit_failure_gate)  # raised 3000->7200 2026-08-25: the 48-step chain measured ~3000s under load and was killed 90s from done; 7200 keeps hung-chain protection without the finish-line kill
# Tolerated display-only chain failures (2026-09-05): run_pipeline continues past a build_descriptive_speech
# contract refusal (exit 2) with the last-good artifact and records it in this receipt; without this block
# a tolerated run is indistinguishable from a clean one to every unattended reader. Best-effort: a report,
# never a gate (audit_full's chain_tolerated_failures check is the ageing tripwire).
try:
    _ctf_path=os.path.join(MB_ROOT,'data/engine/chain_tolerated_failures.json')
    _ctf_steps=[]
    if os.path.exists(_ctf_path):
        with open(_ctf_path,encoding='utf-8') as _ctf_fh: _ctf_entries=json.load(_ctf_fh)
        if isinstance(_ctf_entries,list):
            _ctf_steps=sorted({str(e.get('step')) for e in _ctf_entries if isinstance(e,dict) and e.get('step')})
    if _ctf_steps:
        log('tolerated display-only step failure(s) this run: %s'%', '.join(_ctf_steps))
    _write_status(tolerated_display_steps=_ctf_steps)
except Exception as _ctf_exc:
    try: log('tolerated-failure receipt unreadable: %s'%str(_ctf_exc)[:120])
    except BaseException: pass
step('prepare dashboard deployment',['surfaces/publish_dashboard.py'],timeout=1800,hard=True)
# publish phase, office engine (compute_engine.office_view): a LibreOffice view copy of the workbook, made after the chain and
# never gating it. Engine 'windows' (the default) skips it and logs nothing: this refresh has never recalculated the workbook.
_view_status,_view_note=compute_engine.office_view()
if _view_status!='skipped':
    log('%-28s %s  %s'%('workbook view (LibreOffice)','OK' if _view_status=='ok' else '!!',_view_note))
    _emit('refresh.step','completed' if _view_status=='ok' else 'failed','workbook view (LibreOffice): %s'%_view_note,stage='workbook view')
try:
    new=json.load(open(os.path.join(MB_ROOT,'ranks_python.json')))['rank']
    if prev:
        moved=sum(1 for m in new if prev.get(m)!=new[m])
        _rank_moves={'moved':moved,'members':len(new)}
        log('rank movement this week: %d/%d members'%(moved,len(new)))
        _write_status(rank_moves=_rank_moves)
except Exception as e: log('rank compare skipped: %s'%e)
# post-chain DIAGNOSTIC layers (run-4k/4l lit-audit augmentations): signed-coalition splinter,
# Battaglini-Patacchini KB benchmark, Volden-Wiseman LEP criterion. Tolerant — the chain consumes
# none of them and a diagnostic failure never blocks the refresh; they feed the desk External row.
step('signed coalitions (diag)',['signed_coalitions.py'],timeout=900)
step('KB benchmark (diag)',['kb_benchmark.py'],timeout=900)
step('LEP criterion (diag)',['lep_criterion.py'],timeout=900)
step('say-do gap (diag)',['say_do_gap.py'],timeout=900)   # 2026-07-07: feeds delivery_mult (engine talker discount); runs AFTER desks so it reads the fresh bundle
step('financial_other discovery (review sheet)',['financial_other_discovery.py'],timeout=1800)  # 2026-08-05 Paul: sweep-native finance filers outside every roster -> spend-ranked candidates.csv for HIS review; discovery only, never auto-included
step('forecast ledger (freeze+score)',['forecast_ledger.py'],timeout=900)  # 2026-08-06, A1 lane: graded-forecast spine; freeze is data-vintage-keyed and idempotent
# Audio/narrative safety deployment (2026-07-13): run_pipeline validates the guarded audio
# descriptor and rebuilds the construct-honest profile basis before the desk. The accepted-clip
# adapter above updates a candidate review queue only; it never writes active audio. The old unattended fetch ->
# single-model verification -> voiceprint -> paid narrative chain is retired. Audio remains absent
# until two distinct target-speaker reviews satisfy b5_audio_reviews_v2.json. Narrative regeneration
# is manual through b5_narratives_local.py after explicit user approval; it costs $0.
log('===== WEEKLY REFRESH COMPLETE — dashboards regenerated =====')
_refresh_complete=True
_rank_summary=('rank movement %d/%d members'%(_rank_moves['moved'],_rank_moves['members'])
               if _rank_moves else 'rank movement not measured')
_write_status(last_outcome='COMPLETE_WITH_KNOWN_GATED_FAILURES' if _tolerated else 'COMPLETE',
              failures=list(_tolerated),rank_moves=_rank_moves)
_emit('refresh.run','completed',
      'WEEKLY REFRESH COMPLETE — dashboards regenerated; %s%s'%(
          _rank_summary,'; %d known Paul-gated audit item(s) tolerated'%len(_tolerated) if _tolerated else ''),
      stage='weekly_refresh',detail='; '.join(_tolerated)[:2000],
      duration_ms=int((datetime.datetime.now()-_started_at).total_seconds()*1000),
      metadata={'rank_moves':_rank_moves,'failures':list(_tolerated),
                'tolerated':bool(_tolerated)})
