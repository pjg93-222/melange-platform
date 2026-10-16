"""Bind desktop workspace evidence to data, without redistributing or inventing it."""
from importlib import import_module
from pathlib import Path


WORKSPACE_FILES = {
    'staffer_identity_resolution': {'USER_ASSERTIONS': 'staffer_user_assertions.json'},
    'staffer_person_inventory': {
        'USER_ASSERTIONS': 'staffer_user_assertions.json',
        'PAYROLL_EXCEPTIONS': 'staffer_payroll_inventory_exceptions.json',
    },
    'staffer_routing_evidence': {'NAMED_ARTIFACT': 'staffer_named_filing_evidence.json'},
    'staffer_identity_web': {'SEEDS': 'staffer_identity_reference_seeds.json'},
    'staffer_payroll_identity': {'CONFIG': 'staffer_payroll_identity.json'},
}


def configure_melange_workspace(data_root):
    """Resolve only reviewed data files before LiveService imports its readers.

    Missing evidence remains missing and its existing loader decides whether it
    is optional. Receipt hashes and source provenance checks remain unchanged.
    Validate every path before changing any reader; do not create or copy files.
    """
    root = Path(data_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Melange data directory must be a directory')
    bindings = []
    for name, attributes in WORKSPACE_FILES.items():
        for attribute, filename in attributes.items():
            target = (root / 'fec' / 'surfaces' / filename).resolve()
            if not target.is_relative_to(root):
                raise ValueError('Melange evidence must remain inside the selected data directory')
            if target.exists() and not target.is_file():
                raise ValueError('Melange evidence must be a regular data file')
            bindings.append((name, attribute, target))
    modules = {name: import_module('fec.surfaces.' + name) for name in WORKSPACE_FILES}
    for name, attribute, target in bindings:
        setattr(modules[name], attribute, target)
    return {name + '.' + attribute: str(target) for name, attribute, target in bindings}
