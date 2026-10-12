import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "fec/speech/build_leadership_prerole_reviewed_sources.py"
spec = importlib.util.spec_from_file_location("leadership_prerole_reviewed", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_every_current_leader_has_a_reviewed_source_or_explicit_gap():
    roles = json.loads((ROOT / "data/speech/member_leadership_role_cutoffs_v1.json").read_text())
    covered = set(module.DISCOVERY_RANKS) | set(module.MANUAL_SOURCES) | set(module.GAPS)
    assert covered == set(roles["members"])
    assert set(module.GAPS) == {"Richard Durbin"}


def test_all_manual_sources_predate_the_current_role_and_are_longform_when_known():
    roles = json.loads((ROOT / "data/speech/member_leadership_role_cutoffs_v1.json").read_text())
    for member, source in module.MANUAL_SOURCES.items():
        assert module.parse_date(source["recorded_at"]) < module.parse_date(
            roles["members"][member]["current_role_assumed_on"]
        )
        if source["duration_seconds"] is not None:
            assert source["duration_seconds"] >= 600


def test_scalise_and_johnson_use_pre_current_role_sources():
    discovery = [
        json.loads(line) for line in
        (ROOT / "data/speech/member_leadership_prerole_discovery_v1.jsonl").read_text().splitlines()
        if line.strip()
    ]
    scalise = next(
        row for row in discovery
        if row["member"] == "Steve Scalise"
        and row["candidate_rank"] == module.DISCOVERY_RANKS["Steve Scalise"]
    )
    assert scalise["published"][:10] == "2020-02-07"
    assert module.MANUAL_SOURCES["Mike Johnson"]["recorded_at"] == "2022-03-24"


def test_output_contract_prohibits_post_role_baseline_substitution():
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"post_role_audio_is_role_conditioned_not_baseline": True' in text
    assert '"profile_effect": "NONE_UNTIL_FULL_PROMOTION"' in text
