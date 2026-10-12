"""Stage descriptive Cook PVI from a published snapshot without running models.

The explicit output directory must differ from the published root. This command
does not publish, rebuild combined pages, update aliases, or certify current
analytical sources. Existing scores, provenance dates and honors are preserved.
"""
from __future__ import annotations

import argparse
import ast
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sys

CODE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE_ROOT / "fec"))
from district.cook_pvi import attach_reference, load_reference


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def non_cook_digest(bundle):
    """Hash every existing field, including member order and analytical values."""
    view = {key: value for key, value in bundle.items() if key != "cook_pvi_meta"}
    view["members"] = [
        {key: value for key, value in member.items() if key != "cook_pvi"}
        for member in bundle["members"]
    ]
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    for chunk in encoder.iterencode(view):
        digest.update(chunk.encode("utf-8"))
    return digest.hexdigest()


def _functions(tree, names):
    found = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if len(found) != len(names) or {node.name for node in found} != set(names):
        raise ValueError("Renderer function contract changed: " + ", ".join(names))
    if any(node.decorator_list for node in found):
        raise ValueError("Renderer functions must not have import-time decorators")
    return found


def renderer_helpers(builder_path, kernel_path):
    """Compile only named display/serialization helpers; never import producers."""
    builder = ast.parse(builder_path.read_text(encoding="utf-8"))
    kernel = ast.parse(kernel_path.read_text(encoding="utf-8"))
    constants = {"HEAVY_MEMBER_FIELDS"}
    namespace = {"json": json, "os": os}
    for name in constants:
        assignments = [node for node in ast.walk(builder) if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)]
        if len(assignments) != 1:
            raise ValueError("Renderer constant contract changed: " + name)
        namespace[name] = ast.literal_eval(assignments[0].value)
    selected = _functions(builder, ("honors_generation_meta",))
    selected += _functions(kernel, ("json_for_script", "dump_atomic", "write_text_atomic"))
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(builder_path), "exec"), namespace)
    heavy = namespace["HEAVY_MEMBER_FIELDS"]
    if not isinstance(heavy, list) or "cook_pvi" not in heavy or len(set(heavy)) != len(heavy):
        raise ValueError("Cook PVI must be a distinct deferred profile field")
    return namespace


def prior_page_data(html):
    if html.count("const D=") != 1:
        raise ValueError("Published desk must contain exactly one const D payload")
    return json.JSONDecoder().raw_decode(html.split("const D=", 1)[1].lstrip())[0]


def _patch_published_html_bytes(html, source_template, payload):
    """Add only the two Cook helpers, method row and District call to live HTML."""
    start_anchor, end_anchor = "function cookPviSourceHTML(){", "function demoHTML(m){"
    if source_template.count(start_anchor) != 1 or source_template.count(end_anchor) != 1:
        raise ValueError("Cook renderer source anchors changed")
    helpers = source_template[source_template.index(start_anchor):source_template.index(end_anchor)]
    method_rows = [line for line in source_template.splitlines() if line.startswith("  if(m.cook_pvi)add(")]
    if len(method_rows) != 1:
        raise ValueError("Cook method source row changed")
    if "function cookPviHTML(" in html or "cookPviSourceHTML(" in html:
        raise ValueError("Published page already contains Cook renderers; review before refreshing them")
    if html.count(end_anchor) != 1 or html.count("const DSPLIT=1;") != 1:
        raise ValueError("Published page must have the supported split-profile renderer")
    newline = "\r\n" if "\r\n" in html else "\n"
    helpers = helpers.replace("\r\n", "\n").replace("\n", newline)
    method_line = method_rows[0] + newline
    method_anchor = "  if(m.district_brief)add('District brief',"
    if html.count(method_anchor) != 1:
        raise ValueError("Published district-method anchor changed")
    method_end = html.index(newline, html.index(method_anchor)) + len(newline)
    base_old = '  const base=`${districtBrief}<div class=gen'
    base_new = '  const base=`${cookPviHTML(m)}${districtBrief}<div class=gen'
    if html.count(base_old) != 1:
        raise ValueError("Published District renderer anchor changed")
    # Replacing only the JSON span keeps all surrounding published bytes intact.
    marker = "const D="
    if html.count(marker) != 1:
        raise ValueError("Published data anchor changed")
    data_start = html.index(marker) + len(marker)
    while html[data_start].isspace():
        data_start += 1
    _, data_size = json.JSONDecoder().raw_decode(html[data_start:])
    data_end = data_start + data_size
    data_marker = "__COOK_REFRESH_PRESERVED_DATA__"
    if data_marker in html:
        raise ValueError("Reserved candidate marker is already present")
    baseline = html[:data_start] + data_marker + html[data_end:]
    candidate = html[:method_end] + method_line + html[method_end:]
    candidate = candidate.replace(end_anchor, helpers + end_anchor, 1).replace(base_old, base_new, 1)
    # The three insertions occur after the payload and cannot move its offsets.
    candidate = candidate[:data_start] + data_marker + candidate[data_end:]
    restored = candidate.replace(helpers, "", 1).replace(method_line, "", 1).replace(base_new, base_old, 1)
    if restored != baseline:
        raise RuntimeError("A non-Cook published HTML byte changed")
    digest = hashlib.sha256(baseline.encode("utf-8")).hexdigest()
    return candidate.replace(data_marker, payload, 1), digest


def patch_published_html(text, enriched_bundle, template):
    """Return (HTML, proof) for a saved desk or served desk frame.

    Cook fields are inline in the existing light payload. An unchanged canonical
    profiles script can still merge its prior fields without losing this context.
    Only source-template Cook renderers are used; all other page bytes stay put.
    """
    light = prior_page_data(text)
    rows = light.get("members")
    enriched = enriched_bundle.get("members")
    if not isinstance(rows, list) or not isinstance(enriched, list) or len(rows) != len(enriched):
        raise ValueError("Served and enriched desk rosters differ")
    if (enriched_bundle.get("cook_pvi_meta") or {}).get("display_only") is not True:
        raise ValueError("Cook context must be explicitly descriptive")
    before = non_cook_digest(light)
    for shown, source in zip(rows, enriched, strict=True):
        if shown.get("name") != source.get("name") or not isinstance(source.get("cook_pvi"), dict):
            raise ValueError("Served and enriched member identity/context differ")
        for field in ("chamber", "state", "district"):
            if field in shown and shown[field] != source.get(field):
                raise ValueError("Served and enriched member " + field + " differ: " + str(shown.get("name")))
        prior_bioguide = (shown.get("bio") or {}).get("bioguide")
        if prior_bioguide and prior_bioguide != (source.get("bio") or {}).get("bioguide"):
            raise ValueError("Served and enriched member bioguide differs: " + str(shown.get("name")))
        shown["cook_pvi"] = source["cook_pvi"]
    light["cook_pvi_meta"] = enriched_bundle["cook_pvi_meta"]
    after = non_cook_digest(light)
    if after != before:
        raise RuntimeError("A non-Cook served page data value changed")
    helpers = renderer_helpers(CODE_ROOT / "fec/surfaces/make_member_desk.py", CODE_ROOT / "fec/moneyball_core.py")
    payload = helpers["json_for_script"](light, separators=(",", ":"), ensure_ascii=False)
    patched, html_digest = _patch_published_html_bytes(text, template, payload)
    return patched, {"non_cook_payload_before_sha256": before, "non_cook_payload_after_sha256": after,
                     "published_html_without_payload_sha256": html_digest,
                     "members": len(rows), "inline_cook_pvi": True}


def verify_published_projection(bundle, light, heavy):
    """Refuse a saved bundle that differs from its published desk projection."""
    if not isinstance(light, dict) or len(light.get("members", [])) != len(bundle["members"]):
        raise ValueError("Published desk and saved bundle have different rosters")
    excluded = {"members", "bill_lobbying", "cook_pvi_meta", "efficiency_layers", "efficiency_layers_display"}
    expected = {key: value for key, value in bundle.items() if key not in excluded}
    actual = {key: value for key, value in light.items() if key not in excluded}
    if expected != actual:
        raise ValueError("Published desk and saved bundle disagree on non-Cook metadata")
    for saved, shown in zip(bundle["members"], light["members"], strict=True):
        expected = {key: value for key, value in saved.items() if key not in heavy}
        actual = {key: value for key, value in shown.items() if key not in heavy}
        if expected != actual:
            raise ValueError("Published desk and saved bundle disagree: " + str(saved.get("name")))
    for key in ("efficiency_layers", "efficiency_layers_display"):
        if not isinstance(light.get(key), dict):
            raise ValueError("Published desk lacks preserved " + key)
    if light["efficiency_layers"].get("schema") != "efficiency-layers-v2":
        raise ValueError("Published efficiency schema is unsupported")
    if not (bundle.get("rank_honors") or {}).get("generation_id"):
        raise ValueError("Published honors generation is missing")


def verify_published_profiles(bundle, text, heavy):
    """Refuse any old deferred profile value that disagrees with the saved bundle."""
    prefix = "window.__melangeProfileMerge("
    if not text.startswith(prefix):
        raise ValueError("Published profile script wrapper changed")
    profile, end = json.JSONDecoder().raw_decode(text[len(prefix):])
    if text[len(prefix) + end:].strip() != ");" or set(profile) != {"members_heavy", "bill_lobbying"}:
        raise ValueError("Published profile payload shape changed")
    rows = profile["members_heavy"]
    if not isinstance(rows, list) or len(rows) != len(bundle["members"]):
        raise ValueError("Published profile and saved bundle rosters differ")
    if profile["bill_lobbying"] != bundle.get("bill_lobbying"):
        raise ValueError("Published bill-lobbying data differs from saved bundle")
    for saved, shown in zip(bundle["members"], rows, strict=True):
        expected = {key: saved[key] for key in heavy if key in saved and key != "cook_pvi"}
        actual = {key: value for key, value in shown.items() if key != "cook_pvi"}
        if expected != actual:
            raise ValueError("Published deferred profile differs from saved bundle: " + str(saved.get("name")))
    return profile


def build_candidate(root, out_dir, source_dir=None):
    root, out_dir = Path(root).resolve(), Path(out_dir).resolve()
    source_dir = Path(source_dir or CODE_ROOT / "source_data/cook_pvi/2026").resolve()
    if out_dir == root or root.is_relative_to(out_dir):
        raise ValueError("Output must be a separate candidate directory, not the published root or its parent")
    builder = CODE_ROOT / "fec/surfaces/make_member_desk.py"
    kernel = CODE_ROOT / "fec/moneyball_core.py"
    template_path = CODE_ROOT / "fec/surfaces/templates/make_member_desk_00.tpl"
    paths = [root / "member_desk_bundle.json", root / "cu-member-desk.html",
             root / "cu-member-desk-profiles.js", root / "fec/legislators.csv",
             Path(__file__).resolve(), builder, kernel, template_path,
             CODE_ROOT / "fec/district/cook_pvi.py", source_dir / "source_manifest.json"]
    manifest = json.loads((source_dir / "source_manifest.json").read_text(encoding="utf-8"))
    for relative in manifest["sha256"]:
        target = (source_dir / relative.replace("\\", "/")).resolve()
        if not target.is_relative_to(source_dir):
            raise ValueError("Cook source manifest contains an out-of-directory path")
        paths.append(target)
    inputs = {str(path): sha256(path) for path in paths}

    def unchanged():
        changed = [path for path, digest in inputs.items() if sha256(path) != digest]
        if changed:
            raise RuntimeError("Input changed during candidate build: " + ", ".join(changed))

    output_names = ("member_desk_bundle.json", "cu-member-desk.html", "cu-member-desk-profiles.js",
                    "data/district/cook_pvi_2026.json", "cook_pvi_refresh_receipt.json")
    if any((out_dir / name).exists() for name in output_names):
        raise FileExistsError("Candidate output already exists; choose a new --out-dir")
    bundle = json.loads((root / "member_desk_bundle.json").read_text(encoding="utf-8"))
    if not isinstance(bundle.get("members"), list) or not bundle["members"]:
        raise ValueError("Saved bundle must contain members")
    original_non_cook = non_cook_digest(bundle)
    old_html = (root / "cu-member-desk.html").read_bytes().decode("utf-8")
    light = prior_page_data(old_html)
    helpers = renderer_helpers(builder, kernel)
    heavy = helpers["HEAVY_MEMBER_FIELDS"]
    verify_published_projection(bundle, light, heavy)
    published_profiles = verify_published_profiles(
        bundle, (root / "cu-member-desk-profiles.js").read_bytes().decode("utf-8"), heavy)
    honors_marker = helpers["honors_generation_meta"](bundle)
    if old_html.count(honors_marker) != 1:
        raise ValueError("Published desk honors header differs from its saved bundle")
    published_non_cook = non_cook_digest(light)

    legislators = {}
    for row in csv.DictReader(io.StringIO((root / "fec/legislators.csv").read_text(encoding="utf-8-sig"))):
        bioguide = row.get("bioguide_id")
        if bioguide:
            if bioguide in legislators:
                raise ValueError("Duplicate legislator bioguide: " + bioguide)
            legislators[bioguide] = row
    reference = load_reference(source_dir)
    attach_reference(bundle, reference, legislators)
    preserved_digest = non_cook_digest(bundle)
    if preserved_digest != original_non_cook:
        raise RuntimeError("Cook context attachment changed an existing field")

    heavy_rows = [{**row, "cook_pvi": member["cook_pvi"]}
                  for row, member in zip(published_profiles["members_heavy"], bundle["members"], strict=True)]
    script_json = helpers["json_for_script"]
    profile_script = "window.__melangeProfileMerge(" + script_json(
        {"members_heavy": heavy_rows, "bill_lobbying": published_profiles["bill_lobbying"]},
        separators=(",", ":"), ensure_ascii=False) + ");"
    template, html_proof = patch_published_html(
        old_html, bundle, template_path.read_bytes().decode("utf-8"))
    if template.count(honors_marker) != 1:
        raise ValueError("Candidate honors header changed")
    unchanged()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "data/district").mkdir(parents=True, exist_ok=True)
    helpers["dump_atomic"](bundle, str(out_dir / output_names[0]), separators=(",", ":"), ensure_ascii=False)
    helpers["write_text_atomic"](template, str(out_dir / output_names[1]))
    helpers["write_text_atomic"](profile_script, str(out_dir / output_names[2]))
    helpers["dump_atomic"](reference, str(out_dir / output_names[3]), indent=2, ensure_ascii=False)
    unchanged()
    receipt = {
        "schema": "cook-pvi-descriptive-refresh-v1", "status": "PASS_DESCRIPTIVE_CANDIDATE",
        "created_at": datetime.now(timezone.utc).isoformat(), "published": False,
        "root": str(root), "code_root": str(CODE_ROOT), "out_dir": str(out_dir),
        "scope": "Cook PVI descriptive context only; no analytical-source currency certification",
        "models_run": False, "members": len(bundle["members"]),
        "coverage": bundle["cook_pvi_meta"]["coverage"],
        "honors_generation_id": bundle["rank_honors"]["generation_id"],
        "non_cook_before_sha256": original_non_cook, "non_cook_after_sha256": preserved_digest,
        "published_non_cook_payload_sha256": published_non_cook,
        "published_html_without_payload_sha256": html_proof["published_html_without_payload_sha256"],
        "html_proof": html_proof,
        "preserved": ["all non-Cook bundle fields", "published efficiency layers and percentiles",
                      "honors generation", "existing freshness dates",
                      "all non-Cook deferred profile and bill-lobbying values",
                      "all published HTML bytes except data payload and three Cook insertions"],
        "inputs_sha256": inputs,
        "outputs_sha256": {name: sha256(out_dir / name) for name in output_names[:-1]},
    }
    helpers["dump_atomic"](receipt, str(out_dir / output_names[-1]), indent=2, ensure_ascii=False)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Published data root to read")
    parser.add_argument("--out-dir", type=Path, required=True, help="New candidate directory; never published in place")
    parser.add_argument("--source-dir", type=Path, help="Hash-bound Cook source snapshot; defaults to this code checkout")
    args = parser.parse_args()
    receipt = build_candidate(args.root, args.out_dir, args.source_dir)
    print(json.dumps({key: receipt[key] for key in ("status", "out_dir", "members", "coverage",
                     "honors_generation_id", "non_cook_before_sha256", "non_cook_after_sha256")}, indent=2))


if __name__ == "__main__":
    main()
