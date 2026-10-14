"""Find a source-repo path in the tree the tests run on.

On the laptop the tests run on the source repo, where the workflows are ops/github/workflows/*.yml. CI runs them on the
source-only export, where release_lib.export.remap has moved ops/github/ to .github/ (and ops/export.gitattributes to
.gitattributes). A test names a file by its source path and gets it back in either layout.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ops.release_lib.export import remap  # noqa: E402


def src(rel: str) -> Path:
    """`rel` is the source-repo path (ops/github/workflows/x.yml). Returned as is when it exists, else where the export puts it."""
    path = ROOT / rel
    return path if path.exists() else ROOT / remap(rel)
