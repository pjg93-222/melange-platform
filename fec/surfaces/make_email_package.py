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


def build(root=Path(ROOT), out_dir=None):
    root = Path(root).resolve()
    # Preserve the explicit missing-input contract before the generation gate
    # opens source pages, and fail before creating either delivery package.
    for source, _arcname in entries(FAMILY_FILES) + entries(MONOLITH_FILES):
        if not (root / source).is_file():
            raise RuntimeError('email package input is missing: ' + str(root / source))
    from surfaces.honors_publication import validate_pages
    honors_generation = validate_pages(root, ('Moneyball Dashboard.html', 'cu-member-desk.html', 'Moneyball_Email.html'))
    out_dir = Path(out_dir).resolve() if out_dir else root / "outputs" / "email_package"
    family_zip = out_dir / "Moneyball_Email_Package.zip"
    monolith_zip = out_dir / "Moneyball_Email_Package_AllInOne.zip"
    build_zip(family_zip, root, FAMILY_FILES)
    build_zip(monolith_zip, root, MONOLITH_FILES)

    # Keep the established data/money delivery alias synchronized with the all-in-one package.
    alias = root / "data" / "money" / "Moneyball_email.zip"
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias_tmp = alias.with_name(alias.name + ".%d.tmp" % os.getpid())
    shutil.copyfile(monolith_zip, alias_tmp)
    os.replace(alias_tmp, alias)

    receipt_candidates = (
        "data/engine/collection_intake_receipt.json",
        "data/stance/press_refresh_feed_receipt.json",
        "data/stance/press_component_meta.json",
        "data/engine/weekly_refresh_status.json",
    )
    manifest = {
        "schema": "email-package-v2", "status": "CURRENT_HASH_BOUND",
        "honors_generation_id": honors_generation,
        "generated_at": datetime.now(timezone.utc).isoformat(), "budget_bytes": BUDGET_BYTES,
        "inputs": [input_receipt(root, name) for name in FAMILY_FILES],
        "monolith_inputs": [input_receipt(root, name, arcname)
                            for name, arcname in entries(MONOLITH_FILES)],
        "source_receipts_sha256": {
            name.replace("\\", "/"): sha256_file(root / name)
            for name in receipt_candidates if (root / name).is_file()
        },
        "packages": {
            "outputs/email_package/Moneyball_Email_Package.zip": {
                "bytes": family_zip.stat().st_size, "sha256": sha256_file(family_zip),
                "files": list(FAMILY_FILES)},
            # `files` names what a recipient finds INSIDE the zip, so it is the arcname list;
            # the source each one was built from is in monolith_inputs above.
            "outputs/email_package/Moneyball_Email_Package_AllInOne.zip": {
                "bytes": monolith_zip.stat().st_size, "sha256": sha256_file(monolith_zip),
                "files": [arcname for _name, arcname in entries(MONOLITH_FILES)]},
            "data/money/Moneyball_email.zip": {
                "bytes": alias.stat().st_size, "sha256": sha256_file(alias),
                "files": [arcname for _name, arcname in entries(MONOLITH_FILES)]},
        },
    }
    manifest_path = out_dir / "EMAIL_PACKAGE_MANIFEST.json"
    atomic_json(manifest_path, manifest)
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
