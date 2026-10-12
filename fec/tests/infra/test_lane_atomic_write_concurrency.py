# -*- coding: utf-8 -*-
"""Atomic writes must be unique per process, durable, and lock-tolerant.

Nine producers staged their output through a *fixed* scratch name --
``<name>.tmp`` or ``.<name>.tmp``, on a receiver called either ``path`` or
``destination`` -- with no process identity and no ``fsync``.  Six were body
writers; the last three staged a *copy* (``shutil.copyfile`` into the scratch
file, hash-verify, ``os.replace``), which is why they survived the first pass.
This repo demonstrably runs producers concurrently,
and it lives inside OneDrive, which syncs a stable ``*.tmp`` name and mints
conflict copies from it.  Two producers writing the same output therefore
clobbered each other's staging file and one of them published the other's
bytes.

``test_interleaved_writers_do_not_clobber`` reproduces that deterministically:
writer A stages its body, its publish is deferred, writer B stages and
publishes, then A publishes.  With a fixed scratch name B overwrites A's
staging file (or consumes it outright), so A's publish either fails or ships
B's bytes.  With a unique scratch name each writer publishes its own bytes.

Run with::

    python -m pytest fec/tests/infra/test_lane_atomic_write_concurrency.py -q

Set ``XCUT_FEC_ROOT`` to point the imports at a different ``fec`` directory;
that is how the pre-fix failure was demonstrated.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest


FEC = Path(os.environ.get("XCUT_FEC_ROOT") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(FEC))


# (module, atomic-write callable name, payload type).  The last two are extra
# sites found while migrating: they staged through ``.<name>.tmp``, hidden but
# equally fixed.
ATOMIC_WRITERS = (
    ("audit.audit_billstatus_enrichment_completeness", "atomic_write", bytes),
    ("audit.audit_billstatus_snapshot_divergence", "atomic_write", bytes),
    ("legislation.build_billstatus_relevance_sidecar", "atomic_write", bytes),
    ("legislation.run_billstatus_bulk_enrichment", "atomic_write", bytes),
    ("legislation.compute_legislation_policy_scenarios", "write_atomic", str),
    ("legislation.build_accepted_legislation_generation", "write_text_atomic", str),
    # Found by a later source scan.  These three staged through a fixed name
    # inside a copy helper (``shutil.copyfile`` into the scratch file, verify,
    # ``os.replace``) rather than inside a body writer, which is why the first
    # migration pass missed them.
    ("legislation.run_legislation_network_dependents", "atomic_write", bytes),
    ("legislation.run_legislation_pillars_and_ranks", "atomic_write", bytes),
    ("legislation.run_legislation_say_do_first_pass", "atomic_write", bytes),
)

# A fixed scratch name is a defect whatever the receiver is called.  The
# original literal patterns only matched a receiver named ``path``, so the
# three copy helpers above -- which staged through ``destination.name`` --
# passed this check while still carrying the defect.  Match any identifier.
FIXED_TMP_PATTERN = re.compile(
    r"with_name\(\s*(?:"
    r'\w+\.name\s*\+\s*"\.tmp"'  # dst.with_name(dst.name + ".tmp")
    r'|f"\.\{\w+\.name\}\.tmp"'  # dst.with_name(f".{dst.name}.tmp")
    r")\s*\)"
)


def executable_code(source: str) -> str:
    """Return ``source`` with comments and docstrings blanked out.

    Both the fix and its rationale are documented *in prose that quotes the
    removed pattern*, so a scan of the raw text reports the documentation as a
    live offender.  Stripping ``#`` lines alone is not enough: ``lane_file_io``
    explains the defect in its module docstring.  String literals that are not
    docstrings are kept, because the pattern being matched contains ``".tmp"``
    and discarding every string would disarm the check entirely.
    """

    doc_lines: set[int] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        if ast.get_docstring(node, clean=False) is None:
            continue
        first = node.body[0]
        doc_lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return "\n".join(
        ""
        if number in doc_lines or line.lstrip().startswith("#")
        else line
        for number, line in enumerate(source.splitlines(), start=1)
    )


def _writer(module_name: str, attribute: str, payload_type):
    module = __import__(module_name, fromlist=["*"])
    call = getattr(module, attribute)

    def write(path: Path, body: bytes) -> None:
        call(path, body.decode("utf-8") if payload_type is str else body)

    return write


class _ReplaceSpy:
    """Record every ``os.replace`` and optionally defer it."""

    def __init__(self, defer: bool = False):
        self.defer = defer
        self.calls: list[tuple[str, str]] = []
        self._real = os.replace

    def __enter__(self) -> "_ReplaceSpy":
        spy = self

        def replace(src, dst, *args, **kwargs):
            spy.calls.append((str(src), str(dst)))
            if spy.defer:
                return None
            return spy._real(src, dst, *args, **kwargs)

        os.replace = replace
        return self

    def __exit__(self, *exc) -> bool:
        os.replace = self._real
        return False

    def publish_deferred(self) -> None:
        for src, dst in self.calls:
            self._real(src, dst)


class ScratchNameIsUniquePerProcess(unittest.TestCase):
    def test_scratch_name_carries_the_pid_and_never_repeats(self) -> None:
        for module_name, attribute, payload_type in ATOMIC_WRITERS:
            write = _writer(module_name, attribute, payload_type)
            with tempfile.TemporaryDirectory(prefix="xcut-atomic-") as raw:
                target = Path(raw) / "out.ndjson"
                with _ReplaceSpy() as spy:
                    write(target, b'{"a":1}\n')
                    write(target, b'{"a":2}\n')
                scratches = [src for src, _dst in spy.calls]
                self.assertEqual(len(scratches), 2, module_name)
                self.assertEqual(
                    len(set(scratches)),
                    2,
                    f"{module_name}.{attribute} reuses one scratch name: {scratches}",
                )
                for scratch in scratches:
                    self.assertIn(
                        str(os.getpid()),
                        Path(scratch).name,
                        f"{module_name}.{attribute} scratch name lacks the PID",
                    )
                self.assertEqual(target.read_bytes(), b'{"a":2}\n', module_name)

    def test_no_producer_stages_through_a_fixed_tmp_name(self) -> None:
        offenders = []
        for module_name, _attribute, _payload in ATOMIC_WRITERS:
            source = (FEC / Path(*module_name.split("."))).with_suffix(".py").read_text(
                encoding="utf-8-sig"
            )
            # Comments and docstrings quote the removed pattern on purpose;
            # only executable lines count.
            code = executable_code(source)
            for match in FIXED_TMP_PATTERN.finditer(code):
                offenders.append(f"{module_name}: {match.group(0)}")
        self.assertEqual(offenders, [], f"fixed scratch names remain: {offenders}")


class InterleavedWritersDoNotClobber(unittest.TestCase):
    def test_interleaved_writers_do_not_clobber(self) -> None:
        body_a = b'{"writer":"A"}\n' * 64
        body_b = b'{"writer":"B"}\n'
        for module_name, attribute, payload_type in ATOMIC_WRITERS:
            write = _writer(module_name, attribute, payload_type)
            with tempfile.TemporaryDirectory(prefix="xcut-race-") as raw:
                target = Path(raw) / "shared_output.ndjson"
                # Writer A stages; its publish is deferred.
                with _ReplaceSpy(defer=True) as deferred_a:
                    write(target, body_a)
                # Writer B stages and publishes normally.
                write(target, body_b)
                self.assertEqual(target.read_bytes(), body_b, module_name)
                # Now A publishes.  Its staged bytes must still be its own.
                deferred_a.publish_deferred()
                self.assertEqual(
                    target.read_bytes(),
                    body_a,
                    f"{module_name}.{attribute}: writer B clobbered writer A's "
                    f"staging file",
                )


class DurableAndLockTolerant(unittest.TestCase):
    def _lane_io(self):
        return __import__("lane_file_io")

    def test_publish_retries_through_permission_error(self) -> None:
        lane_io = self._lane_io()
        real_replace = os.replace
        state = {"attempts": 0}

        def flaky(src, dst, *args, **kwargs):
            state["attempts"] += 1
            if state["attempts"] < 3:
                raise PermissionError("OneDrive holds the destination")
            return real_replace(src, dst, *args, **kwargs)

        with tempfile.TemporaryDirectory(prefix="xcut-lock-") as raw:
            target = Path(raw) / "locked.json"
            os.replace = flaky
            try:
                lane_io.atomic_write_bytes(target, b"{}\n")
            finally:
                os.replace = real_replace
        self.assertEqual(state["attempts"], 3)

    def test_failed_write_leaves_no_orphan_scratch(self) -> None:
        lane_io = self._lane_io()
        real_replace = os.replace

        def always_fails(src, dst, *args, **kwargs):
            raise OSError("publish refused")

        with tempfile.TemporaryDirectory(prefix="xcut-orphan-") as raw:
            directory = Path(raw)
            os.replace = always_fails
            try:
                with self.assertRaises(OSError):
                    lane_io.atomic_write_bytes(directory / "out.json", b"{}\n")
            finally:
                os.replace = real_replace
            self.assertEqual(sorted(p.name for p in directory.iterdir()), [])

    def test_creates_missing_parents_and_publishes_exact_bytes(self) -> None:
        lane_io = self._lane_io()
        with tempfile.TemporaryDirectory(prefix="xcut-parents-") as raw:
            target = Path(raw) / "deep" / "nested" / "out.ndjson"
            body = b'{"a":1}\n{"b":2}\n'
            lane_io.atomic_write_bytes(target, body)
            self.assertEqual(target.read_bytes(), body)

    def test_scratch_name_is_hidden_and_suffixed(self) -> None:
        """Orphans must be recognisable, and must not look like real output."""

        lane_io = self._lane_io()
        with tempfile.TemporaryDirectory(prefix="xcut-name-") as raw:
            target = Path(raw) / "out.ndjson"
            with _ReplaceSpy() as spy:
                lane_io.atomic_write_bytes(target, b"{}\n")
            name = Path(spy.calls[0][0]).name
            self.assertTrue(name.startswith("."), name)
            self.assertTrue(name.endswith(".part"), name)
            self.assertIsNotNone(re.search(rf"\.tmp-{os.getpid()}-", name), name)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
