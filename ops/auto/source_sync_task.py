"""Hidden pythonw task entry point; record source synchronization output locally."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import os
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ops import sync_sources


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--push', action='store_true')
    args = parser.parse_args(argv)
    root = Path(os.environ['LOCALAPPDATA']) / 'Melange' / 'source-sync'
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'scheduled.log').open('a', encoding='utf-8') as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            print('\n[' + dt.datetime.now(dt.timezone.utc).isoformat() + '] Source synchronization')
            try:
                return sync_sources.main(['--apply'] + (['--push'] if args.push else []))
            except Exception:
                traceback.print_exc()
                return 1


if __name__ == '__main__':
    raise SystemExit(main())
