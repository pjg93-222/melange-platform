import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "fec/speech/discover_member_longform_youtube.py"
spec = importlib.util.spec_from_file_location("youtube_discovery", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_candidate_score_requires_member_name_and_rewards_interview_context():
    good = {
        "title": "Taxes, Tips, and the State of the Union: Rep. Aaron Bean",
        "description": "A full podcast interview with Representative Aaron Bean.",
    }
    wrong = {
        "title": "Long congressional interview with another representative",
        "description": "No named guest.",
    }
    assert module.candidate_score("Aaron Bean", good) >= 10
    assert module.candidate_score("Aaron Bean", wrong) == 0


def test_candidate_score_accepts_common_public_first_name_variant():
    bernie = {
        "title": "A long-form interview with Bernie Sanders",
        "description": "The senator joins the podcast.",
    }
    assert module.candidate_score("Bernard Sanders", bernie) >= 10


def test_turn_and_identity_promotion_remain_separate_from_search():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "CANDIDATE_IDENTITY_UNVERIFIED" in text
    assert "promotion_requires_audio_hash_asr_in_band_name_full_turns_and_speaker_gate" in text
    assert "candidate_changes_profile" in text
