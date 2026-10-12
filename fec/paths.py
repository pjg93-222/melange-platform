# -*- coding: utf-8 -*-
"""Single source of the project root (architecture review 2026-07-02).
Override with the MONEYBALL_ROOT environment variable — every script resolves
its paths from here, so relocating the project (or escaping OneDrive) is a
one-variable change instead of a 95-file edit."""
import os
_HERE=os.path.dirname(os.path.abspath(__file__))
ROOT=os.path.abspath(os.environ.get('MONEYBALL_ROOT',os.path.join(_HERE,os.pardir)))
# 2026-08-04 Phase 2: Root data artifacts are moving into data\<domain>\.
# Call sites currently use relocated literals; accessor consolidation is the
# dedicated next pass. Keep at root: deployed surfaces, the workbook family,
# databases, and ranks_python.json (rank authority stays at root).
DATA = os.path.join(ROOT,'data')
DRAFT=os.path.abspath(os.environ.get('MONEYBALL_DRAFT',os.path.join(ROOT,os.pardir,'Draft Systems')))   # R press-pipeline root
FEC=os.path.join(ROOT,'fec')
WORKBOOK=os.path.join(ROOT,'Moneyball Main Version - Blended.xlsx')
BACKUPS=os.path.join(ROOT,'backups')

# Staffer-network restricted storage is deliberately outside the repository and
# OneDrive tree.  The repository receives only schema-validated, public-safe
# generation artifacts under DATA_STAFFER_NETWORK.  A caller may override the
# machine-local root for tests or a managed encrypted volume.
_LOCAL_APPDATA = os.path.abspath(
    os.environ.get('LOCALAPPDATA', os.path.join(os.path.expanduser('~'), 'AppData', 'Local'))
)
STAFFER_NETWORK_LOCAL = os.path.abspath(
    os.environ.get(
        'STAFFER_NETWORK_LOCAL',
        os.path.join(_LOCAL_APPDATA, 'Melange', 'staffer_network'),
    )
)
STAFFER_NETWORK_RAW = os.path.join(STAFFER_NETWORK_LOCAL, 'raw')
STAFFER_NETWORK_NORMALIZED = os.path.join(STAFFER_NETWORK_LOCAL, 'normalized')
STAFFER_NETWORK_CORPUS = os.path.join(STAFFER_NETWORK_NORMALIZED, 'source-native-corpus')
STAFFER_NETWORK_LEDGER = os.path.join(STAFFER_NETWORK_LOCAL, 'ledger', 'staffer_ledger.db')
STAFFER_NETWORK_ADJUDICATION = os.path.join(STAFFER_NETWORK_LOCAL, 'adjudication')
DATA_STAFFER_NETWORK = os.path.join(DATA, 'staffer_network')

def p(*parts): return os.path.join(ROOT,*parts)
