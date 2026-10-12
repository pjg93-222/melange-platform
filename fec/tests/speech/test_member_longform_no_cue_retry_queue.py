import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "fec/speech/build_member_longform_no_cue_retry_queue.py"
spec = importlib.util.spec_from_file_location("no_cue_retry", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_contextual_score_prefers_explicit_congressional_interview():
    generic = {"title": "Radio Hour", "channel": "Local News", "metadata_relevance_score": 10}
    explicit = {
        "title": "Podcast interview with Congressman Tim Kennedy",
        "channel": "Local News", "metadata_relevance_score": 8,
    }
    assert module.contextual_score("Timothy Kennedy", explicit) > module.contextual_score(
        "Timothy Kennedy", generic
    )


def test_retry_queue_contract_preserves_first_recording():
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"first_recording_discarded": False' in text
    assert '"search_metadata_establishes_identity": False' in text
