# -*- coding: utf-8 -*-
"""LDA outside-firm fees and in-house expenses are two series, never one total.

BUG (2026-10-09): make_lobby_network_html._load_spend shipped ``total = hired_firms +
in_house`` and the industry page, its server-rendered cards and industry-ui.js all
led with that sum ("$139.4M" for Telecom 2025).  LDA guidance has an in-house filer
include what it pays outside firms, so the sum counts those dollars twice;
fec/lobbying/sector_spend.py states the two series are never added and ruling D-064
says side by side, never summed.  Telecom 2025 is in-house $102,385,884.55 beside
outside-firm fees $37,000,500; the sum $139,386,384.55 must appear nowhere.

Three layers are pinned, each against a fixture shaped like the real sector_spend
CSV: (1) the loader's payload rows carry no total and no value equal to the sum;
(2) the server-rendered cards carry both figures and never the sum, with roster
counts from the same year as the headline figure; (3) the shipped industry-ui.js,
run under node:vm with a minimal fake DOM and fed a payload that STILL carries a
poisoned ``total`` key, renders two labelled figures, grouped bars, a sort by
in-house and two compare panels without ever showing the sum.

Plain unittest, no network, no server: python fec/tests/surfaces/test_industry_series_not_summed.py
"""
import csv
import datetime
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
import unittest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)
from surfaces import make_lobby_network_html as MLN  # has a __main__ guard; importing runs nothing

BUILDER = os.path.join(FEC, "surfaces", "make_lobby_network_html.py")
OVERLAY = os.path.join(FEC, "surfaces", "approved", "clean", "industry-ui.js")

TELECOM_2025_IN_HOUSE = 102385884.55
TELECOM_2025_FEES = 37000500.0
TELECOM_2025_SUM = TELECOM_2025_IN_HOUSE + TELECOM_2025_FEES  # 139,386,384.55

# year -> (organizations, lobbyists, outside-firm fees, in-house).  Telecom is the live
# sector_spend.csv; the other two are invented so that a sort by the sum differs from a
# sort by in-house (bank sum $150M beats telecom $139.4M, but bank in-house $80M loses).
TELECOM = {
    2021: (25, 537, 35530250.0, 95333890.0),
    2022: (23, 554, 37020000.0, 98212019.0),
    2023: (24, 533, 36448000.0, 100554138.0),
    2024: (23, 525, 36967000.0, 101098642.0),
    2025: (24, 532, TELECOM_2025_FEES, TELECOM_2025_IN_HOUSE),
    2026: (23, 479, 17588500.0, 52066170.0),
}
BANK = {
    year: (9 + i, 300 + i, 60000000.0 + 2500000.0 * i, 70000000.0 + 2500000.0 * i)
    for i, year in enumerate(range(2021, 2027))
}
BANK[2026] = (9, 290, 36000000.0, 41000000.0)
CREDIT_UNION = {
    year: (3, 40 + i, 111111.0 + 1000.0 * i, 222222.0 + 2000.0 * i)
    for i, year in enumerate(range(2021, 2027))
}
SECTORS = {"bank": BANK, "credit_union": CREDIT_UNION, "telecom": TELECOM}

FIELDS = [
    "sector", "year", "period", "is_partial_year", "distinct_organizations", "filings",
    "income_total", "expenses_total", "distinct_registrants", "distinct_named_lobbyists",
    "top_1_income_share", "top_3_income_share", "top_10_income_share", "income_hhi",
]


AS_OF = datetime.date(2026, 10, 7)  # the real data's era: Q2 is past its deadline, Q3 (Oct 20) is not


def _write_spend_fixture(directory, through=2, lone_q3=None):
    """The real CSV shape: one annual row per year, and for the partial year four quarter
    rows of which only Q1..Q<through> carry filings (the real file has Q3/Q4 all-zero,
    except crypto's single early Q3 filing worth $0, which ``lone_q3`` names a sector to copy)."""
    json_path = os.path.join(directory, "sector_spend.json")
    csv_path = os.path.join(directory, "sector_spend.csv")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump({"selected_sectors": sorted(SECTORS)}, handle)
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for sector, years in SECTORS.items():
            for year, (orgs, lobbyists, fees, in_house) in years.items():
                writer.writerow({
                    "sector": sector, "year": year, "period": "annual",
                    "is_partial_year": str(year == 2026).lower(),
                    "distinct_organizations": orgs, "filings": 2 * orgs,
                    "income_total": repr(fees), "expenses_total": repr(in_house),
                    "distinct_registrants": 1, "distinct_named_lobbyists": lobbyists,
                    "top_1_income_share": 1, "top_3_income_share": 1,
                    "top_10_income_share": 1, "income_hhi": 1,
                })
                if year != 2026:
                    continue
                for number in (1, 2, 3, 4):
                    filed = number <= through
                    stray = sector == lone_q3 and number == 3 and not filed
                    writer.writerow({
                        "sector": sector, "year": year, "period": f"Q{number}",
                        "is_partial_year": "true",
                        "distinct_organizations": orgs if filed else (1 if stray else 0),
                        "filings": orgs if filed else (1 if stray else 0),
                        "income_total": 0, "expenses_total": 0,
                        "distinct_registrants": 1 if filed else 0,
                        "distinct_named_lobbyists": lobbyists if filed else 0,
                        "top_1_income_share": 0, "top_3_income_share": 0,
                        "top_10_income_share": 0, "income_hhi": 0,
                    })
    return json_path, csv_path


def _numbers(value):
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield float(value)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _numbers(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _numbers(item)


def _strip_python_comments(path):
    with open(path, "rb") as handle:
        tokens = list(tokenize.tokenize(handle.readline))
    return "".join(
        token.string for token in tokens if token.type != tokenize.COMMENT
    )


def _strip_js_comments(path):
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    return re.sub(r"(?m)(?<![:'\"\\])//.*$", "", text)


def _summed_forms():
    """Every way the page could print the forbidden sum, per sector-year."""
    forms = set()
    for years in SECTORS.values():
        for _orgs, _lobbyists, fees, in_house in years.values():
            total = fees + in_house
            forms.add(MLN._money(total))
            forms.add(MLN._money(total, tenths=True))
            forms.add(f"{total:,.0f}")
            forms.add(f"{total:,.2f}")
    return forms


class SpendRowsCarryNoTotal(unittest.TestCase):
    """(1) _load_spend: the payload rows."""

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as directory:
            json_path, csv_path = _write_spend_fixture(directory)
            cls.spend, cls.tier_meta, _receipt = MLN._load_spend(
                json_path, csv_path, ["credit_union"], AS_OF
            )

    def test_the_two_series_are_loaded_exactly_and_separately(self):
        row = self.spend["telecom"][4]
        self.assertEqual(2025, row["year"])
        self.assertEqual(TELECOM_2025_IN_HOUSE, row["in_house"])
        self.assertEqual(TELECOM_2025_FEES, row["hired_firms"])
        self.assertEqual((24, 532), (row["orgs"], row["lobbyists"]))
        self.assertTrue(self.spend["telecom"][5]["partial"])

    def test_the_partial_year_row_records_the_last_quarter_with_filings(self):
        for sector, rows in self.spend.items():
            self.assertEqual("Q2", rows[5]["through_quarter"], sector)  # Q3/Q4 exist but are all-zero
            for row in rows[:5]:
                self.assertNotIn("through_quarter", row, f"{sector} {row['year']} is a complete year")

    def test_no_row_has_a_total_key(self):
        for sector, rows in self.spend.items():
            for row in rows:
                self.assertNotIn("total", row, f"{sector} {row['year']} carries a total")

    def test_no_value_in_any_row_equals_fees_plus_in_house(self):
        for sector, rows in self.spend.items():
            for row in rows:
                forbidden = row["hired_firms"] + row["in_house"]
                for value in _numbers(row):
                    self.assertGreater(
                        abs(value - forbidden), 0.005,
                        f"{sector} {row['year']}: a payload value equals fees + in-house ({forbidden:,.2f})",
                    )

    def test_the_loader_source_does_not_add_the_series(self):
        source = _strip_python_comments(BUILDER)
        self.assertIsNone(re.search(r"hired_firms\s*\+\s*in_house", source))
        self.assertIsNone(re.search(r"""["']total["']\s*:\s*hired_firms""", source))


class PartialYearQuarterComesFromTheQuarterRows(unittest.TestCase):
    """'through Q<n>' is derived from the data, never typed into the page."""

    # A quarter is covered only when its LD-2 deadline (Q1 Apr 20, Q2 Jul 20, Q3 Oct 20, Q4 Jan 20
    # of the next year) is on or before the data's own as-of date AND it has a filing.
    def load(self, through, as_of=AS_OF, lone_q3=None):
        with tempfile.TemporaryDirectory() as directory:
            json_path, csv_path = _write_spend_fixture(directory, through=through, lone_q3=lone_q3)
            return MLN._load_spend(json_path, csv_path, ["credit_union"], as_of)

    def test_q1_to_q3_filings_after_the_q3_deadline_render_through_q3(self):
        spend, tier_meta, _receipt = self.load(3, as_of=datetime.date(2026, 10, 21))
        self.assertEqual({"Q3"}, {rows[5]["through_quarter"] for rows in spend.values()})
        cards = MLN._industry_cards(spend, tier_meta)
        self.assertEqual(3, cards.count("PARTIAL YEAR through Q3 · in-house"))
        self.assertNotIn("through Q2", cards)

    def test_q3_filings_before_the_q3_deadline_do_not_extend_coverage(self):
        spend, _tier_meta, _receipt = self.load(3, as_of=datetime.date(2026, 10, 7))
        self.assertEqual({"Q2"}, {rows[5]["through_quarter"] for rows in spend.values()})

    def test_the_deadline_day_itself_counts(self):
        spend, _tier_meta, _receipt = self.load(3, as_of=datetime.date(2026, 10, 20))
        self.assertEqual({"Q3"}, {rows[5]["through_quarter"] for rows in spend.values()})
        spend, _tier_meta, _receipt = self.load(3, as_of=datetime.date(2026, 10, 19))
        self.assertEqual({"Q2"}, {rows[5]["through_quarter"] for rows in spend.values()})

    def test_the_real_crypto_shape_one_early_zero_dollar_q3_filing_is_still_q2(self):
        # sector_spend.csv 2026: crypto Q3 has 1 filing, 1 organization, $0; data as of 2026-10-07
        spend, tier_meta, _receipt = self.load(2, lone_q3="bank")
        self.assertEqual("Q2", spend["bank"][5]["through_quarter"])
        self.assertEqual({"Q2"}, {rows[5]["through_quarter"] for rows in spend.values()})
        cards = MLN._industry_cards(spend, tier_meta)
        self.assertEqual(3, cards.count("PARTIAL YEAR through Q2 · in-house"))
        self.assertNotIn("through Q3", cards)

    def test_once_the_q3_deadline_has_passed_that_lone_filing_does_count(self):
        spend, _tier_meta, _receipt = self.load(2, as_of=datetime.date(2026, 10, 21), lone_q3="bank")
        self.assertEqual("Q3", spend["bank"][5]["through_quarter"])
        self.assertEqual({"Q2"}, {spend[s][5]["through_quarter"] for s in ("credit_union", "telecom")})

    def test_a_quarter_before_its_deadline_is_not_covered(self):
        spend, _tier_meta, _receipt = self.load(2, as_of=datetime.date(2026, 7, 1))
        self.assertEqual({"Q1"}, {rows[5]["through_quarter"] for rows in spend.values()})

    def test_the_real_data_shape_still_renders_through_q2(self):
        spend, tier_meta, _receipt = self.load(2)
        cards = MLN._industry_cards(spend, tier_meta)
        self.assertEqual(3, cards.count("PARTIAL YEAR through Q2 · in-house"))
        self.assertNotIn("through Q3", cards)

    def test_a_partial_year_with_no_covered_quarter_is_refused_not_guessed(self):
        with self.assertRaises(MLN.IngestError) as caught:
            self.load(0)  # no quarter row has any filing
        self.assertIn("quarter", str(caught.exception))
        with self.assertRaises(MLN.IngestError):
            self.load(2, as_of=datetime.date(2026, 4, 1))  # filings exist but no deadline has passed

    def test_the_page_wide_label_is_the_minimum_across_sectors(self):
        industries = [{"spend": [{"year": 2026, "through_quarter": q}]} for q in ("Q3", "Q2", "Q3")]
        self.assertEqual("Q2", MLN._page_through_quarter(industries))
        self.assertEqual("Q3", MLN._page_through_quarter(industries[:1]))

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_the_as_of_chip_script_takes_the_minimum_too(self):
        with open(BUILDER, encoding="utf-8") as handle:
            source = handle.read()
        line = re.search(r"const THROUGH_QUARTER=.*?;\r?\n", source, re.S).group(0)
        script = ("const PARTIAL_YEAR=2026; const DATA=" + json.dumps({"industries": [
            {"spend": [{"year": 2026, "through_quarter": q}]} for q in ("Q3", "Q2", "Q3")]}) + ";"
            + line + "process.stdout.write(THROUGH_QUARTER);")
        run = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, timeout=60)
        self.assertEqual(("Q2", 0), (run.stdout.decode(), run.returncode), run.stderr.decode()[-400:])

    def test_the_as_of_date_comes_from_the_sweep_receipt_not_the_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "fullscope_receipts.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"scope_sweeps": {"2026": {"sweep_through_dt_posted": "2026-08-01T11:57:13-04:00"}}}, handle)
            self.assertEqual(datetime.date(2026, 8, 1), MLN._sweep_as_of(path))
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"scope_sweeps": {"2025": {}}}, handle)
            with self.assertRaises(MLN.IngestError):
                MLN._sweep_as_of(path)

    def test_the_page_source_no_longer_types_the_quarter(self):
        source = _strip_python_comments(BUILDER)
        self.assertNotRegex(source, r"PARTIAL YEAR through Q\d")
        self.assertNotRegex(source, r"partial year through Q\d")


class ServerRenderedCardsNeverShowTheSum(unittest.TestCase):
    """(2) _industry_cards: the legacy card markup."""

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as directory:
            json_path, csv_path = _write_spend_fixture(directory)
            spend, tier_meta, _receipt = MLN._load_spend(json_path, csv_path, ["credit_union"], AS_OF)
        cls.document = MLN._industry_cards(spend, tier_meta)
        cards = re.split(r"(?=<button class=industry-card)", cls.document)
        cls.cards = {
            re.search(r'data-sector="([^"]+)"', card).group(1): card
            for card in cards if card.startswith("<button")
        }

    def test_telecom_carries_both_separate_values(self):
        card = self.cards["telecom"]
        self.assertEqual("$102.4M", MLN._money(TELECOM_2025_IN_HOUSE))
        self.assertEqual("$37M", MLN._money(TELECOM_2025_FEES))  # bar labels and tooltips drop a trailing .0
        self.assertEqual("$37.0M", MLN._money(TELECOM_2025_FEES, tenths=True))  # headline figures keep it
        self.assertIn("2025 in-house lobbying $102.4M", card)
        self.assertIn("2025 fees reported by outside firms $37.0M", card)
        self.assertIn(">$102.4M</strong>", card)
        self.assertIn(">$37.0M</strong>", card)

    def test_the_sum_appears_nowhere_in_any_card(self):
        self.assertEqual("$139.4M", MLN._money(TELECOM_2025_SUM))
        for form in sorted(_summed_forms()):
            self.assertNotIn(form, self.document, f"the summed figure {form} is rendered")
        self.assertNotIn("139.4", self.document)
        self.assertNotIn("139,386", self.document)
        self.assertNotIn("otal lobbying spend", self.document)

    def test_roster_counts_come_from_the_headline_year_and_name_it(self):
        card = self.cards["telecom"]
        self.assertIn("2025: 24 organizations · 532 named lobbyists", card)
        self.assertNotIn("23 organizations", card)  # 2026 H1 roster, not the headline year
        self.assertNotIn("479 named lobbyists", card)

    def test_the_partial_year_is_one_labelled_line_with_both_series(self):
        card = self.cards["telecom"]
        self.assertIn(
            "2026 · PARTIAL YEAR through Q2 · in-house $52.1M · outside-firm fees $17.6M",
            card,
        )

    def test_the_two_series_are_peers_and_neither_is_a_headline_total(self):
        card = self.cards["telecom"]
        self.assertEqual(2, card.count("<svg class=money-spark"))
        self.assertNotIn("headline-series", card)
        self.assertNotIn("data-series=total", card)

    def test_the_credit_union_card_does_not_show_the_sum_either(self):
        card = self.cards["credit_union"]
        self.assertIn(MLN._money(230222.0), card)  # 2025 in-house
        self.assertIn(MLN._money(115111.0), card)  # 2025 fees
        self.assertNotIn(MLN._money(230222.0 + 115111.0), card)


class BankTierStackNamesTheHeadlineYear(unittest.TestCase):
    """The bank card's per-tier stacks report the same year as the series above them."""

    @classmethod
    def setUpClass(cls):
        order = ["big", "community", "mixed", "unclassified"]
        evidence = {"share": 0.0, "level": "documented", "label": "Documented", "total": 1.0}
        with tempfile.TemporaryDirectory() as directory:
            json_path, csv_path = _write_spend_fixture(directory)
            spend, _tier_meta, _receipt = MLN._load_spend(json_path, csv_path, ["credit_union"], AS_OF)
        for row in spend["bank"]:  # tiers split each series 40/30/20/10 and sum back to the sector value
            row["tiers"] = {
                name: {
                    "hired_firms": row["hired_firms"] * share, "in_house": row["in_house"] * share,
                    "orgs": 1, "evidence": {"hired_firms": dict(evidence), "in_house": dict(evidence)},
                }
                for name, share in zip(order, (0.4, 0.3, 0.2, 0.1))
            }
        document = MLN._industry_cards(spend, {"sectors": ["bank"], "order": order})
        cls.card = next(c for c in re.split(r"(?=<button class=industry-card)", document)
                        if 'data-sector="bank"' in c)

    def test_series_and_tier_stack_values_are_all_the_last_complete_year(self):
        values = re.findall(r"<strong class=seriesvalue>([^<]+)</strong>", self.card)
        # in-house, outside-firm fees, "Outside-firm fees by tier", "In-house by tier" -- all 2025
        self.assertEqual(["$80.0M", "$70.0M", "$70.0M", "$80.0M"], values)
        self.assertNotIn("$41.0M", values)  # 2026 partial in-house
        self.assertNotIn("$36.0M", values)  # 2026 partial fees

    def test_last_complete_year_is_found_by_the_partial_flag_not_by_position(self):
        rows = [{"year": y, "partial": y == 2027} for y in range(2021, 2028)]
        self.assertEqual(2026, MLN._last_complete(rows)["year"])
        self.assertEqual(2025, MLN._last_complete(rows[:5] + [{"year": 2026, "partial": True}])["year"])


JS_DRIVER = r"""
'use strict';
const fs = require('fs'), vm = require('vm');
const [src, dataPath] = process.argv.slice(2);
const data = JSON.parse(fs.readFileSync(dataPath, 'utf8'));
let code = fs.readFileSync(src, 'utf8');
const tail = /\r?\n\s*mount\(\);\s*\r?\n\}\)\(\);\s*$/;
if (!tail.test(code)) throw new Error('industry-ui.js no longer ends with mount(); the harness cannot expose the overlay state');
code = code.replace(tail, '\n  window.__t = { S, setEls(v) { els = v; }, renderFocus, renderOthers };\n})();');
const mk = (extra = {}) => Object.assign({
  dataset: {}, style: { setProperty() {} }, innerHTML: '', textContent: '', hidden: false, clientWidth: 0,
  setAttribute() {}, removeAttribute() {}, getAttribute() { return null; }, appendChild() {}, remove() {},
  classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
  querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {}, closest() { return null; },
}, extra);
const chartBox = mk({ clientWidth: 800 }), partial = mk({ textContent: '2026 \u00b7 PARTIAL YEAR through Q2' });
const doc = {
  documentElement: mk(), body: mk(),
  getElementById: id => (id === 'industries' ? mk() : id === 'ind-chart' ? chartBox : null),
  querySelector: sel => (String(sel).includes('.partial') ? partial : null),
  querySelectorAll: () => [], createElement: () => mk(),
};
const sandbox = {
  document: doc, location: { search: '', hash: '', href: 'http://localhost/' }, URLSearchParams, console,
  getComputedStyle: () => ({ backgroundColor: 'rgb(10, 20, 30)' }), requestAnimationFrame: () => 0, addEventListener() {}, setTimeout,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(code, sandbox, { filename: 'industry-ui.js' });
const T = sandbox.__t;
let sparkBoxes = [];
const cards = mk(), focus = mk(), compareAll = mk();
cards.querySelectorAll = sel => {
  sparkBoxes = sel === '.ind-spark'
    ? [...cards.innerHTML.matchAll(/class="ind-spark" data-sector="([^"]+)"/g)].map(m => mk({ dataset: { sector: m[1] }, clientWidth: 300 }))
    : [];
  return sparkBoxes;
};
T.setEls({ focus, cards, compareAll, tabs: [] });
T.S.data = data;
const out = {};
T.S.focus = 'telecom'; T.S.compare = false;
T.renderFocus();
out.focus = focus.innerHTML;
out.chart = chartBox.innerHTML;
T.S.compare = true;
T.renderFocus();
out.compareFocus = focus.innerHTML;
out.compareChart = chartBox.innerHTML;
T.S.compare = false; chartBox.clientWidth = 477;  // narrow enough that neighbouring bar labels would touch
T.renderFocus();
out.narrowChart = chartBox.innerHTML;
chartBox.clientWidth = 800;
T.S.focus = 'credit_union';
T.renderFocus(); T.renderOthers();
out.cards = cards.innerHTML;
out.sparks = sparkBoxes.map(box => ({ sector: box.dataset.sector, html: box.innerHTML }));
process.stdout.write(JSON.stringify(out));
"""


def _overlay_payload(through="Q2"):
    """industries[].spend as the page ships it, plus the stale ``total`` key a cached page could still carry.
    ``through`` is the partial row's through_quarter; None leaves it off, as an older payload would."""
    industries = []
    for sector, years in SECTORS.items():
        spend = []
        for year, (orgs, lobbyists, fees, in_house) in sorted(years.items()):
            row = {
                "year": year, "partial": year == 2026, "orgs": orgs, "filings": 2 * orgs,
                "hired_firms": fees, "in_house": in_house, "registrants": 1,
                "lobbyists": lobbyists, "total": fees + in_house,
            }
            if year == 2026 and through:
                row["through_quarter"] = through
            spend.append(row)
        industries.append({"sector": sector, "spend": spend})
    return {"industries": industries}


def _run_overlay(payload):
    with tempfile.TemporaryDirectory() as directory:
        driver = os.path.join(directory, "driver.cjs")
        data = os.path.join(directory, "payload.json")
        with open(driver, "w", encoding="utf-8") as handle:
            handle.write(JS_DRIVER)
        with open(data, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        run = subprocess.run(
            [shutil.which("node"), driver, OVERLAY, data], capture_output=True, timeout=120
        )
    if run.returncode != 0:
        raise AssertionError("node driver failed:\n" + run.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(run.stdout.decode("utf-8"))


def _nice_top(maximum, target=6):
    """The overlay's nice(): the axis top for a single-series maximum."""
    raw = maximum / target
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw * (1 - 1e-9))
    return math.ceil(maximum / step - 1e-9) * step


@unittest.skipUnless(shutil.which("node"), "node is not installed; the overlay layer cannot run")
class OverlayNeverShowsTheSum(unittest.TestCase):
    """(3) industry-ui.js rendered under node:vm."""

    @classmethod
    def setUpClass(cls):
        cls.out = _run_overlay(_overlay_payload())  # real-data shape: partial year through Q2, page text says Q2 too
        cls.out_q3 = _run_overlay(_overlay_payload("Q3"))  # the row says Q3 while the fake page text still says Q2
        cls.out_old = _run_overlay(_overlay_payload(None))  # an older payload with no through_quarter

    def every_rendered_string(self):
        out = self.out
        yield from (out[key] for key in ("focus", "chart", "compareFocus", "compareChart", "cards"))
        yield from (item["html"] for item in out["sparks"])

    def test_the_sum_is_never_rendered_anywhere(self):
        for form in sorted(_summed_forms()):
            for text in self.every_rendered_string():
                self.assertNotIn(form, text, f"the summed figure {form} is rendered")
        for text in self.every_rendered_string():
            self.assertNotIn("139.4", text)
            self.assertNotIn("otal lobbying spend", text)

    def test_the_focus_shows_two_labelled_figures_for_the_last_complete_year(self):
        focus = self.out["focus"]
        self.assertIn(
            ">$102.4M</strong><span class=\"ind-figlabel\">2025 in-house lobbying</span>", focus
        )
        self.assertIn(
            ">$37.0M</strong><span class=\"ind-figlabel\">2025 fees reported by outside firms</span>",
            focus,
        )
        self.assertIn(">$37.0M</strong>", focus)
        self.assertEqual(2, focus.count("class=\"ind-big\""))

    def test_roster_counts_are_the_headline_year_not_the_partial_year(self):
        focus = self.out["focus"]
        self.assertIn("2025: 24 organizations · 532 named lobbyists", focus)
        self.assertNotIn("23 organizations", focus)
        self.assertNotIn("479 named lobbyists", focus)

    def test_the_partial_year_is_one_smaller_line_naming_the_quarter(self):
        focus = self.out["focus"]
        self.assertIn("2026 through Q2: in-house $52.1M · outside-firm fees $17.6M", focus)

    def test_the_quarter_is_read_from_the_row_before_the_page_text(self):
        # the fake page still says "through Q2"; the row says Q3, so only the row can produce Q3
        self.assertIn("2026 through Q3: in-house $52.1M · outside-firm fees $17.6M", self.out_q3["focus"])
        self.assertNotIn("through Q2", self.out_q3["focus"])

    def test_an_older_payload_without_through_quarter_falls_back_to_the_page_text(self):
        self.assertIn("2026 through Q2: in-house $52.1M · outside-firm fees $17.6M", self.out_old["focus"])

    def test_the_legend_names_the_two_series(self):
        focus = self.out["focus"]
        self.assertIn("In-house lobbying", focus)
        self.assertIn("Fees reported by outside firms", focus)

    def test_bars_are_grouped_side_by_side_on_a_single_series_axis(self):
        chart = self.out["chart"]
        bars = re.findall(
            r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"[^>]*><title>([^<]*)</title></rect>',
            chart,
        )
        self.assertEqual(12, len(bars), "two bars for each of the six years")
        by_title = {title: tuple(float(v) for v in rest) for *rest, title in bars}

        def bar(title):
            self.assertIn(title, by_title, f"no bar titled {title!r} in {sorted(by_title)}")
            return by_title[title]

        for year in (2021, 2022, 2023, 2024, 2025, 2026):
            suffix = " partial" if year == 2026 else ""
            left = bar(f"{year}{suffix} · In-house lobbying · " + MLN._money(TELECOM[year][3]))
            right = bar(f"{year}{suffix} · Fees reported by outside firms · "
                        + MLN._money(TELECOM[year][2]))
            self.assertLessEqual(left[0] + left[2], right[0] + 0.01, f"{year}: bars overlap, so they are stacked")
            self.assertAlmostEqual(left[1] + left[3], right[1] + right[3], places=1,
                                   msg=f"{year}: bars do not share a baseline")
        # 2025: heights are in the ratio of the two raw values, on one axis
        in_house_bar = bar("2025 · In-house lobbying · $102.4M")
        fees_bar = bar("2025 · Fees reported by outside firms · $37M")
        self.assertAlmostEqual(
            in_house_bar[3] / fees_bar[3], TELECOM_2025_IN_HOUSE / TELECOM_2025_FEES, delta=0.02
        )
        labels = re.findall(r'<text class="ind-lab"[^>]*>([^<]*)</text>', chart)
        self.assertIn("$102.4M", labels)
        self.assertIn("$37M", labels)
        self.assertEqual(12, len(labels), "each bar has its own value label")
        ticks = re.findall(r'<text class="ind-yt"[^>]*>([^<]*)</text>', chart)
        self.assertEqual("$120M", ticks[-1], "the axis top is scaled to the larger single series")

    def test_neighbouring_bar_labels_never_overprint(self):
        # 477px wide: a 6-character label (~37px at 6.2px a character) is wider than the 34px between
        # bar centres, so the fit rule must drop it rather than let it touch the next label.
        labels = [(float(x), text) for x, text in re.findall(
            r'<text class="ind-lab" x="([\d.]+)"[^>]*>([^<]*)</text>', self.out["narrowChart"])]
        self.assertTrue(labels, "the narrow chart dropped every label, so this proves nothing")
        labels.sort()
        spacing = {round(b[0] - a[0], 1) for a, b in zip(labels, labels[1:])}
        for (x1, t1), (x2, t2) in zip(labels, labels[1:]):
            if x2 - x1 < 40:  # bars of the same year sit one bar-plus-gap apart
                self.assertGreaterEqual(x2 - x1, (len(t1) + len(t2)) * 6.2 / 2,
                                        f"labels {t1!r} and {t2!r} overprint ({sorted(spacing)})")
        self.assertNotIn("$95.3M", [t for _, t in labels])  # 2021 in-house: too wide for its slot

    def test_other_industries_are_sorted_by_last_complete_year_in_house(self):
        cards = self.out["cards"]
        self.assertIn("Sorted by 2025 in-house lobbying", cards)
        order = re.findall(r'<article class="ind-card[^"]*" data-sector="([^"]+)"', cards)
        self.assertEqual(["telecom", "bank"], order)  # the sum would put bank ($150M) first
        telecom = cards.split('data-sector="telecom"', 1)[1].split("</article>", 1)[0]
        self.assertIn("$102.4M", telecom)
        self.assertIn(">$102.4M</strong>", telecom)
        self.assertIn(">$37.0M outside-firm fees<", telecom)
        bank = cards.split('data-sector="bank"', 1)[1].split("</article>", 1)[0]
        self.assertIn(">$80.0M</strong>", bank)
        self.assertIn(">$70.0M outside-firm fees<", bank)

    def test_each_card_has_two_labelled_sparklines_one_per_series(self):
        self.assertEqual({"telecom", "bank"}, {item["sector"] for item in self.out["sparks"]})
        for item in self.out["sparks"]:
            html_ = item["html"]
            self.assertEqual(2, html_.count("<svg"), item["sector"])
            self.assertIn("In-house", html_)
            self.assertIn("Outside-firm fees", html_)
            self.assertIn("in-house lobbying 2021–2025", html_)
            self.assertIn("fees reported by outside firms 2021–2025", html_)
        telecom = next(item["html"] for item in self.out["sparks"] if item["sector"] == "telecom")
        self.assertIn("2025 $102.4M", telecom)
        self.assertIn("2025 $37M", telecom)

    def test_compare_all_is_two_titled_panels_one_line_per_industry(self):
        chart = self.out["compareChart"]
        self.assertEqual(2, chart.count("<svg"))
        self.assertEqual(3 * 2, chart.count("<polyline"), "one line per industry in each panel")
        self.assertIn("In-house lobbying</", chart)
        self.assertIn("Fees reported by outside firms</", chart)
        self.assertNotIn("otal", chart)
        self.assertEqual(2, chart.count('<figure class="ind-panel">'))
        self.assertEqual(2, len(re.findall(r'<figure class="ind-panel"><figcaption[^>]*>[^<]*</figcaption><svg', chart)),
                         "each panel's svg sits directly in its figure")

    def test_each_compare_panel_axis_tops_out_at_its_own_single_series_maximum(self):
        panels = {
            re.search(r"<figcaption[^>]*>([^<]*)</figcaption>", p).group(1): p
            for p in self.out["compareChart"].split('<figure class="ind-panel">')[1:]
        }
        self.assertEqual({"In-house lobbying", "Fees reported by outside firms"}, set(panels))
        fixture_max = {  # larger single series, any industry, any year -- never fees + in-house
            "In-house lobbying": max(v[3] for years in SECTORS.values() for v in years.values()),
            "Fees reported by outside firms": max(v[2] for years in SECTORS.values() for v in years.values()),
        }
        summed_max = max(v[2] + v[3] for years in SECTORS.values() for v in years.values())
        for name, panel in panels.items():
            ticks = re.findall(r'<text class="ind-yt"[^>]*>([^<]*)</text>', panel)
            expected = MLN._money(_nice_top(fixture_max[name]))
            self.assertEqual(expected, ticks[-1], f"{name}: axis top")
            self.assertNotEqual(expected, MLN._money(_nice_top(summed_max)),
                                "fixture cannot tell a sum-scaled axis from a single-series one")
        self.assertEqual(("$120M", "$80M"),
                         tuple(re.findall(r'<text class="ind-yt"[^>]*>([^<]*)</text>', panels[n])[-1]
                               for n in ("In-house lobbying", "Fees reported by outside firms")))

    def test_card_sr_text_names_the_year_and_the_in_house_figure_only(self):
        cards = self.out["cards"]
        telecom = cards.split('data-sector="telecom"', 1)[1].split("</article>", 1)[0]
        self.assertIn('</div><span class="ind-sr">2025 in-house lobbying</span>', telecom)
        self.assertNotIn("fees reported by outside firms", telecom.split('<span class="ind-sr">', 1)[1].split("</span>", 1)[0])
        self.assertEqual(1, telecom.count("$37.0M"), "the fee is stated once on the card")

    def test_compare_legend_names_the_industries(self):
        compare_focus = self.out["compareFocus"]
        for name in ("Bank", "Credit Union", "Telecom"):
            self.assertIn(name, compare_focus)

    def test_the_overlay_source_never_reads_a_spend_total(self):
        source = _strip_js_comments(OVERLAY)
        self.assertIsNone(re.search(r"\b(?:row|done|part|latest|item)\.total\b", source))
        self.assertIsNone(re.search(r"hired_firms\)?\s*\|\|\s*0\)\s*\+", source))
        self.assertNotIn("total lobbying spend", source.lower())


class SourceWording(unittest.TestCase):
    """Source-level pins (comments stripped first) that need no node."""

    def test_tier_chart_titles_name_the_outside_firm_fees(self):
        builder = _strip_python_comments(BUILDER)
        overlay = _strip_js_comments(OVERLAY)
        for text in (builder, overlay):
            self.assertNotRegex(text, r"Hired firms by (?:bank )?tier")
            self.assertIn("In-house by", text)
        self.assertEqual(2, builder.count("Outside-firm fees by tier"))  # card tier stack + the roster page heading
        self.assertEqual(1, builder.count("Outside-firm fees by bank tier"))  # the roster page's tierChart title
        self.assertEqual(2, overlay.count("Outside-firm fees by tier"))  # the title passed to tierChart and the h4 beside it

    def test_no_visible_text_still_calls_the_series_hired_firms(self):
        builder = _strip_python_comments(BUILDER)
        self.assertNotIn("Hired firms", builder)
        self.assertNotIn("Hired-firm", builder)
        self.assertIn("class=num>Outside-firm fees</th>", builder)  # roster/tier table column header
        self.assertIn("class=num>In-house</th>", builder)
        self.assertTrue(MLN.TIER_SERIES_NOTE.startswith("Outside-firm fees and in-house spend"), MLN.TIER_SERIES_NOTE)
        self.assertNotIn("Hired", MLN.TIER_SERIES_NOTE)
        self.assertNotIn("Hired", _strip_js_comments(OVERLAY))

    def test_compare_panel_svgs_keep_responsive_sizing(self):
        with open(os.path.join(os.path.dirname(OVERLAY), "industry-ui.css"), encoding="utf-8") as handle:
            css = re.sub(r"/\*[\s\S]*?\*/", "", handle.read())
        self.assertIn(".ind-panel>svg{display:block;width:100%;overflow:visible}", css.replace(" ", ""))


if __name__ == "__main__":
    unittest.main()
