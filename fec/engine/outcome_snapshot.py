"""Prospective receptivity research entry point; no production imports or implicit look.

Legacy --freeze/--score invocations intentionally refuse without a new committed v2
contract. The 2026-07-04 snapshot is never migrated, overwritten, or scored here.
No argument (or --status) opens no outcome or predictor files. See outcome_contract.py.
"""
import os as _o, sys as _s
_s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from engine.outcome_contract import main

if __name__ == '__main__':
    raise SystemExit(main())
