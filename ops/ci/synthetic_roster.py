"""CI only: plant a synthetic data/engine/roster.json when the checkout has none.

The source-only export carries no data/, and fec/roster.py raises without its artifact (by design: nothing falls back to a
remembered 533). fec/legislation modules that the no-data tests import read the roster at import time for their row bounds,
so release.yml and compute-office.yml run this first, the way release.yml writes the mentat/inputs placeholder.
No name here is a real member, and an existing roster.json is never touched."""
import json
import sys
from pathlib import Path

ROSTER = Path(__file__).resolve().parents[2] / "data" / "engine" / "roster.json"

if __name__ == "__main__":
    if ROSTER.exists():
        sys.exit(0)
    members = [{"member": f"Synthetic Member {i:03d}", "bioguide": f"Z{i:06d}", "chamber": "Senate" if i <= 100 else "House"}
               for i in range(1, 536)]
    ROSTER.parent.mkdir(parents=True, exist_ok=True)
    ROSTER.write_text(json.dumps({"schema": "melange-roster-v1", "synthetic": True, "members": members, "departed": []}),
                      encoding="utf-8")
