# -*- coding: utf-8 -*-
from pathlib import Path
import sys


FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))

from members import wikipedia_biographies as wiki


SAMPLE = """Jane Doe is a U.S. representative. She took office in 2021.

Early life and education

Doe was born in Example City. She earned a degree at State University.

Political career

Doe served in the state house. She won election to Congress in 2020.

Personal life

Doe is married and has two children.

2022 attack

Doe was injured at a public event. She returned to work two months later.

References

This paragraph must never enter a biography summary.
"""


def test_extract_summaries_are_sectioned_and_exclude_reference_text():
    result = wiki.summarize_extract(SAMPLE)
    assert result == {
        "overview": "Jane Doe is a U.S. representative. She took office in 2021.",
        "early_life": "Doe was born in Example City. She earned a degree at State University.",
        "career": "Doe served in the state house.",
        "family_personal": "Doe is married and has two children.",
        "life_experiences": "Doe was injured at a public event.",
    }
    assert all("References" not in value for value in result.values())


def test_summary_caps_keep_biographies_concise_across_many_sections():
    source = """Jane Doe represents Example State. She took office in 2021. She is a third lead sentence.

Early life

First early sentence has enough words. Second early sentence has enough words. Third early sentence has enough words.

Business career

First business-career sentence has enough words. Second business-career sentence has enough words.

Political career

First political-career sentence has enough words. Second political-career sentence has enough words.

Personal life

First personal sentence has enough words. Second personal sentence has enough words. Third personal sentence has enough words.

2019 illness

First illness sentence has enough words. Second illness sentence has enough words.

2022 attack

First attack sentence has enough words. Second attack sentence has enough words.
"""
    result = wiki.summarize_extract(source)
    assert len(wiki._sentences(result["overview"])) == 2
    assert len(wiki._sentences(result["early_life"])) == 2
    assert len(wiki._sentences(result["career"])) == 2
    assert len(wiki._sentences(result["family_personal"])) == 2
    assert len(wiki._sentences(result["life_experiences"])) == 2
    assert "Second business-career" not in result["career"]
    assert "Second illness" not in result["life_experiences"]


def test_wikitext_is_reduced_to_sectioned_prose_before_summary():
    source = """{{Infobox officeholder|name=Jane Doe}}
'''Jane Doe''' is a [[United States House of Representatives|U.S. representative]].<ref>Citation</ref>

== Personal life ==
Doe is married and has two children. {{cite web|title=Source}}

== References ==
* A source that must not appear.
"""
    plain = wiki.wikitext_to_plain(source)
    assert "Infobox" not in plain
    assert "Citation" not in plain
    assert "Personal life" in plain
    assert "Doe is married and has two children." in plain
    summary = wiki.summarize_extract(plain)
    assert summary["overview"] == "Jane Doe is a U.S. representative."
    assert summary["family_personal"] == "Doe is married and has two children."


def test_congressional_abbreviations_do_not_create_clipped_sentences():
    sentences = wiki._sentences(
        "Rep. Doe introduced H.R. 1234 to modernize the program. Sen. Roe joined later."
    )
    assert sentences == [
        "Rep. Doe introduced H.R. 1234 to modernize the program.",
        "Sen. Roe joined later.",
    ]


def test_summary_deduplicates_sentences_across_biography_sections():
    repeated = "Jane Doe served in the state house before entering Congress."
    source = """%s She took office in 2021.

Political career

%s She later chaired the finance committee.

Personal life

She is married and has two children.
""" % (repeated, repeated)
    result = wiki.summarize_extract(source)
    assert result["overview"] == repeated + " She took office in 2021."
    assert result["career"] == "She later chaired the finance committee."
    assert result["family_personal"] == "She is married and has two children."
    assert sum(value.count(repeated) for value in result.values()) == 1


def test_roster_join_has_a_bioguide_for_steve_scalise():
    rows = {row["member"]: row for row in wiki.load_roster()}
    assert rows["Steve Scalise"]["bioguide"] == "S001176"
    assert rows["Steve Scalise"]["wikipedia_id"] == "Steve Scalise"


def test_fetch_extracts_preserves_requested_titles_across_redirects(monkeypatch):
    monkeypatch.setattr(wiki, "_post_json", lambda _fields: {
        "query": {
            "normalized": [{"from": "Jane_Doe", "to": "Jane Doe"}],
            "redirects": [{"from": "Jane Doe", "to": "Jane Doe (politician)"}],
            "pages": [{"title": "Jane Doe (politician)", "extract": "Biography."}],
        }
    })
    result = wiki.fetch_extracts(["Jane_Doe"])
    assert result["Jane_Doe"]["title"] == "Jane Doe (politician)"
