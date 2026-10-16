# -*- coding: utf-8 -*-
"""Offline validator proof. No API calls.

A positive control (a compliant brief must pass) and one negative control per gate
(each gate must fire on its own mutation, and only its own). A gate that never fires
is not a gate.
"""
import collections
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from fec.members import gen_demo_briefs as G   # noqa: E402

LEDGER_PATH = os.path.join(HERE, "out", "demo_ledger.json")
if not os.path.exists(LEDGER_PATH):
    LEDGER_PATH = os.path.normpath(os.path.join(
        HERE, "..", "..", "data", "district", "district_brief_evidence_v1.json"))
LED = json.load(open(LEDGER_PATH, encoding="utf-8"))["members"]

OWENS = LED["Burgess Owens"]
BEGICH = LED["Nicholas Begich"]
MORAN = LED["Jerry Moran"]
HARRIS = LED["Andy Harris"]
MAGAZINER = LED["Seth Magaziner"]
MARSHALL = LED["Roger Marshall"]


def ctx(record):
    sent, _user = G.build_user(record)
    return {
        "record": sent,
        "budget": G.superlative_budget(record),
        "allowed": G.allowed_figures(sent),
        "noun": sent["geography"]["noun"],
        "want_year": (sent["constraints"].get("industry_vintage_year")
                      if sent["constraints"].get("industry_vintage_is_current") is False
                      else None),
    }


def run(text, record, name, seen=None):
    c = ctx(record)
    return G.validate(text, c["record"], c["budget"], c["allowed"], seen or set(),
                      want_year=c["want_year"],
                      forbid_word="district" if c["noun"] == "state" else None,
                      require_word="state" if c["noun"] == "state" else None,
                      member_name=name)


# ---------------------------------------------------------------- positive control
GOOD = """The fourth seat in Utah is young, well paid and growing into its housing costs.
The median age is 31.2 years, and a household in the middle takes home 109,469 dollars a
year, which puts it well ahead of most seats in the country. Housing has run ahead of that:
the middle home is worth more than five times the middle income. Even so, 73.8 percent of
occupied homes are owned rather than rented, and poverty here is among the lowest anywhere,
at 5.4 percent. Work is not hard to find.

Credit unions own this market. They hold 63.2 percent of all the retail offices banks and
credit unions run between them, a share matched almost nowhere else. Mountain America,
America First and Utah Community are the largest credit unions working there. Their
employment runs at roughly twice the national norm, and their offices sit thicker. A young,
high earning, home owning population is exactly the borrower base that produces mortgage
and vehicle lending at volume.

Construction is what this place does that others do not, at 10.3 percent of jobs and close
to twice the national rate. Retail trade employs more people than anything else. Small
seams of mining and a healthy information sector sit alongside them, while health care is
thinner here than the country as a whole."""


def report(label, defects, expect_substring=None):
    if expect_substring is None:
        ok = not defects
        print("%-46s %s" % (label, "PASS" if ok else "FAIL"))
        for d in defects:
            print("        - " + d)
        return ok
    ok = any(expect_substring.lower() in d.lower() for d in defects)
    print("%-46s %s  (%d defect(s))" % (label, "CAUGHT" if ok else "MISSED", len(defects)))
    if not ok:
        for d in defects:
            print("        - " + d)
    return ok


results = []
print("== POSITIVE CONTROL ==")
results.append(report("compliant brief validates clean",
                      run(GOOD, OWENS, "Burgess Owens")))
print("   word count:", len(re.findall(r"[A-Za-z0-9$%,.'-]+", GOOD)),
      " figures:", [f[0] for f in G.figures_in(GOOD) if not f[3]],
      " superlative budget:", G.superlative_budget(OWENS))

print()
print("== NEGATIVE CONTROLS (each mutation must fire its own gate) ==")

M = GOOD.replace("Work is not hard to find.",
                 "Its residents are unusually well educated, with many holding a degree.")
results.append(report("education claim", run(M, OWENS, "Burgess Owens"), "education"))

M = GOOD.replace("Work is not hard to find.",
                 "It scores near the top of the demographic potential rank.")
results.append(report("place-rating claim", run(M, OWENS, "Burgess Owens"), "rating"))

M = GOOD.replace("at 5.4 percent.", "at 5.4 percent, and unemployment is 3.7 percent, "
                                    "with 209.7 people per square mile and 300,500 jobs.")
results.append(report("figure cap exceeded", run(M, OWENS, "Burgess Owens"), "figures"))

M = GOOD.replace("63.2 percent", "71.4 percent")
results.append(report("figure not in the ledger", run(M, OWENS, "Burgess Owens"),
                      "not in this member"))

ROGERS_SENT, _ = G.build_user(LED["Harold Rogers"])
results.append(report(
    "digits inside evidence-backed institution names are not figures",
    [] if not G.figures_in("Y-12 serves the district.",
                           G.figure_name_masks(ROGERS_SENT)) else
    ["Y-12 was misread as a statistical figure"]))

M = GOOD.replace("housing costs.", "housing costs — and it shows.")
results.append(report("em dash", run(M, OWENS, "Burgess Owens"), "long dash"))

M = GOOD.replace("housing costs.", "housing costs, naïvely.")
results.append(report("non-ascii character", run(M, OWENS, "Burgess Owens"), "ascii"))

M = GOOD.replace("\n\nConstruction is", "\nConstruction is")
results.append(report("wrong paragraph count", run(M, OWENS, "Burgess Owens"),
                      "paragraphs"))

M = GOOD.replace("The fourth seat in Utah", "**Overview.** The fourth seat in Utah")
results.append(report("markdown heading", run(M, OWENS, "Burgess Owens"), "markdown"))

M = GOOD.rsplit("\n\n", 1)[0] + "\n\nConstruction is what this place does."
results.append(report("under the word floor", run(M, OWENS, "Burgess Owens"), "words"))

M = GOOD.replace("Work is not hard to find.",
                 "Credit union membership has grown sharply there.")
results.append(report("credit union membership", run(M, OWENS, "Burgess Owens"),
                      "membership"))

M = GOOD.replace("Work is not hard to find.",
                 "The area boasts a vibrant retail landscape.")
results.append(report("brochure language", run(M, OWENS, "Burgess Owens"), "brochure"))

M = GOOD.replace("Work is not hard to find.",
                 "The location quotient for construction is elevated.")
results.append(report("method jargon", run(M, OWENS, "Burgess Owens"), "jargon"))

M = GOOD.replace("Work is not hard to find.",
                 "Roughly one in five households is cost burdened.")
results.append(report("cost-burden claim", run(M, OWENS, "Burgess Owens"), "cost burden"))

M = GOOD.replace("Work is not hard to find.",
                 "There are 78 credit union branches counted here.")
results.append(report("counted-not-apportioned", run(M, OWENS, "Burgess Owens"),
                      "counted-not-apportioned"))

M = GOOD.replace("Work is not hard to find.",
                 "Its largest employers are federal contractors.")
results.append(report("named employers", run(M, OWENS, "Burgess Owens"), "employers"))

M = GOOD.replace("Work is not hard to find.",
                 "Burgess and his neighbours know the area well.")
results.append(report("names the seat holder", run(M, OWENS, "Burgess Owens"),
                      "names the person"))

results.append(report(
    "name gate does not confuse Mark with market",
    [d for d in run(GOOD, OWENS, "Mark Alford") if "Names the person" in d]))
M = GOOD.replace("own this market", "booked this market")
results.append(report(
    "name gate does not confuse Booker with booked",
    [d for d in run(M, OWENS, "Cory Booker") if "Names the person" in d]))

M = GOOD.replace("poverty here is among the lowest anywhere",
                 "poverty is the lowest, incomes the highest and homeownership the "
                 "strongest")
results.append(report("superlative over budget", run(M, OWENS, "Burgess Owens"),
                      "superlative"))

M = GOOD.replace("which puts it well ahead of most seats in the country",
                 "which is well above most, and homeownership is above most too")
results.append(report("calibration phrase reused", run(M, OWENS, "Burgess Owens"),
                      "calibration"))

print()
print("== PER-MEMBER GATES ==")
SEN = ("Kansas is a wide, thinly settled state. Its middle household earns 75,514 dollars "
       "a year, and homes there are cheap against that. Owning is common and poverty is "
       "not high.\n\nCredit unions hold 15.7 percent of the retail offices in the state, "
       "well under what the typical state carries. Navy Federal Credit Union, Credit "
       "Union of America and Meritrust are the leading names. Employment in credit unions "
       "sits a little above the national norm.\n\nManufacturing is what the state does "
       "that others do not, at 12.8 percent of jobs. Health care employs the most people. "
       "Farming and public work fill in behind them, and the whole picture is steady "
       "rather than concentrated in any one thing at all across the years since then.")
results.append(report("senator: word 'district' forbidden",
                      run(SEN.replace("state.", "district."), MORAN, "Jerry Moran"),
                      "covers the whole state"))
results.append(report("senator: must name the state",
                      run(SEN.replace("state", "area").replace("States", "Areas"),
                          MORAN, "Jerry Moran"),
                      "Name the geography"))

AK = ("Alaska is huge and nearly empty. A household in the middle earns 95,665 dollars a "
      "year, and homes are dear against that. Most people own where they live, and "
      "poverty is not high.\n\nCredit unions are strong here. They hold 40.5 percent of "
      "the retail offices in the state, well above what the typical state carries. "
      "Global, Credit Union 1 and Nuvision are the leading names. Credit union employment "
      "runs far above the national norm and their offices thicker still, which is what "
      "you would expect where distance makes a local institution matter more.\n\nThe "
      "working picture of the state was measured in an earlier year, and back then oil "
      "and gas dominated everything, running many times the national rate. Health care "
      "and public work employed the most people. Transport and utilities filled in behind "
      "them, and the whole shape was unusually narrow for a state of that size.")
results.append(report("stale vintage: year must be named", run(AK, BEGICH, "Nicholas Begich"),
                      "measured in 2016"))

FAILM = ("Maryland's first seat is older and comfortable. A household in the middle earns "
         "89,533 dollars a year. Homes are dear against that, and most people own where "
         "they live.\n\nCredit unions are thin here, holding 18.8 percent of the retail "
         "offices. Their employment sits near the national norm.\n\nThere are 240,000 "
         "jobs in this seat, and farming is what it does that others do not, at 1.5 "
         "percent of jobs and above the national rate. Health care employs the most "
         "people, with retail and public work behind it, and the whole shape is broad "
         "rather than concentrated in any single trade at all in this part of the world.")
results.append(report("employment level in a shortfall state",
                      run(FAILM, HARRIS, "Andy Harris"), "not in this member"))

print()
print("== FIGURE TOKENISER (the two measured false positives) ==")
T = "As measured in 2023, the 1st congressional seat held 798,569 people at 39.9 years."
got = G.figures_in(T)
counted = [f[0] for f in got if not f[3]]
years = [f[0] for f in got if f[3]]
print("   counted:", counted, " years:", years)
results.append(report("trailing comma does not break the year test",
                      [] if years == ["2023"] else ["years were %s" % years]))
results.append(report("an ordinal is not a statistic",
                      [] if counted == ["798,569", "39.9"] else ["counted %s" % counted]))

print()
print("== NOVELTY ==")
MASKS = G.ledger_phrases(LED)


def runn(text, record, name, seen):
    c = ctx(record)
    return G.validate(text, c["record"], c["budget"], c["allowed"], seen,
                      want_year=c["want_year"],
                      forbid_word="district" if c["noun"] == "state" else None,
                      require_word="state" if c["noun"] == "state" else None,
                      member_name=name, masks=MASKS)


grams = G.brief_grams(GOOD, MASKS)
results.append(report("verbatim reuse of a prior brief",
                      runn(GOOD, OWENS, "Burgess Owens", grams), "Reuses wording"))
results.append(report("no reuse against an empty window",
                      runn(GOOD, OWENS, "Burgess Owens", set())))

# False positive 1: a five-gram that only exists because it straddles a full stop.
A = "Offices are run by Synchrony Bank. Credit union employment runs high there."
B = "Deposits sit with Synchrony Bank. Credit union employment runs thin there."
cross = G.brief_grams(A, MASKS) & G.brief_grams(B, MASKS)
results.append(report("gram spanning a full stop is not counted",
                      [] if not cross else ["counted %s" % list(cross)[:2]]))

# False positive 2: the sector's own name is not paraphrasable.
A = "Its strength is Health Care and Social Assistance, which employs many."
B = "Its strength is Health Care and Social Assistance, which pays poorly."
shared = G.brief_grams(A, MASKS) & G.brief_grams(B, MASKS)
results.append(report("a sector's own name is not counted as reuse",
                      [] if not shared else ["counted %s" % list(shared)[:2]]))

# The threshold itself, raised 3 -> 5 on 2026-09-03 (Paul): two distinct runs pass,
# three and four now ALSO pass (this is the change), five fires. Five independent
# one-sentence five-token runs so 3/4/5-run variants can share an exact prefix of them.
# Invented words, individually checked against MASKS (ledger_phrases does a raw
# substring match, not word-boundary -- "chi" silently matched inside "Michigan" and
# "omega" is a real credit union's name, both measured false starts while writing this).
base5 = ("Quixxel vornak ytterbium flarnok plizzo here. "
         "Wextra glimmark trinovax borvek yislan here. "
         "Cendrak fluvium hesket dravorn zelqui here. "
         "Nimbrose tuvalek shrimna colgast burevi here. "
         "Alopren fenshaw dorquil metrasi ovendaal here.")
two = ("Quixxel vornak ytterbium flarnok plizzo now. "
       "Wextra glimmark trinovax borvek yislan now. "
       "Something entirely other written now.")
three = ("Quixxel vornak ytterbium flarnok plizzo now. "
         "Wextra glimmark trinovax borvek yislan now. "
         "Cendrak fluvium hesket dravorn zelqui now. Something entirely other written now.")
four = ("Quixxel vornak ytterbium flarnok plizzo now. "
        "Wextra glimmark trinovax borvek yislan now. "
        "Cendrak fluvium hesket dravorn zelqui now. "
        "Nimbrose tuvalek shrimna colgast burevi now. "
        "Something entirely other written now.")
five = ("Quixxel vornak ytterbium flarnok plizzo now. "
        "Wextra glimmark trinovax borvek yislan now. "
        "Cendrak fluvium hesket dravorn zelqui now. "
        "Nimbrose tuvalek shrimna colgast burevi now. "
        "Alopren fenshaw dorquil metrasi ovendaal now.")
seen = G.brief_grams(base5, MASKS)
results.append(report("two reused runs do not fire",
                      [] if not G.novelty_defects(two, seen, MASKS) else ["fired"]))
results.append(report("three reused runs do NOT fire (raised threshold: was the old cap)",
                      [] if not G.novelty_defects(three, seen, MASKS) else ["fired"]))
results.append(report("REQUIRED CONTROL: four reused runs now PASS (previously failed "
                      "under the 3-run threshold)",
                      [] if not G.novelty_defects(four, seen, MASKS) else ["fired"]))
results.append(report("REQUIRED CONTROL: five reused runs still FAIL",
                      G.novelty_defects(five, seen, MASKS), "Reuses wording"))
results.append(report("REQUIRED CONTROL: one lifted 10+ token run still FAILS "
                      "regardless of run count (the untouched trigger)",
                      G.novelty_defects("Alpha beta gamma delta epsilon here zeta eta "
                                        "theta iota kappa here.",
                                        G.brief_grams("Alpha beta gamma delta epsilon "
                                                      "here zeta eta theta iota kappa "
                                                      "here.", MASKS), MASKS),
                      "Reuses wording"))

def assert_that(label, cond, reason):
    """report() wants a list of defect strings (empty = pass); this adapts a plain
    boolean structural check (a ledger field, a directive string) to that shape."""
    return report(label, [] if cond else [reason])


print()
print("== PILOT DEFECT FIXES, 2026-09-02 (five defects the pilot found) ==")

# Defect 1 (Owens): "against a single named bank competitor, Synchrony Bank" -- the
# ledger no longer carries a bare 'largest_bank_by_name'; it carries a renamed field,
# a branch count, and a caveat, and these four phrasings are banned outright near
# 'competitor'.
M = GOOD.replace("and vehicle lending at volume.",
                 "and vehicle lending against a single named bank competitor.")
results.append(report("defect 1: single-bank-competitor framing",
                      run(M, OWENS, "Burgess Owens"), "single-bank"))
M = GOOD.replace("and vehicle lending at volume.",
                 "and vehicle lending, the only bank competitor being out of state.")
results.append(report("defect 1: 'the only bank ... competitor'",
                      run(M, OWENS, "Burgess Owens"), "single-bank"))
results.append(assert_that("defect 1: field renamed off the ledger",
                           "largest_bank_by_name" not in OWENS["credit_unions"],
                           "largest_bank_by_name is still present"))
results.append(assert_that("defect 1: branch count now carried",
                           OWENS["credit_unions"].get("bank_branch_count") == 89,
                           "bank_branch_count=%r, expected 89"
                           % OWENS["credit_unions"].get("bank_branch_count")))

# Defect 2 (Begich): "a member base scattered across enormous distances" -- no
# membership count exists for any credit union in this ledger.
M = GOOD.replace("and vehicle lending at volume.",
                 "for a member base scattered across the district.")
results.append(report("defect 2: 'member base'", run(M, OWENS, "Burgess Owens"),
                      "membership"))
M = GOOD.replace("and vehicle lending at volume.",
                 "for the members served across the district.")
results.append(report("defect 2: 'members served'", run(M, OWENS, "Burgess Owens"),
                      "membership"))
# a member of Congress is never at risk: the system prompt forbids ever naming or
# referring to the seat holder, so 'member' never needs its other sense, and 'the
# member' is already caught by the pre-existing per-person gate.
results.append(report("defect 2: naming the seat holder is caught separately",
                      run(GOOD.replace("Work is not hard to find.",
                                       "The member for this seat knows it well."),
                          OWENS, "Burgess Owens"),
                      "member as a person"))

# Defect 3, SOFT (Harris / Magaziner): deposit-per-branch and deposit-competition
# framing on an equal-split estimate.
M = GOOD.replace("and vehicle lending at volume.",
                 "relative to the people and deposits they serve.")
results.append(report("defect 3: 'people and deposits they serve'",
                      run(M, OWENS, "Burgess Owens"), "deposit-per-branch"))
M = GOOD.replace("and vehicle lending at volume.",
                 "as credit unions compete hard for the deposits of a well paid "
                 "population.")
results.append(report("defect 3: 'compete ... for the deposits of ... population'",
                      run(M, OWENS, "Burgess Owens"), "deposit-per-branch"))

# Defect 4 (Moran): "employs more thickly than its office count alone would show" on
# a 1.09-vs-1.05 gap. This is a Python materiality judgement, not a bannable phrase
# (the same wording is a true, legitimate claim for Begich, whose gap is 1.45), so
# the mechanism is checked structurally: the ledger field and the directive text,
# not a validate() defect.
results.append(assert_that(
    "defect 4: Moran/Marshall gap (0.04) reads as about_equal",
    MORAN["credit_unions"].get("credit_union_jobs_vs_offices_relationship") == "about_equal",
    "relationship=%r" % MORAN["credit_unions"].get("credit_union_jobs_vs_offices_relationship")))
results.append(assert_that(
    "defect 4: no raw contrast field takes its place",
    "credit_union_jobs_vs_offices_gap" not in MORAN["credit_unions"],
    "a raw gap field leaked into the sent ledger"))
results.append(assert_that(
    "defect 4: Begich's real 1.45 gap is NOT flattened",
    BEGICH["credit_unions"].get("credit_union_jobs_vs_offices_relationship")
    == "jobs_outpace_offices",
    "relationship=%r" % BEGICH["credit_unions"].get("credit_union_jobs_vs_offices_relationship")))
moran_sent, _ = G.build_user(MORAN)
results.append(assert_that(
    "defect 4: the directive tells the model to write them equal",
    "effectively the same level" in G.directive(moran_sent),
    "no flattening instruction found in Moran's directive"))
begich_sent, _ = G.build_user(BEGICH)
results.append(assert_that(
    "defect 4: Begich's directive carries no such flattening line",
    "effectively the same level" not in G.directive(begich_sent),
    "the flattening instruction leaked into Begich's directive"))

# Defect 5 (template repetition): a per-member opening angle for p1 and p2, drawn
# from that member's own evidence. Checked structurally: four House members pull
# four different angles; the identical-evidence Kansas pair falls back to the
# existing (and now doubled, p1 AND p2) seat-slot split instead of drawing an
# angle from evidence that cannot tell them apart.
harris_sent, _ = G.build_user(HARRIS)
magaziner_sent, _ = G.build_user(MAGAZINER)
owens_sent, _ = G.build_user(OWENS)
angles_p1 = {n: G.p1_angle(s) for n, s in (("Owens", owens_sent), ("Begich", begich_sent),
                                           ("Harris", harris_sent),
                                           ("Magaziner", magaziner_sent))}
results.append(assert_that(
    "defect 5: four house members draw four distinct p1 angles",
    len(set(angles_p1.values())) == 4, "angles were %r" % angles_p1))
angles_p2 = {n: G.p2_angle(s) for n, s in (("Owens", owens_sent), ("Begich", begich_sent),
                                           ("Harris", harris_sent),
                                           ("Magaziner", magaziner_sent))}
results.append(assert_that(
    "defect 5: house p2 angle tracks the standout CU fact "
    "(Owens/Begich share-first, Harris/Magaziner staffing-first)",
    angles_p2["Owens"] == angles_p2["Begich"]
    and angles_p2["Harris"] == angles_p2["Magaziner"]
    and angles_p2["Owens"] != angles_p2["Harris"],
    "angles were %r" % angles_p2))
results.append(assert_that(
    "defect 5: directive() never asks a state member's p1_angle "
    "(senate keeps its own seat-slot split, not this mechanism; p1_angle() itself "
    "is noun-agnostic, so the gate lives in directive()'s call site, checked here)",
    (G.p1_angle(moran_sent) or "") not in G.directive(moran_sent),
    "p1_angle(Moran) leaked into Moran's directive text"))
marshall_sent, _ = G.build_user(MARSHALL)
results.append(assert_that(
    "defect 5: Moran and Marshall directives differ despite "
    "byte-identical evidence (seat-slot p1 AND p2 split)",
    G.directive(moran_sent) != G.directive(marshall_sent),
    "directives were identical"))

print()
print("== THOUSANDS SEPARATOR, 2026-09-03 (Paul: mechanical formatting, not a new ban) ==")
M = GOOD.replace("109,469 dollars", "109469 dollars")
results.append(report("bare figure without a comma is caught",
                      run(M, OWENS, "Burgess Owens"), "thousands separator"))
results.append(assert_that(
    "a correctly-formatted figure (the unmutated GOOD brief) never trips the new gate",
    not any("thousands separator" in d for d in run(GOOD, OWENS, "Burgess Owens")),
    "the gate false-fired on GOOD"))
M2 = GOOD.replace("The median age is 31.2 years,",
                  "The median age is 31.2 years, as measured in 2016,")
results.append(assert_that(
    "a bare 4-digit YEAR is exempt from the thousands-separator gate",
    not any("thousands separator" in d for d in run(M2, OWENS, "Burgess Owens")),
    "a year was wrongly flagged"))
results.append(assert_that(
    "still grounded: the gate fires alongside grounding, not instead of it "
    "(109469 without a comma is still the same, correctly-grounded, value)",
    not any("not in this member" in d for d in run(M, OWENS, "Burgess Owens")),
    "the reformatted-but-still-correct figure was wrongly treated as ungrounded"))

print()
print("== SLIDING NOVELTY WINDOW, 2026-09-03 (N=%d) ==" % G.NOVELTY_WINDOW)

# Control A: a brief that would FAIL against the whole run's accepted briefs (the
# old, unbounded behaviour) but PASS against a bounded 50-window, because the brief
# it echoes has aged out of the window. Simulate 51 acceptances in order: one
# "old" brief carrying a distinctive 10-token run, then 50 unrelated filler
# briefs -- enough to push the old one out of a maxlen=50 window entirely.
SHARED_RUN = "vega ypsilon zeta eta theta iota kappa lambda mu nu"  # 10 tokens
OLD_BRIEF = SHARED_RUN + " written first here today for testing this window mechanism now."
CANDIDATE_OLD_ECHO = (SHARED_RUN + " appears again in a completely different brief "
                      "about another place entirely.")

window_a = collections.deque(maxlen=G.NOVELTY_WINDOW)
counts_a = collections.Counter()
G.window_add(window_a, counts_a, G.brief_grams(OLD_BRIEF, MASKS))
full_seen = set(G.brief_grams(OLD_BRIEF, MASKS))
for i in range(G.NOVELTY_WINDOW):
    filler = ("Filler brief number %d uses placeholder words to occupy this space "
              "only today." % i)
    grams = G.brief_grams(filler, MASKS)
    G.window_add(window_a, counts_a, grams)
    full_seen |= grams

results.append(assert_that(
    "old brief has aged out of a full 50-window",
    len(window_a) == G.NOVELTY_WINDOW and G.brief_grams(OLD_BRIEF, MASKS) not in window_a,
    "window did not evict the old brief (len=%d)" % len(window_a)))
defects_full = G.novelty_defects(CANDIDATE_OLD_ECHO, full_seen, MASKS)
defects_windowed = G.novelty_defects(CANDIDATE_OLD_ECHO, set(counts_a), MASKS)
results.append(report("echo of an old brief: FAILS against the full/unbounded run",
                      defects_full, "Reuses wording"))
results.append(report("echo of an old brief: PASSES against the 50-window "
                      "(this is the fix -- same candidate, bounded comparison)",
                      defects_windowed))

# Control B: the long-run trigger (>=10 tokens) must still fire when the source
# brief is one of the last 50 -- i.e. still inside the window. This is the "do not
# weaken the threshold, only bound what it compares against" requirement.
RECENT_SHARED_RUN = "quorra vex mireth axolotl bramble seven ostinato ferrous glimmer thallow"
filler_25 = ("Filler brief number 25 uses placeholder words to occupy this space "
            "only today. " + RECENT_SHARED_RUN + " sits inside a later sentence here.")
window_b = collections.deque(maxlen=G.NOVELTY_WINDOW)
counts_b = collections.Counter()
for i in range(G.NOVELTY_WINDOW):
    text = filler_25 if i == 25 else (
        "Filler brief number %d uses placeholder words to occupy this space "
        "only today." % i)
    G.window_add(window_b, counts_b, G.brief_grams(text, MASKS))
CANDIDATE_RECENT_ECHO = (RECENT_SHARED_RUN + " turns up again in an unrelated "
                         "brief about a different place.")
results.append(report("echo of a RECENT (still-windowed) brief still fires "
                      "(the trigger itself is untouched)",
                      G.novelty_defects(CANDIDATE_RECENT_ECHO, set(counts_b), MASKS),
                      "Reuses wording"))

# Control C: rebuild_novelty_window() on resume. Write a synthetic progress log
# with more than NOVELTY_WINDOW acceptances plus one member that failed on an
# earlier run and only succeeded on a later one (interleaved, not contiguous), and
# confirm the rebuilt window/counts match what window_add() produces from replaying
# the same ok=True texts in file order -- i.e. resume does not silently reset to an
# empty window, and a fail-then-succeed member is placed at its succeeding line's
# position, not its first appearance.
tmp_fd, tmp_path = tempfile.mkstemp(suffix=".jsonl")
os.close(tmp_fd)
try:
    lines = []
    for i in range(G.NOVELTY_WINDOW + 5):
        lines.append({"name": "Filler %d" % i, "ok": True,
                      "text": "Resume filler brief number %d for the rebuild test "
                              "today." % i})
        if i == 3:
            # "Retry Case" fails on attempt 3's slot, then succeeds much later --
            # its accepted line must count at its LATER position, not position 3.
            lines.append({"name": "Retry Case", "ok": False, "text": "",
                          "defects": ["placeholder failure"]})
    lines.append({"name": "Retry Case", "ok": True,
                  "text": "Retry case succeeded on its second attempt, much later "
                          "in the run than its first try."})
    with open(tmp_path, "w", encoding="utf-8") as fh:
        for rec in lines:
            fh.write(json.dumps(rec) + "\n")

    rebuilt_window, rebuilt_counts = G.rebuild_novelty_window(tmp_path, MASKS)
    expect_window = collections.deque(maxlen=G.NOVELTY_WINDOW)
    expect_counts = collections.Counter()
    for rec in lines:
        if rec.get("ok"):
            G.window_add(expect_window, expect_counts, G.brief_grams(rec["text"], MASKS))

    results.append(assert_that(
        "resume: rebuilt window matches a live window_add() replay",
        list(rebuilt_window) == list(expect_window) and rebuilt_counts == expect_counts,
        "rebuilt window/counts diverged from the expected replay"))
    results.append(assert_that(
        "resume: window is bounded at N even though the log holds more acceptances",
        len(rebuilt_window) == G.NOVELTY_WINDOW,
        "window held %d entries, expected %d" % (len(rebuilt_window), G.NOVELTY_WINDOW)))
    # "Retry Case"'s accepted grams must be present in the
    # rebuilt window (it is recent enough to still be inside it), and the failed
    # attempt (empty text) must not have contributed anything.
    retry_grams = G.brief_grams(
        "Retry case succeeded on its second attempt, much later in the run than its "
        "first try.", MASKS)
    results.append(assert_that(
        "resume: the retried member's accepted grams made it into the window",
        retry_grams and retry_grams in rebuilt_window,
        "retry grams not found in rebuilt window"))
finally:
    os.remove(tmp_path)

print()
print("%d/%d checks behaved as specified." % (sum(1 for r in results if r), len(results)))
sys.exit(0 if all(results) else 1)
