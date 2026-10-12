"""Standalone staged partisan-share baselines. Standard library only; no live activation.

`stage` packages already-frozen policies and results; it never selects or fits.
`project` applies that package to explicit, dated, identity-checked anchors.
Company/employee/PAC use is a scenario transfer, not independently validated.
"""
from __future__ import annotations

import argparse
import copy
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys

CHAMBERS = ("House", "Senate")
INDUSTRIES = ("auto_light_duty_manufacturing", "commercial_banks", "credit_unions", "property_casualty")
BASELINES = ("persistence", "same_phase", "equal_blend")
BASIS = ("industry_two_party_candidate_giving", "employee_two_party_candidate_giving", "pac_two_party_candidate_giving")
SHA = re.compile(r"^[0-9a-f]{64}$")


class ContractError(ValueError):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ContractError("duplicate JSON key: " + key)
        out[key] = value
    return out


def read_json(path):
    raw = Path(path).read_bytes()
    value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_object,
                       parse_constant=lambda x: (_ for _ in ()).throw(ContractError("nonfinite JSON: " + x)))
    if not isinstance(value, dict):
        raise ContractError("JSON root must be an object")
    return value, sha256(raw)


def iso_date(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ContractError(name + " must be an ISO calendar date")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ContractError(name + " is not a calendar date") from error


def finite_share(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ContractError("share must be a finite number in [0,1]; missing is not zero")
    return float(value)


def validate_scope(scope):
    keys = {"kind", "id", "industry_id", "accounting_basis", "member_ids"}
    if not isinstance(scope, dict) or set(scope) != keys:
        raise ContractError("scope requires kind, id, industry_id, accounting_basis, member_ids only")
    if scope["kind"] not in ("industry", "company", "roster") or scope["industry_id"] not in INDUSTRIES:
        raise ContractError("unsupported scope; community banks are not commercial banks")
    if not isinstance(scope["id"], str) or not scope["id"].strip() or scope["accounting_basis"] not in BASIS:
        raise ContractError("invalid scope identity or accounting basis")
    ids = scope["member_ids"]
    if not isinstance(ids, list) or any(not isinstance(x, str) or not x.strip() for x in ids) or ids != sorted(set(ids)):
        raise ContractError("member_ids must be a sorted unique list of explicit identities")
    if scope["kind"] == "industry":
        if scope["id"] != scope["industry_id"] or ids:
            raise ContractError("whole-industry scope must use its industry ID and no narrower member roster")
    elif not ids:
        raise ContractError("company and roster scopes require explicit member IDs")


def seal_anchor(anchor):
    """Fingerprint supplied anchor content; this does not verify external source truth."""
    if "snapshot_sha256" in anchor:
        raise ContractError("anchor is already sealed")
    return dict(copy.deepcopy(anchor), snapshot_sha256=sha256(canonical(anchor)))


def validate_anchor(anchor, expected_hash, chamber, scope, cycle, as_of):
    keys = {"chamber", "scope", "cycle", "share", "complete", "observed_through", "available_on", "source_id", "source_sha256", "snapshot_sha256"}
    if not isinstance(anchor, dict) or set(anchor) != keys:
        raise ContractError("anchor fields are missing or unknown")
    for value in (anchor["source_sha256"], anchor["snapshot_sha256"], expected_hash):
        if not isinstance(value, str) or not SHA.fullmatch(value):
            raise ContractError("anchor hashes must be lowercase SHA256 strings")
    content = {k: v for k, v in anchor.items() if k != "snapshot_sha256"}
    if sha256(canonical(content)) != anchor["snapshot_sha256"] or anchor["snapshot_sha256"] != expected_hash:
        raise ContractError("anchor snapshot differs from its pinned identity")
    if anchor["chamber"] != chamber or anchor["scope"] != scope:
        raise ContractError("anchor chamber, membership or accounting scope mismatch")
    if type(anchor["cycle"]) is not int or anchor["cycle"] != cycle:
        raise ContractError("anchor cycle differs from the frozen family rule")
    if anchor["complete"] is not True:
        raise ContractError("incomplete anchor cannot substitute for a full-cycle share")
    end = iso_date(anchor["observed_through"], "observed_through")
    available = iso_date(anchor["available_on"], "available_on")
    if end != date(cycle, 12, 31) or available < end or available > as_of:
        raise ContractError("anchor coverage or availability is incompatible with the information cutoff")
    if not isinstance(anchor["source_id"], str) or not anchor["source_id"].strip():
        raise ContractError("anchor source identity is required")
    return finite_share(anchor["share"])


def baseline_value(family, prior, same=None):
    if family not in BASELINES:
        raise ContractError("package uses an unsupported estimator; no fitting is permitted")
    prior = finite_share(prior)
    if family == "persistence":
        return prior
    same = finite_share(same)
    return same if family == "same_phase" else (prior + same) / 2


def validate_package(package):
    if (package.get("schema_version") != 1 or package.get("kind") != "staged_partisan_baseline_package"
            or package.get("live_activation") is not False or package.get("publication_status") != "HOLD"
            or package.get("supported_industries") != list(INDUSTRIES)):
        raise ContractError("invalid staged package identity, scope, or activation status")
    if package.get("uncertainty", {}).get("status") != "unavailable":
        raise ContractError("these baselines have no calibrated uncertainty contract")
    expected_version = "partisan-baseline-preview-" + package.get("policy_manifest_sha256", "")[:12]
    if package.get("estimator_version") != expected_version or set(package.get("families", {})) != set(CHAMBERS):
        raise ContractError("package version or chamber identity mismatch")
    for family in package["families"].values():
        if family not in BASELINES:
            raise ContractError("only already-frozen baseline families can be staged")
    content = {k: v for k, v in package.items() if k != "content_sha256"}
    if sha256(canonical(content)) != package.get("content_sha256"):
        raise ContractError("package content hash mismatch")


def load_package(path, expected_sha256):
    package, actual = read_json(path)
    if not isinstance(expected_sha256, str) or not SHA.fullmatch(expected_sha256) or actual != expected_sha256:
        raise ContractError("package file differs from its pinned SHA256")
    validate_package(package)
    return package


def project(package, request):
    """Pure projection. A number being ready never authorizes live activation."""
    validate_package(package)
    required = {"schema_version", "estimator_version", "chamber", "target_cycle", "as_of", "scope", "anchors", "expected_anchor_sha256"}
    if not isinstance(request, dict) or set(request) != required or request["schema_version"] != 1:
        raise ContractError("request fields are missing or unknown")
    if request["estimator_version"] != package["estimator_version"] or request["chamber"] not in CHAMBERS:
        raise ContractError("request estimator version or chamber mismatch")
    year = request["target_cycle"]
    if type(year) is not int or year < 2016 or year % 2:
        raise ContractError("target_cycle must be an even election year at least 2016")
    as_of = iso_date(request["as_of"], "as_of")
    if not date(year - 1, 1, 1) <= as_of <= date(year, 12, 31):
        raise ContractError("only the explicit current election cycle is supported; no chaining")
    scope = request["scope"]
    validate_scope(scope)
    family = package["families"][request["chamber"]]
    needs = ("prior_cycle",) if family == "persistence" else ("prior_cycle", "same_phase")
    anchors, expected = request["anchors"], request["expected_anchor_sha256"]
    if not isinstance(anchors, dict) or not isinstance(expected, dict) or set(anchors) != set(needs) or set(expected) != set(needs):
        raise ContractError("supply exactly the anchors the frozen family uses; no missing-anchor fallback")
    shares = {name: validate_anchor(anchors[name], expected[name], request["chamber"], scope,
                                   year - (2 if name == "prior_cycle" else 4), as_of) for name in needs}
    r_share = baseline_value(family, shares["prior_cycle"], shares.get("same_phase"))
    transported = scope["kind"] != "industry" or scope["accounting_basis"] != "industry_two_party_candidate_giving"
    formula = {"persistence": "R_share = prior_cycle_share", "same_phase": "R_share = same_phase_share",
               "equal_blend": "R_share = (prior_cycle_share + same_phase_share) / 2"}[family]
    return {
        "schema_version": 1, "status": "ready_for_offline_review", "live_activation": False,
        "publication_status": "HOLD", "publication_holds": copy.deepcopy(package["publication_holds"]),
        "estimator_version": package["estimator_version"], "package_content_sha256": package["content_sha256"],
        "family": family, "formula": formula, "chamber": request["chamber"], "target_cycle": year,
        "as_of": request["as_of"], "package_built_at_utc": package["built_at_utc"],
        "historical_issue_claim": False, "scope": copy.deepcopy(scope),
        "validation_scope": "unvalidated scenario transfer to the supplied scope" if transported else "whole-industry historical evaluation; publication gates remain on hold",
        "share": {"status": "ready", "Republican": r_share, "Democratic": 1 - r_share,
                  "denominator": "Democratic plus Republican candidate giving; Other excluded",
                  "change_from_prior_pp": 100 * (r_share - shares["prior_cycle"]), "clipped": False},
        "uncertainty": copy.deepcopy(package["uncertainty"]),
        "allocation": {"status": "not_provided", "reason": "No compatible chamber-party dollar budget is supplied or inferred."},
        "inputs_used": {"anchor_shares": shares, "anchor_snapshot_sha256": copy.deepcopy(expected)},
        "inputs_unused": [], "source_verification": "Snapshot content and caller-pinned identities checked; external source truth is not independently checked here.",
    }


def create_package(policy, policy_hash, development, development_hash, confirmation, confirmation_hash,
                   verification, verification_hash, bundle_hash, runtime_hash):
    """Copy locked choices and evidence. No candidate selection or model fitting."""
    if policy.get("kind") != "frozen_partisan_share_policy" or policy.get("training_through") != 2022:
        raise ContractError("expected the frozen 2012--2022 policy")
    if policy.get("input_sha256") != bundle_hash or development.get("input_sha256") != bundle_hash:
        raise ContractError("policy and evaluation input identity mismatch")
    if confirmation.get("policy_manifest_sha256") != policy_hash or confirmation.get("selection_performed") is not False:
        raise ContractError("confirmation is not bound to the frozen policy")
    if confirmation.get("input_sha256") != bundle_hash:
        raise ContractError("confirmation input bundle identity mismatch")
    if development.get("evaluator_sha256") != policy.get("evaluator_sha256") or confirmation.get("evaluator_sha256") != policy.get("evaluator_sha256"):
        raise ContractError("evaluator identity mismatch")
    if verification.get("passed") is not True or policy.get("failure_count") != 0 or development.get("failure_count") != 0 or confirmation.get("failure_count") != 0:
        raise ContractError("evaluation or independent verification incomplete")
    if development_hash not in verification.get("inputs", {}).values():
        raise ContractError("independent verification is not bound to this development evaluation")
    families = {c: policy["selections"][c]["selected_family"] for c in CHAMBERS}
    if any(f not in BASELINES for f in families.values()):
        raise ContractError("this package writer only implements frozen baseline winners")
    if families != {"House": "persistence", "Senate": "equal_blend"}:
        raise ContractError("this reviewed package is restricted to the actual frozen House/Senate choices")
    for chamber in CHAMBERS:
        if development["final_selection"][chamber]["selected_family"] != families[chamber]:
            raise ContractError("development choice differs from frozen manifest")
    holds = ["Live activation has not been authorized.",
             "Existing G6 publication gate is not cleared; previous waivers do not transfer.",
             "The House nested policy did not beat persistence in development.",
             "The Senate equal-blend G6 diagnostic lacks the 2014 same-phase anchor.",
             "2024 is reused historical confirmation, not an untouched test; Senate persistence performed better than the frozen blend."]
    g6 = []
    for row in development["loeo_diagnostic"]["metrics"]:
        if row["industry"] == "__ALL__":
            continue
        published = development["published_g6_loeo_reference_pp"][row["chamber"]][row["industry"]]
        complete = row["n"] == 5 and row["origins"] == [2014, 2016, 2018, 2020, 2022]
        g6.append({"chamber": row["chamber"], "industry": row["industry"], "candidate_mae_pp": row["mae_pp"],
                   "published_mae_pp": published, "candidate_origins": row["origins"], "same_complete_grid": complete,
                   "passes": complete and row["mae_pp"] <= published + 1e-9})
    package = {
        "schema_version": 1, "kind": "staged_partisan_baseline_package",
        "estimator_version": "partisan-baseline-preview-" + policy_hash[:12],
        "built_at_utc": datetime.now(timezone.utc).isoformat(), "live_activation": False, "publication_status": "HOLD",
        "publication_holds": holds, "families": families, "supported_industries": list(INDUSTRIES),
        "chamber_status": {"House": "research_only_development_policy_failed", "Senate": "development_pass_publication_HOLD"},
        "activation_recommended": False,
        "policy_manifest_sha256": policy_hash, "input_bundle_sha256": bundle_hash,
        "evaluation_history_sha256": policy["history_sha256"], "evaluator_sha256": policy["evaluator_sha256"],
        "predictor_sha256": runtime_hash, "development_evaluation_sha256": development_hash,
        "confirmation_evaluation_sha256": confirmation_hash, "independent_verification_sha256": verification_hash,
        "formula_version": "explicit-baseline-anchors-v1", "uncertainty": {"status": "unavailable", "interval": None,
            "reason": "No calibrated prediction interval or OLS p-value applies to these baseline estimators."},
        "evaluation": {"development_decisions": verification["decisions"],
                       "development_metrics": development["metrics"], "confirmation_metrics": confirmation["metrics"],
                       "confirmation_metadata": confirmation.get("holdout_metadata", {}),
                       "g6": g6, "g6_missing": development["loeo_diagnostic_failures"],
                       "confirmation_status": "previously examined reserved historical confirmation",
                       "scope": "whole-industry two-party candidate giving, four industries; P&C uses the approved broad-insurance proxy",
                       "vintage_limitation": "Retrospective source editions and provider transfer; no historical information-availability certification."},
        "scope_transport": "Company, employee and roster-PAC anchors are explicit scenario transfers with no separate accuracy validation.",
    }
    package["content_sha256"] = sha256(canonical(package))
    validate_package(package)
    return package


def stage(args):
    destination = args.output
    if destination.exists():
        raise ContractError("staging directory exists; overwrite refused")
    inputs = {}
    for label in ("policy_manifest", "development", "confirmation", "verification", "bundle"):
        inputs[label] = read_json(getattr(args, label))
    policy, policy_hash = inputs["policy_manifest"]
    if policy_hash != args.policy_sha256:
        raise ContractError("frozen policy hash differs from the explicitly pinned identity")
    # Bind the development history content as well as its containing file.
    history = {c: sorted([{k: r[k] for k in ("industry", "cycle", "share", "forecast", "current_control", "growth_pct", "prior_growth_pct", "pres")}
                        for r in inputs["bundle"][0]["share_models"]["control_" + c]["history"]], key=lambda r: (r["cycle"], r["industry"])) for c in CHAMBERS}
    if sha256(canonical(history)) != policy.get("history_sha256"):
        raise ContractError("frozen development history hash mismatch")
    runtime = Path(__file__).read_bytes()
    package = create_package(policy, policy_hash, *inputs["development"], *inputs["confirmation"],
                             *inputs["verification"], inputs["bundle"][1], sha256(runtime))
    package_bytes = canonical(package)
    package_hash = sha256(package_bytes)
    readme = ("# Offline partisan-share predictor\n\nPublication status: HOLD. Live activation: false.\n\n"
              "House uses the frozen prior-cycle persistence rule. Senate uses the frozen equal blend of t-2 and t-4 shares. "
              "Neither family is a learned regression. No p-values, prediction intervals or dollar forecasts are supplied.\n\n"
              "The package retains the independent chronological checks, reused 2024 confirmation and failed/incomplete G6 comparisons. "
              "This is executable review material, not authorization to replace the live estimator.\n\n"
              "Run the copied predict.py with `project --package package.json --package-sha256 <digest in package.sha256> "
              "--request request.json --output result.json`. The output file must not already exist.\n\n"
              "Requests require estimator version, current-cycle as_of, chamber, exact scope, and sealed complete-cycle anchors. "
              "Use the exported seal_anchor function to fingerprint supplied anchor contents; supply the trusted expected fingerprints separately. "
              "External source truth must be checked by the caller. Company/employee/PAC anchors are unvalidated scenario transfers.\n")
    destination.mkdir(parents=True, exist_ok=False)
    payloads = {"package.json": package_bytes, "package.sha256": (package_hash + "\n").encode(), "predict.py": runtime,
                "README.md": readme.encode(), "policy_manifest.json": Path(args.policy_manifest).read_bytes(),
                "independent_verification.json": Path(args.verification).read_bytes()}
    for chamber in CHAMBERS:
        scope = {"kind": "company", "id": "synthetic-company-example", "industry_id": "commercial_banks",
                 "accounting_basis": "pac_two_party_candidate_giving", "member_ids": ["synthetic-company-example"]}
        anchors = {}
        for name, year, share in (("prior_cycle", 2024, 0.6), ("same_phase", 2022, 0.5)):
            if name == "same_phase" and package["families"][chamber] == "persistence":
                continue
            anchors[name] = seal_anchor({"chamber": chamber, "scope": scope, "cycle": year, "share": share,
                "complete": True, "observed_through": f"{year}-12-31", "available_on": f"{year+1}-01-15",
                "source_id": "SYNTHETIC_DEMONSTRATION_ONLY", "source_sha256": "0" * 64})
        request = {"schema_version": 1, "estimator_version": package["estimator_version"], "chamber": chamber,
                   "target_cycle": 2026, "as_of": "2026-10-05", "scope": scope, "anchors": anchors,
                   "expected_anchor_sha256": {k: a["snapshot_sha256"] for k, a in anchors.items()}}
        payloads[f"example-{chamber.lower()}-request-SYNTHETIC.json"] = canonical(request)
        payloads[f"example-{chamber.lower()}-result-SYNTHETIC.json"] = canonical(project(package, request))
    payloads["staging_receipt.json"] = canonical({"schema_version": 1, "package_sha256": package_hash,
        "runtime_sha256": sha256(runtime), "policy_manifest_sha256": policy_hash, "live_activation": False,
        "activation_recommended": False, "artifacts": {name: sha256(raw) for name, raw in payloads.items()}})
    for name, content in payloads.items():
        with (destination / name).open("xb") as stream:
            stream.write(content)
    print(json.dumps({"staged": str(destination), "package_sha256": package_hash, "families": package["families"], "live_activation": False}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("stage")
    for flag in ("policy-manifest", "development", "confirmation", "verification", "bundle", "output"):
        build.add_argument("--" + flag, type=Path, required=True)
    build.add_argument("--policy-sha256", required=True)
    run = commands.add_parser("project")
    for flag in ("package", "request", "output"):
        run.add_argument("--" + flag, type=Path, required=True)
    run.add_argument("--package-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "stage":
            stage(args)
        else:
            if args.output.exists():
                raise ContractError("projection output exists; overwrite refused")
            package = load_package(args.package, args.package_sha256)
            if package["predictor_sha256"] != sha256(Path(__file__).read_bytes()):
                raise ContractError("runtime code differs from the packaged predictor")
            request, _ = read_json(args.request)
            result = project(package, request)
            with args.output.open("xb") as stream:
                stream.write(canonical(result))
            print(str(args.output) + ": offline projection saved; live activation remains false")
        return 0
    except (ContractError, ValueError, OSError, KeyError, TypeError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
