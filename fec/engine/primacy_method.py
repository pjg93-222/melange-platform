"""User-ratified 2026-09-20 diagnostic promotion and exact legacy escape hatch."""
import os

METHOD = os.environ.get('PRIMACY_METHOD', 'diagnostic_v1')
if METHOD not in ('diagnostic_v1', 'legacy'):
    raise ValueError('PRIMACY_METHOD must be diagnostic_v1 or legacy')
DIAGNOSTIC = METHOD == 'diagnostic_v1'
BENEFICIARY_W = {
    'cu_primary': 1.0, 'shared': 0.5, 'bank_primary': 0.15,
    'crypto_primary': 0.15, 'fintech': 0.15, 'telecom_primary': 0.15, 'offtopic': 0.0,
    'oppose': 1.0,  # Magnitude only: the routed act already carries its sign.
}
