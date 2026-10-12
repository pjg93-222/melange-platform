# -*- coding: utf-8 -*-
"""Behavioural tests for the Member Desk coalition builder's "suggested next cosponsors".

BUG (product owner, 2026-08-02): "for the next recommender in the coalition builder, it always
asks Andy Barr, that is bad".

Root cause: the panel ranked the ABSOLUTE sum of tie probabilities into the coalition
(sc[v] += p for every coalition member touching v).  That is a modular, monotone NON-DECREASING
function of the coalition, not a marginal gain -- adding a hub's neighbours RAISED the hub's score
instead of retiring it.  With Tom Emmer (171 ties) and Andy Barr (164) holding the two highest
degrees out of 2,555 edges across 533 members, the same handful of hubs pinned to the top of every
list.  The recommender now ranks the marginal increase in expected coalition reach, which is
submodular, so a member's value falls as the set already covers their neighbourhood.

These tests execute the SHIPPED JavaScript (extracted from the template) under node against a
synthetic graph, so they check behaviour rather than the presence of source strings.  They are
skipped, not failed, when node is unavailable.

HARNESS CONTRACT (2026-08-03).  The harness used to carry a two-line slice plus five functions
named one at a time, and that shape broke TWICE while the shipped code was perfectly healthy:
  * the af1160d merge brought in the issue-adaptive edge probability, and adaptiveEdgeProbability()
    is declared far above the named five -- every node test died with "adaptiveEdgeProbability is
    not defined" the moment coverage() was called
  * the cosponsor-propensity basis added top-level declarations reading the bundle global `D`
    (CPR/CP_COS/CP_SPO/CP_AREA/CP_MIX, SELJ/SEL_S/SEL_O/SEL_G, PRICING/P_CAP/P_PRICE) plus a new
    shared scorer basisScores() -- every node test died at load with "D is not defined"
Both were harness drift, not defects: the source guards below stayed green throughout and the
scoring path was verified under node against the real 533-member bundle on 2026-08-03.

So the harness now carries two CONTIGUOUS slices of the template rather than naming functions.  A
slice picks up whatever lands between its markers, which is exactly the failure mode that recurs.
The harness supplies only what the BROWSER would have supplied -- the bundle global `D`, the
member/edge arrays, and the four interactive selections.  Nothing is stubbed: DIN/DOUT/HAS_DIR,
adaptiveEdgeProbability(), memberHasCommittee(), basisScores() and the whole scoring core are the
shipped source, so a test can populate `D` and exercise a real artifact-backed basis.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

FEC = Path(__file__).resolve().parents[2]
TEMPLATE = FEC / "surfaces" / "templates" / "make_member_desk_00.tpl"
NODE = shutil.which("node")


# The two pure-logic runs of the template the harness executes, as (first line kept, first line
# dropped).  Both were checked to contain no document/localStorage/window/$()/Math.random/Date.now
# reference, which is what makes them runnable outside a browser.
# Each entry is (start candidates, end).  The FIRST candidate present in the template wins, which
# is how one harness spans template vintages: the directed cosponsorship graph arrived on
# 2026-08-03, so a checkout that predates it starts the same slice at the influence policy instead
# and simply has no DIN/DOUT to carry.
SHIPPED_SLICES = (
    # the directed cosponsorship graph (DIN/DOUT/HAS_DIR) when the template has one, then the
    # interactive influence policy, the issue-adaptive edge probability every scorer calls, and
    # the committee filter helpers.
    (("const DIN={},DOUT={};", "const IP=D.influence_policy"), "const STCOL="),
    # the entire coalition-builder scoring core in shipped order: coverage, marginalGain, the
    # stratum nulls, the cosponsor-propensity basis, specificResidual, the cbBasis default, the
    # pricing block, basisScores, suggest, reachEstimate.  Stops where DOM rendering begins.
    (("function coverage(",), "function renderCoal("),
)


def shipped_slice(source: str, starts, end: str) -> str:
    """Carry the template run from the first present start marker up to `end`, verbatim.

    A marker that appears twice would slice silently and wrongly, which is the same class of quiet
    drift this harness has already been bitten by, so ambiguity raises rather than guesses.  A
    start candidate that is simply ABSENT is not an error -- that is how the slice tolerates a
    template vintage without the block -- but running out of candidates is.
    """
    if source.count(end) != 1:
        raise AssertionError(
            "harness end marker %r appears %d times in the template, expected exactly 1 "
            "-- the slice boundaries need re-anchoring" % (end, source.count(end)))
    for start in starts:
        found = source.count(start)
        if found > 1:
            raise AssertionError(
                "harness start marker %r appears %d times in the template, expected at most 1 "
                "-- the slice boundaries need re-anchoring" % (start, found))
        if found == 1:
            return source[source.index(start):source.index(end)]
    raise AssertionError(
        "none of the harness start markers %r are in the template -- the slice boundaries need "
        "re-anchoring" % (list(starts),))


class SyntheticGraph:
    """A hub-and-spoke graph that reproduces the real failure shape.

    Member 0 ("Hub") is tied to everyone -- the Andy Barr / Tom Emmer role.  Members 1..N are tied
    to the hub and to a small local cluster.  Under an absolute tie-sum score the hub wins forever;
    under a marginal-reach score the hub is correct FIRST and then must fall away.
    """

    N = 24
    HUB = 0
    SIGNER = 20         # has actually cosponsored the seed coalition's bills
    SPONSOR_HUB = 21    # sponsors constantly, signs nothing -- the role the basis must NOT reward
    SELECTIVE = 22      # signs rarely, but almost always on Area A

    @classmethod
    def edges(cls):
        rows = []
        for v in range(1, cls.N):
            rows.append([cls.HUB, v, 0.45])                 # hub touches everyone strongly
        for v in range(1, cls.N - 1):
            rows.append([v, v + 1, 0.30])                   # local chain
        for v in range(1, cls.N - 4, 5):
            rows.append([v, v + 4, 0.25])                   # a few shortcuts
        return rows

    @classmethod
    def members(cls):
        cats = ["Area A", "Area B", "Area C"]
        out = []
        for i in range(cls.N):
            # deliberately anti-correlated with degree so a topic bias has something to say
            radar = [round(((i * 7 + k * 11) % 10) / 10.0, 2) for k in range(len(cats))]
            out.append({"name": "Hub" if i == cls.HUB else "M%02d" % i,
                        "party": "Republican" if i % 2 else "Democrat",
                        "chamber": "House", "rank": i + 1, "radar": radar})
        return out, cats

    # ------------------------------------------------------------------ artifact-backed bundle
    @classmethod
    def directed(cls):
        """D.dadj rows -- [sponsor, signer, weight, probability]; who signed WHOSE bill.

        SIGNER has signed bills sponsored by the seed coalition (2, 3, 4).  SPONSOR_HUB has
        sponsored bills that nearly everyone signed and has signed nothing itself, so it carries a
        huge OUT-degree and no in-degree.  That is the exact asymmetry the cosponsor basis exists
        to respect: "we already found a sponsor, we are shopping for signers".
        """
        rows = [[s, cls.SIGNER, 1.0, 0.40] for s in (2, 3, 4)]
        rows += [[cls.SPONSOR_HUB, v, 1.0, 0.40]
                 for v in range(1, cls.N) if v != cls.SPONSOR_HUB]
        return rows

    @classmethod
    def cospon(cls, sponsorships=True):
        """D.cospon -- the bills-ledger counts.  Deliberately FLAT in `cos` so the propensity half
        cannot be what discriminates; `spons` is the display-only column under test."""
        spons = [0] * cls.N
        if sponsorships:
            spons[cls.SPONSOR_HUB] = 40
        return {"cos": [3] * cls.N, "spons": spons,
                "area": [[1, 1, 1] for _ in range(cls.N)],
                "area_share": [1 / 3.0, 1 / 3.0, 1 / 3.0]}

    @classmethod
    def selectivity(cls):
        """D.selectivity -- EB-shrunk signature rate over a real opportunity denominator.  Everyone
        gets the same opportunity; only SELECTIVE converts it."""
        sel = [[0.05, 0.05, 0.05] for _ in range(cls.N)]
        sig = [[1, 1, 1] for _ in range(cls.N)]
        sel[cls.SELECTIVE] = [0.90, 0.05, 0.05]
        sig[cls.SELECTIVE] = [9, 1, 1]
        return {"sel": sel, "opp": [[10, 10, 10] for _ in range(cls.N)], "sig": sig}


HARNESS = r"""
'use strict';
const DATA = JSON.parse(process.argv[2]);
const M = DATA.members, CATS = DATA.cats;
const ADJ = {};
DATA.edges.forEach(([a, b, p]) => {
  (ADJ[a] = ADJ[a] || []).push([b, p]);
  (ADJ[b] = ADJ[b] || []).push([a, p]);
});
// D is the desk bundle every optional artifact hangs off.  A test supplies only the keys it is
// exercising; each absent key leaves the shipped feature flag false (HAS_DIR / HAS_CPROP /
// HAS_SEL / HAS_PRICING), which is the graceful-degradation path the template documents.
const D = Object.assign({dadj: [], cospon: {}, selectivity: {}, pricing: {},
                         influence_policy: null}, DATA.bundle || {});
// the four interactive selections the panel owns. cbBasis is NOT declared here -- it comes from
// the shipped slice, so the harness cannot drift away from the landing default; run() sets it.
let coal = new Set(), cbArea = '', cbParty = '', cbCommittee = '';
__SHIPPED__
function run(seedSet, area, basis, party, committee) {
  coal = new Set(seedSet); cbArea = area || ''; cbBasis = basis || 'reach';
  cbParty = party || ''; cbCommittee = committee || '';
  return { suggestions: suggest(), reach: reachEstimate() };
}
const out = DATA.calls.map(c => run(c.coal, c.area, c.basis, c.party, c.committee));
process.stdout.write(JSON.stringify(out));
"""


class NodeHarness(unittest.TestCase):
    """Builds the node harness out of the shipped template and calls it. No tests of its own."""

    @classmethod
    def setUpClass(cls):
        if NODE is None:
            raise unittest.SkipTest("node is not available on PATH")
        source = TEMPLATE.read_text(encoding="utf-8-sig")
        shipped = "\n".join(shipped_slice(source, a, b) for a, b in SHIPPED_SLICES)
        cls.harness = HARNESS.replace("__SHIPPED__", shipped)
        cls.members, cls.cats = SyntheticGraph.members()
        cls.edges = SyntheticGraph.edges()

    def call(self, calls, bundle=None):
        payload = json.dumps({"members": self.members, "cats": self.cats,
                              "edges": self.edges, "calls": calls, "bundle": bundle or {}})
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "harness.js")
            Path(script).write_text(self.harness, encoding="utf-8")
            proc = subprocess.run([NODE, script, payload], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace")
        self.assertEqual(0, proc.returncode, proc.stderr)
        return json.loads(proc.stdout)


class CoalitionRecommenderBehaviour(NodeHarness):
    # ---------------------------------------------------------------- the bug
    def test_a_member_already_in_the_coalition_is_never_recommended_again(self):
        coalitions = [[0], [0, 1], [3], [0, 5, 9], list(range(0, 12)), [2, 3, 4, 5, 6, 7]]
        results = self.call([{"coal": c, "area": ""} for c in coalitions])
        for coal, res in zip(coalitions, results):
            picked = {row[0] for row in res["suggestions"]}
            self.assertFalse(picked & set(coal),
                             "already-selected members re-suggested: %s" % sorted(picked & set(coal)))

    def test_the_hub_stops_being_recommended_once_its_neighbourhood_is_covered(self):
        """THE regression. The hub is a legitimate FIRST pick; it must not survive every pick."""
        empty_seed = self.call([{"coal": [7], "area": ""}])[0]
        self.assertEqual(SyntheticGraph.HUB, empty_seed["suggestions"][0][0],
                         "a maximum-degree member should still win the first pick")
        # take the hub, then keep accepting the top suggestion; the hub must not dominate after
        grown = list(range(0, 14))          # hub plus its whole local neighbourhood
        res = self.call([{"coal": grown, "area": ""}])[0]
        gains = {row[0]: row[2] for row in res["suggestions"]}
        self.assertTrue(gains, "a partially built coalition must still have candidates")
        # every remaining candidate's NEW reach is small relative to the hub's opening value
        self.assertLess(max(gains.values()), empty_seed["suggestions"][0][2],
                        "marginal value did not fall as the coalition absorbed the graph")

    def test_recommendations_differ_across_topics(self):
        seed = [0, 5, 9]
        results = self.call([{"coal": seed, "area": a} for a in [""] + self.cats])
        tops = [r["suggestions"][0][0] for r in results if r["suggestions"]]
        self.assertGreater(len(set(tops)), 1,
                           "the area bias never changes the top recommendation: %s" % tops)
        orders = {tuple(row[0] for row in r["suggestions"]) for r in results}
        self.assertGreater(len(orders), 1, "the area bias never reorders the list at all")

    def test_marginal_gains_are_non_increasing_as_the_coalition_grows(self):
        """Submodularity sanity: greedily accepting the top suggestion must show diminishing
        returns. The pre-fix absolute tie-sum score increased instead."""
        coal, series = [11], []
        for _ in range(10):
            res = self.call([{"coal": list(coal), "area": ""}])[0]
            if not res["suggestions"]:
                break
            top = res["suggestions"][0]
            series.append(top[2])           # raw marginal gain, before the area bias
            coal.append(top[0])
        self.assertGreaterEqual(len(series), 5, "greedy stopped too early to judge")
        for earlier, later in zip(series, series[1:]):
            self.assertLessEqual(later, earlier + 1e-9,
                                 "marginal gain increased along the greedy: %s" % series)

    def test_a_candidate_the_coalition_already_reaches_is_discounted(self):
        """The already-covered correction: the same candidate must be worth less to a set that
        already touches them (mirrors the leave-one-out sector mean in lobby_champions.py)."""
        # member 5 is tied to 1 (shortcut) and to 6 (chain); adding 6 both raises 5's coverage and
        # removes 6 from the reach 5 would have brought.
        # POOL (2026-08-03): the panel reports only its top 16 rows, and the shipped pool fix
        # widened the candidate pool from the coalition's NEIGHBOURS to the whole eligible chamber.
        # 5 is then discounted so hard it drops off the end of the list -- the property under test
        # truncating away its own evidence. Run the comparison inside the shipped party filter
        # instead: that leaves 11 candidates, so every one is reported and nothing can be hidden.
        target = 5
        party = self.members[target]["party"]
        alone, covered = self.call([{"coal": [1], "area": "", "party": party},
                                    {"coal": [1, 6], "area": "", "party": party}])
        a = next((r[2] for r in alone["suggestions"] if r[0] == target), None)
        b = next((r[2] for r in covered["suggestions"] if r[0] == target), None)
        self.assertIsNotNone(a, "fixture: target must be a candidate for the smaller coalition")
        self.assertIsNotNone(b, "fixture: target must be a candidate for the larger coalition")
        self.assertLess(b, a, "a candidate the set already covers was not discounted")

    def test_recommendation_is_deterministic(self):
        """Variety must come from the marginal update, never from randomness (SEED=7/MC=20 house
        rule): the same coalition must always produce the same list."""
        call = [{"coal": [0, 4, 8], "area": "Area B"}]
        self.assertEqual(self.call(call), self.call(call))

    # ------------------------------------------- coalition-specific (leave-one-out) basis
    def test_the_coalition_specific_basis_demotes_the_graph_hub(self):
        """The (d) correction, mirroring lobby_champions.py's sector_relative_score: the member
        tied to EVERYONE must lose to a member tied specifically to this set."""
        coal = [2, 3, 4]
        reach, specific = self.call([{"coal": coal, "area": "", "basis": "reach"},
                                     {"coal": coal, "area": "", "basis": "specific"}])
        reach_rank = [row[0] for row in reach["suggestions"]]
        specific_rank = [row[0] for row in specific["suggestions"]]
        self.assertEqual(SyntheticGraph.HUB, reach_rank[0],
                         "reach should still name the hub — that is the honest widest-net answer")
        self.assertGreater(specific_rank.index(SyntheticGraph.HUB), 0,
                           "the coalition-specific basis still put the everyone-hub first")

    def test_the_two_bases_are_genuinely_different_orderings(self):
        coal = [0, 5, 9]
        reach, specific = self.call([{"coal": coal, "area": "", "basis": "reach"},
                                     {"coal": coal, "area": "", "basis": "specific"}])
        self.assertNotEqual([r[0] for r in reach["suggestions"]],
                            [r[0] for r in specific["suggestions"]],
                            "the basis toggle does not reorder anything")

    def test_both_bases_report_the_same_reach_figures(self):
        """Switching basis changes the ORDER, never what a row claims about reach."""
        coal = [0, 5, 9]
        reach, specific = self.call([{"coal": coal, "area": "", "basis": "reach"},
                                     {"coal": coal, "area": "", "basis": "specific"}])
        self.assertEqual(reach["reach"], specific["reach"])
        claimed = {row[0]: (row[2], row[3]) for row in reach["suggestions"]}
        for member, gain, already in ((r[0], r[2], r[3]) for r in specific["suggestions"]):
            if member in claimed:
                self.assertAlmostEqual(claimed[member][0], gain, places=9)
                self.assertAlmostEqual(claimed[member][1], already, places=9)

    def test_the_coalition_specific_basis_excludes_selected_members_too(self):
        for coal in ([0], [0, 1, 2], list(range(0, 10))):
            res = self.call([{"coal": coal, "area": "", "basis": "specific"}])[0]
            picked = {row[0] for row in res["suggestions"]}
            self.assertFalse(picked & set(coal))

    def test_an_area_bias_never_inverts_a_negative_specific_residual(self):
        """The bias is a positive multiplier; applied naively it would REWARD a strongly negative
        residual. A candidate must never overtake one that outranked it on every bias factor."""
        coal = [0, 5, 9]
        plain = self.call([{"coal": coal, "area": "", "basis": "specific"}])[0]["suggestions"]
        scores = {row[0]: row[1] for row in plain}
        worst = min(scores, key=lambda k: scores[k])
        best = max(scores, key=lambda k: scores[k])
        for area in self.cats:
            biased = self.call([{"coal": coal, "area": area, "basis": "specific"}])[0]["suggestions"]
            order = [row[0] for row in biased]
            if worst in order and best in order:
                self.assertLess(order.index(best), order.index(worst),
                                "area bias inverted the sign of the specific residual on %s" % area)

    def test_reach_matches_the_sum_of_accepted_marginals(self):
        """The displayed reach stat and the recommender must be the same objective: accepting a
        suggestion must move reach by exactly the gain that was advertised."""
        base = self.call([{"coal": [6], "area": ""}])[0]
        top = base["suggestions"][0]
        after = self.call([{"coal": [6, top[0]], "area": ""}])[0]
        self.assertAlmostEqual(base["reach"] + top[2], after["reach"], places=6)


class CosponsorPropensityBasis(NodeHarness):
    """The 2026-08-03 landing basis, exercised with its artifacts PRESENT.

    Until now nothing covered this path at all: the harness supplied no bundle, so every run fell
    through `if(!HAS_DIR&&!HAS_CPROP)` to the affinity residual and the cosponsor basis was never
    actually executed.  These fixtures populate D.dadj / D.cospon / D.selectivity so the real
    scorer runs, and assert the invariants the template commits to in prose.
    """

    COAL = [2, 3, 4]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The basis arrived on 2026-08-03. Against an earlier template there is no cosponsor path
        # to exercise, so these SKIP rather than fail -- the same contract as a missing node.
        # A skip must never come to mean "the basis was deleted and nothing noticed", so
        # test_the_cosponsor_basis_is_wired_wherever_it_is_offered below fails, rather than skips,
        # the moment the template offers the basis without the machinery behind it.
        source = TEMPLATE.read_text(encoding="utf-8-sig")
        if "function basisScores(" not in source or "basis==='cosponsor'" not in source:
            raise unittest.SkipTest("template predates the cosponsor-propensity basis")

    def bundle(self, sponsorships=True, selectivity=True):
        b = {"dadj": SyntheticGraph.directed(),
             "cospon": SyntheticGraph.cospon(sponsorships=sponsorships)}
        if selectivity:
            b["selectivity"] = SyntheticGraph.selectivity()
        return b

    def test_the_basis_promotes_the_member_who_signed_this_coalitions_bills(self):
        """Paul's ethos for the pathfinder: "we already found a sponsor, it is finding the best
        path around them to build the most effective co-sponsor coalition". So the member with a
        demonstrated record of SIGNING these seeds' bills must win, and the member with the huge
        sponsorship book must not ride in on it."""
        cospon, reach = self.call(
            [{"coal": self.COAL, "area": "", "basis": "cosponsor"},
             {"coal": self.COAL, "area": "", "basis": "reach"}], self.bundle())
        order = [row[0] for row in cospon["suggestions"]]
        self.assertEqual(SyntheticGraph.SIGNER, order[0],
                         "the proven signer did not take the cosponsor basis' first pick: %s" % order[:5])
        self.assertNotIn(SyntheticGraph.SPONSOR_HUB, order,
                         "the sponsor-only hub was recommended as a COSPONSOR")
        # and the contrast is real: the reach objective names the graph hub, not the signer
        self.assertEqual(SyntheticGraph.HUB, [row[0] for row in reach["suggestions"]][0])

    def test_sponsorship_volume_never_enters_the_score(self):
        """Template: "Sponsorship (D.cospon.spons) is carried for DISPLAY ONLY and enters no
        score." A member who sponsors 40 bills and one who sponsors none must score identically."""
        call = [{"coal": self.COAL, "area": "", "basis": "cosponsor"}]
        none_ = self.call(call, self.bundle(sponsorships=False))[0]
        many = self.call(call, self.bundle(sponsorships=True))[0]
        self.assertEqual(none_, many,
                         "changing D.cospon.spons moved the cosponsor ranking; it is display-only")

    def test_the_row_reports_its_cosponsorship_evidence(self):
        """Switching basis changes the order, never what a row CLAIMS -- but the claim has to be
        there. With the artifacts present the cosponsorship count and the tie to THIS set are
        reported; without them the same columns are null rather than a fabricated zero."""
        with_, without = (self.call([{"coal": self.COAL, "area": "", "basis": "cosponsor"}],
                                    self.bundle())[0],
                          self.call([{"coal": self.COAL, "area": "", "basis": "cosponsor"}])[0])
        top = next(r for r in with_["suggestions"] if r[0] == SyntheticGraph.SIGNER)
        self.assertIsNotNone(top[4], "cosponsorship count not reported")
        self.assertGreater(top[5], 0, "the signer's directed tie to this coalition is not reported")
        for row in without["suggestions"]:
            self.assertIsNone(row[4], "a cosponsorship count was invented with no artifact")
            self.assertIsNone(row[5], "a directed tie was invented with no directed graph")

    def test_selectivity_fires_only_once_an_area_is_selected(self):
        """cosponSel() takes 0.25 of the score, but only when an area is picked. With no area the
        run must be byte-identical to one with no selectivity artifact at all (the template's
        "500/500 no-area coalitions unchanged"); with an area the selective member must rise."""
        plain, sel = self.bundle(selectivity=False), self.bundle(selectivity=True)
        no_area = [self.call([{"coal": self.COAL, "area": "", "basis": "cosponsor"}], b)[0]
                   for b in (plain, sel)]
        self.assertEqual(no_area[0], no_area[1],
                         "the selectivity artifact changed a run with no area selected")
        area = [self.call([{"coal": self.COAL, "area": "Area A", "basis": "cosponsor"}], b)[0]
                for b in (plain, sel)]
        ranks = [[row[0] for row in r["suggestions"]] for r in area]
        self.assertNotEqual(ranks[0], ranks[1], "selectivity did not reorder an area-scoped run")
        self.assertIn(SyntheticGraph.SELECTIVE, ranks[1])
        self.assertLess(ranks[1].index(SyntheticGraph.SELECTIVE),
                        ranks[0].index(SyntheticGraph.SELECTIVE),
                        "the member who signs almost only in this area did not rise")

    def test_the_basis_degrades_to_the_affinity_residual_without_artifacts(self):
        """No directed graph and no bills ledger is the shipped fallback, and it must be the
        coalition-specific residual rather than an empty or arbitrary list."""
        cospon, specific = self.call([{"coal": self.COAL, "area": "", "basis": "cosponsor"},
                                      {"coal": self.COAL, "area": "", "basis": "specific"}])
        self.assertEqual([r[0] for r in cospon["suggestions"]],
                         [r[0] for r in specific["suggestions"]])
        self.assertTrue(cospon["suggestions"], "the fallback returned no candidates at all")


class CoalitionRecommenderSource(unittest.TestCase):
    """Guards that survive without node."""

    def test_the_absolute_tie_sum_ranking_is_gone(self):
        source = TEMPLATE.read_text(encoding="utf-8-sig").replace(" ", "")
        self.assertNotIn("sc[v]=(sc[v]||0)+p", source,
                         "the monotone-increasing tie-sum score is back")
        self.assertIn("functionmarginalGain(", source)

    def test_the_panel_explains_why_a_member_is_recommended(self):
        source = TEMPLATE.read_text(encoding="utf-8-sig")
        self.assertIn("new reach", source)
        self.assertIn("already covered by this set", source)

    def test_the_basis_toggle_is_offered_and_documented(self):
        source = TEMPLATE.read_text(encoding="utf-8-sig")
        self.assertIn("id=cbbasis", source)
        self.assertIn("data-b=specific", source)
        self.assertIn("Coalition-specific", source)
        # the honesty line must say why the reach basis favours the big cosponsorship books
        self.assertIn("largest cosponsorship books", source)

    def test_the_landing_basis_and_the_highlighted_button_agree(self):
        """A mismatch would show one basis while highlighting the other."""
        source = TEMPLATE.read_text(encoding="utf-8-sig")
        basis = re.search(r"let cbBasis='(\w+)';", source)
        self.assertIsNotNone(basis, "cbBasis default not found")
        seg = re.search(r"<span class=seg id=cbbasis[^>]*>(.*?)</span>", source, re.S).group(1)
        highlighted = re.findall(r"<button data-b=(\w+)[^>]*class=on", seg)
        self.assertEqual([basis.group(1)], highlighted,
                         "the highlighted basis button does not match the cbBasis default")

    def test_the_cosponsor_basis_is_wired_wherever_it_is_offered(self):
        """CosponsorPropensityBasis skips on a template that predates the basis. That skip must
        mean "not built yet" and never "deleted, and the panel still offers it": if the template
        offers or lands on the cosponsor basis, the scorer behind it has to be there."""
        source = TEMPLATE.read_text(encoding="utf-8-sig")
        if "data-b=cosponsor" not in source and "let cbBasis='cosponsor';" not in source:
            self.skipTest("template predates the cosponsor-propensity basis")
        for symbol in ("function basisScores(", "basis==='cosponsor'", "function directedTie(",
                       "function cosponRate("):
            self.assertIn(symbol, source,
                          "the panel offers the cosponsor basis but %s is gone" % symbol)

    def test_no_randomness_was_introduced_into_the_recommender(self):
        source = TEMPLATE.read_text(encoding="utf-8-sig")
        block = source.split("COALITION BUILDER", 1)[1].split("INVESTMENT EFFICIENCY", 1)[0]
        self.assertNotIn("Math.random", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
