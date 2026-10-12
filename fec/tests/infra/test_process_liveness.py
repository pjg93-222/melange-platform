from pathlib import Path
import sys,subprocess,os
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from process_liveness import process_alive
from pipeline_lock import _pid_alive


def test_probe_does_not_signal_or_terminate_child():
    child=subprocess.Popen([sys.executable,'-c','import sys;sys.stdin.read()'],stdin=subprocess.PIPE)
    try:
        assert process_alive(child.pid)
        assert _pid_alive(child.pid)
        assert child.poll() is None
        child.communicate(input=b'',timeout=10)
        assert child.returncode==0
        assert not process_alive(child.pid)
    finally:
        if child.poll() is None:
            child.communicate(input=b'',timeout=10)


def test_invalid_pids_never_target_a_process_group():
    for value in (None,'bad',0,-1): assert not process_alive(value)
    assert process_alive(os.getpid())
