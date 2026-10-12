from __future__ import annotations

from pathlib import Path
import subprocess
import sys

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from collect.refresh_press_stream import find_rscript


def test_official_wordpress_custom_post_adapter_against_private_r_fixture():
    result = subprocess.run(
        [find_rscript(), str(Path(__file__).with_suffix(".R")),
         str(FEC / "collect/press_official_coverage.R"),
         str(FEC / "collect/press_wordpress_custom_posts.R")],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout
