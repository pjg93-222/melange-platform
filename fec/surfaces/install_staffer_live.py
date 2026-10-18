"""Install only the live view and navigation hooks into an existing checkout."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

try:
    from .staffer_live_integration import inject_navigation
except ImportError:  # direct script execution: no package context
    import os as _bootstrap_os, sys as _bootstrap_sys
    _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
    if _bootstrap_root not in _bootstrap_sys.path:
        _bootstrap_sys.path.insert(0, _bootstrap_root)
    from fec.surfaces.staffer_live_integration import inject_navigation

FILES = (
    "fec/surfaces/staffer_live_server.py",
    "fec/surfaces/staffer_live_service.py",
    "fec/surfaces/melange_dashboard.py",
    "fec/surfaces/staffer_routing.py",
    "fec/surfaces/staffer_work_sources.py",
    "fec/surfaces/staffer_closeness_routes.py",
    "fec/surfaces/staffer_route_cancel.py",
    "fec/surfaces/staffer_distinct_routes.py",
    "fec/surfaces/staffer_overlap_routes.py",
    "fec/surfaces/staffer_office_routes.py",
    "fec/surfaces/staffer_search_index.py",
    "fec/surfaces/staffer_connection_facts.py",
    "fec/surfaces/staffer_network_scope.py",
    "fec/surfaces/staffer_payroll_identity.py",
    "fec/surfaces/staffer_payroll_identity.json",
    "fec/surfaces/staffer_identity_records.py",
    "fec/surfaces/staffer_groups_index.py",
    "fec/surfaces/staffer_contacts.py",
    "fec/staffer_network/closeness_math.py",
    "fec/staffer_network/relationship_uncertainty.py",
    "fec/staffer_network/member_context.py",
    "fec/staffer_network/ranking_math.py",
    "fec/staffer_network/lda_status_labels.py",
    "fec/staffer_network/lda_employment.py",
    "fec/staffer_network/littlesis_contract.py",
    "fec/staffer_network/name_standard.py",
    "fec/staffer_network/source_native_corpus.py",
    "fec/staffer_network/source_manifest.py",
    "fec/staffer_network/schema_validation.py",
    "fec/staffer_network/validate.py",
    "fec/staffer_network/publication.py",
    "fec/surfaces/staffer_ecosystem_routes.py",
    "fec/surfaces/staffer_reconciled_evidence.py",
    "fec/surfaces/staffer_assertions.py",
    "fec/surfaces/staffer_routing_evidence.py",
    "fec/surfaces/staffer_person_inventory.py",
    "fec/surfaces/staffer_network_explorer.py",
    "fec/surfaces/source_capture.py",
    "fec/surfaces/staffer_source_catalog.py",
    "fec/surfaces/staffer_lda_catalog.py",
    "fec/surfaces/staffer_people_graph.py",
    "fec/surfaces/staffer_identity_resolution.py",
    "fec/surfaces/staffer_identity_auto.py",
    "fec/surfaces/staffer_identity_web.py",
    "fec/surfaces/staffer_identity_reference_seeds.json",
    "fec/surfaces/staffer_activity_index.py",
    "fec/surfaces/staffer_payroll_inventory_exceptions.json",
    "fec/surfaces/staffer_user_assertions.json",
    "fec/surfaces/staffer_named_filing_evidence.json",
    "fec/surfaces/staffer_graph_snapshot.py",
    "fec/surfaces/staffer_live_integration.py",
    "fec/surfaces/start_staffer_live.ps1",
    "fec/surfaces/install_staffer_live.py",
    "fec/surfaces/staffer_live/index.html",
    "fec/surfaces/staffer_live/staffer-live.css",
    "fec/surfaces/staffer_live/staffer-live.js",
    "fec/surfaces/staffer_live/staffer-route.js",
    "fec/surfaces/staffer_live/staffer-assertions.js",
    "fec/surfaces/staffer_live/staffer-explorer.js",
    "fec/surfaces/staffer_live/staffer-inventory-worker.js",
    "fec/surfaces/staffer_live/staffer-groups.js",
    "fec/surfaces/staffer_live/staffer-person-facts.js",
    "fec/surfaces/staffer_live/staffer-map-worker.js",
    "fec/surfaces/staffer_live/melange-dashboard.js",
    "fec/surfaces/staffer_live/melange-dashboard.css",
    "fec/surfaces/staffer_live/launch.js",
    "fec/surfaces/staffer_live/README.md",
    "fec/surfaces/test_staffer_graph_snapshot.py",
    "fec/surfaces/test_staffer_routing.py",
    "fec/surfaces/test_staffer_activity_index.py",
    "fec/surfaces/test_staffer_people_graph.py",
    "fec/tests/surfaces/test_staffer_identity_resolution.py",
    "fec/tests/surfaces/test_staffer_identity_auto.py",
    "fec/tests/surfaces/test_staffer_identity_web.py",
    "fec/tests/surfaces/test_staffer_automatic_service.py",
    "fec/tests/surfaces/test_staffer_inventory_cache.py",
    "fec/tests/surfaces/test_staffer_network_explorer.py",
    "fec/tests/surfaces/test_staffer_source_catalog.py",
    "fec/tests/surfaces/test_staffer_lda_catalog.py",
    "fec/tests/surfaces/test_staffer_inventory_worker.cjs",
    "fec/tests/surfaces/test_staffer_identity_api.py",
    "fec/tests/surfaces/test_staffer_routing_evidence.py",
    "fec/tests/surfaces/test_staffer_person_inventory.py",
    "fec/surfaces/test_staffer_live_service.py",
    "fec/tests/surfaces/test_staffer_live_server.py",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def install(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    receipt_dir = target / "audits/staffer_live_runtime"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    changes = []

    def update(path, data):
        previous = path.read_bytes() if path.is_file() else None
        if previous == data:
            changes.append({"path": str(path), "sha256": sha(data), "changed": False})
            return
        previous_stat = path.stat() if previous is not None else None
        # Existing dashboard/builders are backed up; never replace unrelated content.
        if previous is not None:
            backup = receipt_dir / "backups" / path.relative_to(target)
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                backup.write_bytes(previous)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".staffer-live.tmp")
        temp.write_bytes(data)
        if previous_stat and (path.stat().st_mtime_ns != previous_stat.st_mtime_ns or path.stat().st_size != previous_stat.st_size):
            temp.unlink()
            raise RuntimeError("Concurrent update detected; retry installation: " + str(path))
        os.replace(temp, path)
        changes.append({"path": str(path), "sha256": sha(data), "previous_sha256": sha(previous) if previous else None, "changed": True})

    for name in FILES:
        if name == 'fec/surfaces/staffer_user_assertions.json' and (target / name).is_file():
            changes.append({'path': str(target / name), 'sha256': sha((target / name).read_bytes()),
                            'changed': False, 'preserved_user_decisions': True})
            continue
        update(target / name, (source / name).read_bytes())
    # Surgical builder edits: source checkout may contain unrelated local changes.
    builder = target / "fec/surfaces/make_dashboard_html.py"
    content = builder.read_bytes().decode("utf-8")
    if "from surfaces.staffer_live_integration import inject_navigation" not in content:
        old = "    html=_tpl('make_dashboard_html_00.tpl')"
        if content.count(old) != 1:
            raise RuntimeError("Dashboard builder hook location changed; manual review needed")
        newline = "\r\n" if content[content.index(old) + len(old):].startswith("\r\n") else "\n"
        content = content.replace(old, "    from surfaces.staffer_live_integration import inject_navigation" + newline + "    html=inject_navigation(_tpl('make_dashboard_html_00.tpl'))")
        update(builder, content.encode("utf-8"))
    builder = target / "fec/surfaces/make_combined.py"
    content = builder.read_bytes().decode("utf-8")
    if "from surfaces.staffer_live_integration import inject_navigation" not in content:
        old = "out = os.path.join(B, 'Moneyball.html')"
        if content.count(old) != 1:
            raise RuntimeError("Combined builder hook location changed; manual review needed")
        newline = "\r\n" if content[content.index(old) + len(old):].startswith("\r\n") else "\n"
        content = content.replace(old, "from surfaces.staffer_live_integration import inject_navigation" + newline + "HTML = inject_navigation(HTML)" + newline + old)
        update(builder, content.encode("utf-8"))
    for name in ("Moneyball.html", "Moneyball Dashboard.html"):
        page = target / name
        if page.is_file():
            content = page.read_bytes().decode("utf-8")
            update(page, inject_navigation(content).encode("utf-8"))
    receipt = {"schema": "melange-staffer-live-install-v1", "installed_at": datetime.now(timezone.utc).isoformat(),
               "source": str(source), "target": str(target), "changes": changes,
               "raw_or_checkpoint_files_written": False, "existing_services_restarted": False,
               "dashboard_url": "http://127.0.0.1:8841/", "live_url": "http://127.0.0.1:8841/staffer-live/"}
    (receipt_dir / "integration_receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    result = install(Path(__file__).resolve().parents[2], args.target)
    print(json.dumps({"installed_files": len(result["changes"]), "live_url": result["live_url"], "target": result["target"]}))
