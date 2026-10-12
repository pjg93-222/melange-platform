import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "fec/speech/screen_member_longform_identity_cues.py"
spec = importlib.util.spec_from_file_location("screen_member_longform_identity_cues", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def word(index, value):
    return {"word": value, "start_sec": float(index), "end_sec": float(index) + 0.8}


def test_full_name_and_approved_alias_are_candidate_cues():
    exact = [word(i, value) for i, value in enumerate("welcome Senator Alex Padilla thank you".split())]
    alias = [word(i, value) for i, value in enumerate("our guest is Steve Scalise today".split())]
    assert module.find_name_cues(exact, "Alex Padilla")[0]["phrase"] == "Alex Padilla"
    assert "Steve Scalise" in module.find_name_cues(alias, "Stephen Scalise")[0]["phrase"]


def test_surname_only_is_not_a_hard_name_cue():
    words = [word(i, value) for i, value in enumerate("welcome Congressman Padilla thank you".split())]
    assert module.find_name_cues(words, "Alex Padilla") == []


def test_common_first_name_forms_and_conservative_asr_forms_are_candidates():
    bernie = [word(i, value) for i, value in enumerate("our guest is Bernie Sanders today".split())]
    katherine = [word(i, value) for i, value in enumerate("welcome Catherine Clark today".split())]
    hakeem = [word(i, value) for i, value in enumerate("Congressman Hakim Jefferies joins us".split())]
    amy = [word(i, value) for i, value in enumerate("someone named Amy kashar spoke".split())]
    assert module.find_name_cues(bernie, "Bernard Sanders")
    assert module.find_name_cues(katherine, "Katherine Clark")
    assert module.find_name_cues(hakeem, "Hakeem Jeffries")
    cue = module.find_name_cues(amy, "Amy Klobuchar")[0]
    assert cue["first_match"] == "exact_or_approved_alias"
    assert cue["surname_similarity"] >= 0.5


def test_requested_member_aliases_are_bidirectional_name_cues():
    cases = (
        ("Congresswoman Jan Schakowsky joins us", "Janice Schakowsky"),
        ("Congresswoman Janice Schakowsky joins us", "Jan Schakowsky"),
        ("Congressman Pat Ryan joins us", "Patrick Ryan"),
        ("Congressman Patrick Ryan joins us", "Pat Ryan"),
        ("Congressman Bobby Scott joins us", "Robert Scott"),
        ("Congressman Robert Scott joins us", "Bobby Scott"),
        ("Congressman Rob Scott joins us", "Bobby Scott"),
        ("Congressman Bob Scott joins us", "Bobby Scott"),
    )
    for phrase, member in cases:
        words = [word(i, value) for i, value in enumerate(phrase.split())]
        assert module.find_name_cues(words, member), (phrase, member)
        assert module.turns.evidence_names_member(phrase, member), (phrase, member)


def test_kean_kane_asr_homophone_is_a_candidate_not_identity_authority():
    words = [word(i, value) for i, value in enumerate("Senator Tom Kane joins us".split())]
    cue = module.find_name_cues(words, "Thomas Kean")[0]
    assert cue["phrase"] == "Tom Kane"
    assert cue["surname_similarity"] == 0.75


def test_low_similarity_surname_is_still_rejected():
    words = [word(i, value) for i, value in enumerate("welcome Amy Kennedy today".split())]
    assert module.find_name_cues(words, "Amy Klobuchar") == []


def test_split_surname_and_compact_full_name_asr_forms_are_candidates():
    split = [word(i, value) for i, value in enumerate("Congressman Guy Ren thaler joins us".split())]
    compact = [word(i, value) for i, value in enumerate("welcome Nicolalota to the program".split())]
    assert module.find_name_cues(split, "Guy Reschenthaler")
    cue = module.find_name_cues(compact, "Nick LaLota")[0]
    assert cue["first_match"] == "compact_full_name_asr"


def test_screen_is_explicitly_non_authoritative():
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"speaker_identity_authority": False' in text
    assert '"literal_name_cue_establishes_identity": False' in text
