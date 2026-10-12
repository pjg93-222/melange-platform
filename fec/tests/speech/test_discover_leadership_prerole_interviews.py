import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "fec/speech/discover_leadership_prerole_interviews.py"
spec = importlib.util.spec_from_file_location("leadership_prerole", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_role_registry_matches_current_formal_leadership_roster():
    roles = json.loads((ROOT / "data/speech/member_leadership_role_cutoffs_v1.json").read_text())
    formal = json.loads((ROOT / "data/members/formal_visibility_roles.json").read_text())
    assert set(roles["members"]) == set(formal["members"])
    assert roles["members"]["Mike Johnson"]["current_role_assumed_on"] == "2023-10-25"
    assert roles["members"]["Steve Scalise"]["current_role_assumed_on"] == "2023-01-03"


def test_candidate_score_requires_name_and_rewards_political_context():
    other = {"trackName": "Interview with another Mike Johnson", "description": "musician"}
    political = {"trackName": "Interview with Congressman Mike Johnson", "description": "Congress"}
    assert module.candidate_score("Mike Johnson", political) > module.candidate_score(
        "Mike Johnson", other
    )


def test_contract_separates_prerole_and_role_conditioned_audio():
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"post_role_audio_is_role_conditioned": True' in text
    assert '"candidate_must_predate_current_role": True' in text
