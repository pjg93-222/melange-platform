# -*- coding: utf-8 -*-
"""Round-8 thin runner — text-N vs behavioral reactivity, SUCCESSOR to round 4 at MEM_N>=250
(prereg big5_n2_prereg.md; round-4 pin: 'successor prereg only at reactivity coverage MEM_N>=250,
same five-bar structure, fresh fingerprints'). ZERO re-implementation: every model, bar, seed,
gate, battery sim, demotion rule, noiv ship-gate, and the non-finite rule execute inside
fec/members/b5_n_matrix.py; this runner only re-points the prereg file and the fresh fingerprint pins.
The RESULT heading check ('\\n## RESULT-N') matches the '## RESULT-N2' heading this round appends,
so the one-look gate arms identically. LOCK-TIME: fill PIN below from the lock commit, replacing
every None; the look REFUSES (not consumed) while any None remains."""
import os as _o, sys as _s
_s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import members.b5_n_matrix as NM

NM.PREREG = _o.path.join(_o.path.dirname(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))), 'big5_n2_prereg.md')

# LOCK-TIME PINS (filled at lock; None = unlockable, the gate below refuses)
PIN2 = {
    'MEM': 470, 'MEM_N': 263,
    'bd_sha': '57ca1e2b1731cbeed4411645c2b527a8db0136362c2d3d4654dda8faaa852e03',
    'ix_sha': 'b79632d88d183c3e88093378aa427d416f540579b4d4bf0c6185cfe6f1328d94',
    'vv_sha': '0bf7c461e9eca440535484bf325c5b90d423daf0573e984a1b8691a242194751',
    'bp_sha': '491761ea130f8458ae0d09d15be20619b31011ab14e56e6a05bfbdf3d703a4cb',
    'speeches': 268173, 'posts': 29098,
}

if __name__ == '__main__':
    if any(v is None for v in PIN2.values()):
        import argparse
        ap = argparse.ArgumentParser(); ap.add_argument('--simulate', action='store_true')
        ap.add_argument('--look', action='store_true'); a = ap.parse_args()
        if a.look:
            print('!! LOOK REFUSED (not consumed): PIN2 lock-time fields unfilled (prereg still DRAFT)')
            raise SystemExit(2)
        # simulate/status may run against the draft (diagnostic phase); pins stay round-4 for status text
    else:
        NM.PIN = PIN2
        # LOCK ADDENDUM 1: apply the already-locked five-seed >=3/5 successor gate inside the battery;
        # round-4's single-seed default remains unchanged in b5_n_matrix.py.
        NM.PLANT_SEEDS = [424242, 515151, 616161, 717171, 818181]
        NM.PLANT_MIN_PASS = 3
    NM.main()
