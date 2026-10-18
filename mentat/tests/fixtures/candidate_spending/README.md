# Candidate spending fixtures

Results in the exact shape `committee_activity_reader.read_campaign_spending(...)` returns (the crawler lane's contract,
`app/candidate_spending.py` is the adapter). They are test and screenshot stand-ins only: the console reads them solely
when `MENTAT_CANDIDATE_SPENDING_FIXTURE` points here. Production never sets it and never reads this directory.

| File | What it is |
| --- | --- |
| `H0KY06104-2024.json` | Andy Barr 2023-24: a hand-picked PARTIAL SAMPLE of his real Schedule B rows (the payees named in the brief). `coverage_status: partial`, `totals: []`, so coverage is unknown. Not the whole committee. |
| `H8ZZ00001-2024.json` | SYNTHETIC. Two committees; a repeated `sub_id`, memo parents (one resolves, one does not), a VOID and its reissue, a category that nets below zero, transfers between the candidate's own committees, a reimbursement to the candidate, and low coverage (47%). |
| `crawler/read_campaign_spending_fixture.json` | The crawler lane's contract fixture v1 (SYNTHETIC: complete, partial, pending, not_seeded, mapping_unknown), copied unchanged. |
| `crawler/read_campaign_spending_fixture.schema.json` | Its JSON Schema (`#/$defs/Result` is the return value). |

Provenance of the `crawler/` copies (from the crawler lane, D-072 stage):

- source: `C:/Users/pgoff/Documents/Codex/2026-10-14/so/outputs/mentat-audit/D-072-stage/fixtures/` (nothing at runtime refers to it)
- `read_campaign_spending_fixture.json` sha256 `c6a51fe6018691372f2cc427d0a37523003f22263fc91202990fc3eae49523f7`
- `read_campaign_spending_fixture.schema.json` sha256 `def754dc60bd42a0323cf0ab56699db5fe76e52345854ef958caf6a8e969cc05`

`tests/test_candidate_spending.py` checks both hashes, validates every fixture here against the schema, and feeds the
crawler's examples unchanged through the adapter.
