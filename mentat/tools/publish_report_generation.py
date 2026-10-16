"""Publish an Overview/Data generation through the shared owned commit protocol."""
import argparse
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'app'))
from report_publication import publish
from report_directory import report_directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--overview', type=Path, required=True)
    parser.add_argument('--data', type=Path)
    parser.add_argument('--audit-directory', type=Path)
    parser.add_argument('--wait-seconds', type=float, default=30)
    args = parser.parse_args()
    print(json.dumps(publish(args.overview, args.data, report_directory(PROJECT), args.audit_directory,
                             wait_seconds=min(60, max(0, args.wait_seconds)))))


if __name__ == '__main__':
    main()
