from pathlib import Path
import subprocess
import sys

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from collect.refresh_press_stream import ROOT, find_rscript


def test_network_deadline_and_resume_with_private_upstream_fixtures():
    result = subprocess.run([
        find_rscript(), str(Path(__file__).with_suffix('.R')),
        str(FEC / 'collect/press_request_deadlines.R'),
        str(Path(ROOT).parent / 'Draft Systems/cu_press_release_scraper.R'),
    ], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS:' in result.stdout
