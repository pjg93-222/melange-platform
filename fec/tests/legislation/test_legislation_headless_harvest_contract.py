from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_all_harvest_collectors_default_headless_with_explicit_fallback():
    sources = [
        ROOT / "fec" / "collect" / "collect_congress_advanced_pages.mjs",
        ROOT / "fec" / "enrich_congress_sponsorship_pages.mjs",
        ROOT / "fec" / "enrich_congress_text_relevance_pages.mjs",
        ROOT / "fec" / "refresh_bill_repository_headless.mjs",
    ]
    for source in sources:
        text = source.read_text(encoding="utf-8")
        assert "headless: true" in text, source
        assert 'value === "--headed"' in text, source
        assert "args.headless = false" in text, source


def test_pipeline_explicitly_requests_headless_for_every_browser_stage():
    pipeline = (
        ROOT / "fec" / "run_legislation_coverage_pipeline.ps1"
    ).read_text(encoding="utf-8")
    assert pipeline.count('"--headless"') == 5
