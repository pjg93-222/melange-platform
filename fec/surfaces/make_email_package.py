#!/usr/bin/env python3
"""Build hash-bound, size-capped email delivery packages from the live HTML surfaces.

Two packages: the FAMILY zip (the eight standalone surfaces, each under its own name) and the
ALL-IN-ONE zip (the combined single-file dashboard).  As of 2026-09-05 the all-in-one is built
from Moneyball_Email.html, the three-surface variant, and stored inside the zip as
Moneyball.html; see MONOLITH_FILES below for why.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import ROOT


FAMILY_FILES = (
    "Moneyball Dashboard.html", "cu-member-desk.html", "lobby-network.html",
    "lobby-interactions.html", "lobby-forecast.html", "lobby-bills.html",
    "lobby-champions.html", "lobby-screen.html",
)
# (source name in the live tree, arcname inside the zip).  The all-in-one attachment is built
# from Moneyball_Email.html -- the THREE-surface variant of the combined dashboard (network +
# member desk + industry), which is exactly what Moneyball.html was before the staffer surface
# landed on 2026-09-04 -- but it is stored under the name recipients have always opened,
# Moneyball.html, so nothing about the received attachment changes.
#
# WHY (2026-09-05).  BUDGET_BYTES is an email ATTACHMENT limit, sized for base64 overhead, so
# it is not this script's to raise.  With the fourth surface merged in, the level-9 zip of the
# four-surface Moneyball.html measured 14,808,817 bytes against the 14,680,064 budget -- 128,753
# over -- and build_zip refused the package, halting the chain before the audit.  The staffer
# panel alone is a ~4.12 MB JSON payload that compresses to ~640 KB, which was the entire
# remaining headroom.  The four-surface document stays the file-open / live deliverable; only
# the emailed copy drops back to three.  See fec/surfaces/make_combined.py, which writes both.
MONOLITH_FILES = (("Moneyball_Email.html", "Moneyball.html"),)
BUDGET_BYTES = 14 * 1024 * 1024


def entries(names):
    """Normalize a package file tuple to (source_name, arcname) pairs.

    FAMILY_FILES are plain names, where the two are the same; MONOLITH_FILES carries an
    explicit arcname so the emailed variant can differ from the file it is zipped from.
    """
    return tuple((item, item) if isinstance(item, str) else tuple(item) for item in names)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".%d.tmp" % os.getpid())
    with open(temporary, "w", encoding="utf-8", newline='\r\n') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def build_zip(destination, root, names):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".%d.tmp" % os.getpid())
    stronger = destination.with_name(destination.name + ".%d.strong.tmp" % os.getpid())
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, arcname in entries(names):
                source = root / name
                if not source.is_file():
                    raise RuntimeError("email package input is missing: %s" % source)
                archive.write(source, arcname=arcname)
        if temporary.stat().st_size > BUDGET_BYTES:
            from surfaces.zip_compression import recompress_zip
            proof = recompress_zip(temporary, stronger, iterations=5)
            print('email ZIP lossless DEFLATE fallback: %s %d -> %d bytes (%d extracted hashes verified)' %
                  (destination.name, proof['before_bytes'], proof['after_bytes'], proof['entries_verified']), flush=True)
            if stronger.stat().st_size < temporary.stat().st_size:
                os.replace(stronger, temporary)
        if temporary.stat().st_size > BUDGET_BYTES:
            raise RuntimeError("email package exceeds 14 MiB: %s (%d bytes)" %
                               (destination.name, temporary.stat().st_size))
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
        if stronger.exists():
            stronger.unlink()


def input_receipt(root, name, arcname=None):
    """Hash-bind one package input.

    `name` is the SOURCE path relative to the live tree, and stays the key the audit resolves:
    fec/audit/audit_full.py re-hashes root/<name> for every entry of `inputs` and
    `monolith_inputs`, so recording the source here is what keeps the manifest bound to the
    file that was actually zipped.  `arcname` records the name it carries inside the zip, which
    differs for the monolith (Moneyball_Email.html is stored as Moneyball.html).
    """
    path = root / name
    stat = path.stat()
    return {"name": name, "arcname": arcname or name, "bytes": stat.st_size,
            "sha256": sha256_file(path),
            "mtime_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()}


class _CapturedPages:
    """Read one captured page at a time for the existing honors validator."""
    def __init__(self, root):
        self.root = root

    def __getitem__(self, name):
        return (self.root / name).read_text(encoding="utf-8")


def _capture_input(root, snapshot, name, arcname):
    digest = hashlib.sha256()
    size = 0
    target = snapshot / name
    with (root / name).open("rb") as source, target.open("xb") as output:
        before = os.fstat(source.fileno())
        for block in iter(lambda: source.read(1024 * 1024), b""):
            output.write(block)
            digest.update(block)
            size += len(block)
        after = os.fstat(source.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or size != before.st_size:
        raise RuntimeError("email package input changed during capture: " + name)
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    return {"name": name, "arcname": arcname, "bytes": size, "sha256": digest.hexdigest(),
            "mtime_utc": datetime.fromtimestamp(before.st_mtime, timezone.utc).isoformat()}


def _verify_captured_zip(path, records):
    with zipfile.ZipFile(path) as archive:
        if archive.namelist() != [item["arcname"] for item in records]:
            raise RuntimeError("email package member list differs from captured inputs")
        for item in records:
            digest = hashlib.sha256()
            size = 0
            with archive.open(item["arcname"]) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
                    size += len(block)
            if size != item["bytes"] or digest.hexdigest() != item["sha256"]:
                raise RuntimeError("email package member differs from captured input: " + item["name"])


def build(root=Path(ROOT), out_dir=None):
    root = Path(root).resolve()
    pairs = entries(FAMILY_FILES) + entries(MONOLITH_FILES)
    for source, _arcname in pairs:
        if not (root / source).is_file():
            raise RuntimeError('email package input is missing: ' + str(root / source))
    from surfaces.honors_publication import validate_pages
    checked_pages = ('Moneyball Dashboard.html', 'cu-member-desk.html', 'Moneyball_Email.html')
    out_dir = Path(out_dir).resolve() if out_dir else root / "outputs" / "email_package"
    out_dir.mkdir(parents=True, exist_ok=True)
    family_zip = out_dir / "Moneyball_Email_Package.zip"
    monolith_zip = out_dir / "Moneyball_Email_Package_AllInOne.zip"
    alias = root / "data" / "money" / "Moneyball_email.zip"
    receipt_candidates = (
        "data/engine/collection_intake_receipt.json",
        "data/stance/press_refresh_feed_receipt.json",
        "data/stance/press_component_meta.json",
        "data/engine/weekly_refresh_status.json",
    )

    def lineage_hashes():
        return {name: sha256_file(root / name)
                for name in receipt_candidates if (root / name).is_file()}

    # Final delivery paths remain untouched until all captured content, current
    # source checks, ZIP member checks and the unchanged size gate have passed.
    with tempfile.TemporaryDirectory(prefix=".email-package-", dir=out_dir) as scratch:
        staging = Path(scratch)
        snapshot = staging / "inputs"
        snapshot.mkdir()
        captured = [_capture_input(root, snapshot, name, arcname) for name, arcname in pairs]
        family_inputs = captured[:len(FAMILY_FILES)]
        monolith_inputs = captured[len(FAMILY_FILES):]
        source_receipts = lineage_hashes()
        pages = _CapturedPages(snapshot)
        honors_generation = validate_pages(root, checked_pages, captured=pages)
        staged_family = staging / family_zip.name
        staged_monolith = staging / monolith_zip.name
        build_zip(staged_family, snapshot, FAMILY_FILES)
        build_zip(staged_monolith, snapshot, MONOLITH_FILES)
        _verify_captured_zip(staged_family, family_inputs)
        _verify_captured_zip(staged_monolith, monolith_inputs)
        if validate_pages(root, checked_pages, captured=pages) != honors_generation:
            raise RuntimeError("email package recognition generation changed during build")
        for item in captured:
            source = root / item["name"]
            if not source.is_file() or source.stat().st_size != item["bytes"] or sha256_file(source) != item["sha256"]:
                raise RuntimeError("email package input changed during build: " + item["name"])
        if lineage_hashes() != source_receipts:
            raise RuntimeError("email package source receipts changed during build")
        family_record = {"bytes": staged_family.stat().st_size, "sha256": sha256_file(staged_family),
                         "files": list(FAMILY_FILES)}
        monolith_record = {"bytes": staged_monolith.stat().st_size, "sha256": sha256_file(staged_monolith),
                           "files": [arcname for _name, arcname in entries(MONOLITH_FILES)]}
        manifest = {
            "schema": "email-package-v2", "status": "CURRENT_HASH_BOUND",
            "honors_generation_id": honors_generation,
            "generated_at": datetime.now(timezone.utc).isoformat(), "budget_bytes": BUDGET_BYTES,
            "inputs": family_inputs, "monolith_inputs": monolith_inputs,
            "source_receipts_sha256": source_receipts,
            "packages": {
                "outputs/email_package/Moneyball_Email_Package.zip": family_record,
                "outputs/email_package/Moneyball_Email_Package_AllInOne.zip": monolith_record,
                "data/money/Moneyball_email.zip": dict(monolith_record),
            },
        }
        staged_manifest = staging / "EMAIL_PACKAGE_MANIFEST.json"
        atomic_json(staged_manifest, manifest)
        # Stage the cross-directory alias before touching any existing delivery.
        alias.parent.mkdir(parents=True, exist_ok=True)
        alias_tmp = alias.with_name(alias.name + ".%d.tmp" % os.getpid())
        try:
            shutil.copyfile(staged_monolith, alias_tmp)
            os.replace(staged_family, family_zip)
            os.replace(staged_monolith, monolith_zip)
            os.replace(alias_tmp, alias)
            os.replace(staged_manifest, out_dir / "EMAIL_PACKAGE_MANIFEST.json")
        finally:
            if alias_tmp.exists():
                alias_tmp.unlink()
        # Each replacement is atomic; the family is not a multi-file transaction.
        # A publication interruption must be detected by the existing manifest audit.
        return manifest


def main():
    manifest = build()
    print("email packages: %d family surface(s), %d all-in-one surface(s), all <=14 MiB" %
          (len(manifest["inputs"]), len(manifest["monolith_inputs"])))
    for item in manifest["monolith_inputs"]:
        print("  all-in-one: %s (%d B) zipped as %s"
              % (item["name"], item["bytes"], item["arcname"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
