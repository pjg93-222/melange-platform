# How the 19-category adjudication is produced and recorded

Version: `acu-legislation-19-category-adjudication-v1`
Status: design note. Nothing here has been adjudicated. No decision has been written.

This document is written to be read without reading any code. It says what a
decision *is*, what evidence it must carry, what happens when the evidence is
not there, and who signs it off. It also reports one finding that changes the
order of work.

---

## 1. The problem, in one paragraph

Congress.gov Advanced Search ran 62 queries and returned 18,557 measures. A
measure can be returned by more than one query, so there are 64,004
measure-and-query pairs — call them *memberships*. Nineteen of the 62 queries
are the real policy categories; the other 43 are coverage probes. Nobody has
yet looked at any measure and said "yes, this really is a Credit Union Tax
Status bill" or "no, this hit is a coincidence". Because that review has never
happened, the culling engine flags all 64,004 memberships and marks nothing as
keep or junk. This document specifies the review.

---

## 2. Two records, not one

The adjudication produces **two different kinds of record**. Confusing them is
the main way this work can go wrong.

### 2.1 The candidate sweep — 19 rows per measure

For each of the 18,557 measures, a reviewer works through **all nineteen
categories**, including the categories the measure was never returned for. For
each category they record that they looked at the official title, checked
whether an official CRS summary exists, read the official full text, and
treated any absence as neutral.

That is 18,557 × 19 = **352,583 rows**, which is exactly the figure the
architecture document specifies. This is the block the culling contract calls
`category_evidence`, and it is the thing that is empty today.

Why all nineteen and not just the ones the measure was returned for? Because
the contract requires the count to be exactly nineteen, and because a measure
returned only by the *Payments* query may genuinely also be an *Interchange*
bill. The sweep is what catches that. Discovery is a net, not a verdict.

### 2.2 The membership decision — one row per measure-and-query pair

Separately, for each of the 64,004 memberships, the reviewer records a
disposition: **include** or **exclude_false_positive**. This is what actually
moves a membership out of the flagged pile.

The two records are joined by the measure. A single sitting on one measure
produces its 19 sweep rows *and* the handful of membership decisions it carries
(the average measure has 3.4 memberships; the largest has 23).

**This is why the review queue groups every membership of a measure together
and never splits a measure across batches.** Reviewing a measure once against
all nineteen categories is far cheaper than reviewing each of its memberships
in isolation, and the evidence envelope demands all nineteen anyway.

---

## 3. The decision unit

**`(canonical_key, query_id)`.** For example
`("119:hr:2483", "credit_union_tax_status")`.

A measure can be a genuine false positive for one category while remaining
squarely relevant to another. There is no such thing as "this bill is
irrelevant" as a single fact. A measure only becomes candidate-wide junk when
*every one* of its memberships has been explicitly excluded, and even then it
is a reversible view, never a deletion.

---

## 4. What evidence a decision must cite

Every include and every exclude must cite official Congress.gov sources, in
this order of authority:

| Source | Requirement |
|---|---|
| Official title | Required for every disposition. |
| Official CRS summary | Required when Congress.gov publishes one. When Congress.gov officially has none, the decision cites the recorded *absence*, and that absence is neutral — it is not evidence against relevance. |
| Official full text | Required for every disposition. |
| All official text versions | Required only when an exclusion rests on something *not* being in the text. If you are ruling a measure out because a provision is absent, you must have looked at every version. |
| Press or article absence | **Never** admissible as exclusion evidence. |
| Search-query membership | **Never** on its own sufficient to include or exclude. |

The decision record itself carries: the measure and query, the disposition, a
written reason, a reason code, the evidence basis (one of
`congress_qualified_identity_mismatch`, `explicit_named_outline_bill`,
`official_title`, `official_summary`, `operative_provisions`,
`official_title_and_provisions`), a Congress.gov URL plus a short quoted or
paraphrased official passage, the reviewer's name, the timestamp, a confidence
level, and a hash binding it to the exact evidence it was made against.

---

## 5. What counts as sufficient evidence

These rules are carried over unchanged from the binding relevance review
method; they are restated here so this note stands alone.

**To include**, at least one official source must affirmatively show a material
industry connection: a title that is specifically about the relevant policy; a
summary describing a material effect on credit unions, NCUA, credit-union
members, covered institutions that include credit unions, or the category's
regulated activity; or an operative provision that creates or changes a duty,
authority, exemption, cost, benefit, eligibility rule, market structure,
funding stream, or enforcement exposure material to the industry.

A multi-topic bill may belong to several categories. Relevance is not discarded
because the bill's headline subject is broader.

**To exclude**, official evidence must affirmatively show the hit is unrelated:
a different bill sharing a short title or acronym; a Congress/bill-number
collision; a lexical collision (transportation "interchange"); a named statute
appearing only in a definition, citation, or conforming reference; or operative
provisions that regulate a different industry and do not reach the category.

**Named-Act rules.** The bare word `Act` is never evidence. A short acronym
alias — `SMART Act`, `CLARITY Act`, `GENIUS Act`, `TABS Act` — is a recall
probe, never relevance evidence on its own; it requires Congress-qualified
identity plus the complete official title or operative provisions. A base
statute name such as `Federal Credit Union Act` must appear complete, ordered,
and token-bounded. A mere mention of a named statute is unresolved until a
localized operative action shows what the bill does to it. 817 of the 64,004
queued memberships are flagged as ambiguous short-act aliases; the queue sorts
them to the front within their measure.

**Uncertainty is never an exclusion.** Missing summary, missing text, weak
evidence, or no press coverage all leave the membership unresolved.

---

## 6. How "unresolved" is represented

There is no single "unknown" value. Four distinct states exist, and all four
land in the flagged pile:

| State | Meaning |
|---|---|
| `unresolved` | Nobody has looked at this membership yet. This is the state of all 64,004 today. |
| `unresolved_evidence_incomplete` | A reviewer looked, but a required official source is missing. The queue names exactly which one, as a **blocker**. |
| `unresolved_reviewer_cannot_decide` | Every required source is present and the reviewer still cannot say. Recorded with the reason, never forced. |
| `unresolved_prior_decision_not_finalizable` | A decision exists but does not satisfy the contract — for example it came from automated triage, or the evidence it cited has since changed. The queue keeps the row and records what the prior decision said. |

**A blocker is a statement about missing evidence. It is never a disposition
and can never become an exclusion.** This is enforced in code, not just in
prose: the queue builder emits every row with a null disposition, and the
culling engine's own gate refuses to finalize anything whose evidence is
incomplete.

---

## 7. How human ratification is captured

A decision leaves the flagged queue only if its `adjudication_state` is
`human_ratified` or `reverified_preexisting_project_decision`, and its
`automated_final_decision` flag is false.

The mechanism is a **batch signature**:

1. A reviewer (or a model lane acting as a proposer) fills in the decision
   columns for one batch of the queue.
2. A signature record is appended to `ratifications.ndjson`, an append-only,
   hash-chained ledger in the same shape as the existing junk and restoration
   ledgers. It names the queue generation, the batch, every queue row it
   covers, the content hash of each of those rows at the moment of signing, the
   ratifier, and the UTC timestamp.
3. A decision may claim `human_ratified` only if a signature record covers its
   `queue_row_id` **and** that row's content hash is unchanged. If the
   underlying evidence is re-collected and the row's hash moves, the signature
   no longer covers it and the membership returns to unresolved.

A model lane cannot self-ratify. Any decision whose `decision_source` begins
with `automated` is projected with `automated_final_decision: true`, and the
contract rejects it. That property already exists and is already tested; it is
not something this design has to add.

**Not built.** The ratification writer is deliberately not implemented. It
writes decisions, and writing decisions is not authorized. This note specifies
it so it can be built when Paul says so.

---

## 8. The review queue

`fec/legislation/build_legislation_category_review_queue.py` emits one row per membership
still needing adjudication. It reads the **discovery partition — all 64,004
memberships** — and never the effective review ledger, because the ledger omits
1,776 probe edges affecting 1,625 measures, 279 of which are reachable *only*
through a probe. A ledger-driven review would never see them.

### What a row carries

- the measure and the query, and a stable `queue_row_id` that is a hash of the
  membership, the evidence it is bound to, and the query definition;
- **why the membership exists**: the query's search terms, split into real
  phrases, short-act aliases (marked non-dispositive), the bare word `Act`
  (marked non-evidence), and Congress-qualified `cite:` identity anchors; plus
  the exact highlighted Congress.gov search-result URL the hit came from;
- **whether the query names this measure outright.** A `cite:` anchor such as
  `congressId:119 AND cite:"s2704"` is resolved to `119:s:2704` and compared
  with the measure itself. **55 of the 64,004 memberships** are explicitly
  named this way. That figure independently reproduces the earlier pipeline's
  `automated_named_outline_inclusion_count: 55` from a completely different
  code path, which is a useful check on both. These are the only memberships
  the relevance review method allows to bypass manual membership review;
- the official title and its hash, the measure's Congress.gov page, its `/text`
  page, and — where full text has actually been accepted — the verified
  collection URL;
- the evidence state: summary status, full-text status, version inventory;
- the **blockers**: exactly which required sources are missing;
- blank reviewer columns.

### One honest caveat on term matching

The queue reports which query terms it can find in the locally held discovery
record. **This is navigational, not evidentiary.** Congress.gov searched full
bill text and summaries this project does not hold, so a term that does not
match locally has not been ruled out. Empirically only **399 of 64,004 rows**
have any local term match at all, and only 262 have a match on a term that
could count as evidence. That is not a defect — it is direct proof that the
hits came from full text we do not yet have, and that a reviewer genuinely
cannot decide these from metadata alone.

### The order of work

Rows are grouped by measure — every membership of a measure is contiguous and
in one batch — and measures are ordered by, in strict sequence:

1. the measure's best tier (below);
2. how many queries name the measure outright by Congress-qualified cite;
3. how many scoring categories it appears in;
4. how many memberships it carries;
5. whether Congress.gov full text has been accepted;
6. whether the CRS summary question is settled;
7. the measure identifier, purely to break ties.

Within a measure, higher-tier memberships come first, then explicitly-named
memberships, then ambiguous short-act aliases, then query name.

| Tier | Meaning | Rows |
|---|---|---|
| T1 | scoring category, and Congress.gov full text is accepted and physically reverified | 15,637 |
| T2 | scoring category, no accepted full text yet | 45,204 |
| T3 | the one deliberately non-scoring category (`credit_union_bank_competitive_dynamics`) | 1,387 |
| T4 | coverage probe or non-dispositive edge | 1,776 |

The non-scoring category and the probe edges are **still queued**. They do not
feed scoring, but candidate-wide junk requires *every* membership to be
explicitly excluded, and the 19-slot sweep is required for every measure
regardless. Leaving them out would silently make junk unreachable.

Because tier is the first sort key, the 55 explicitly-named memberships land at
ranks 16,598–16,890 — at the head of T2, not the head of the queue, because
none of those 55 measures has accepted full text yet. If Paul wants them first
outright, that is a one-line change to the sort key. As shipped they are
reachable with a single filter:
`explicitly_named_in_query == True` in `queue.csv`.

---

## 9. The finding that changes the order of work

The handoff records the 19-category adjudication as *the* blocker. It is a
blocker, and it is the one that has never been started — but **completing it
today would move zero memberships out of flagged.** Running the project's own
contract validator over all 18,557 evidence envelopes gives:

| Readiness flag | Candidates |
|---|---|
| `title_ready` | 18,557 |
| `summary_ready` | **0** |
| `summary_explicitly_unavailable` | 4,122 |
| `full_text_ready` | 2,886 |
| `category_audit_ready` | **0** |
| `semantic_review_ready` | **0** |

A membership can only finalize when the title is ready, the summary question is
settled *either way*, the full text is accepted, and the 19-category audit
exists. Two of those are collection gaps, and they do not overlap:

- 2,886 measures have accepted Congress.gov full text.
- 4,122 measures have an officially recorded *absence* of a CRS summary, which
  is the only way the summary gate currently passes — nothing anywhere has
  summary status `available`, because the evidence builder correctly refuses to
  claim Congress.gov provenance for GovInfo-sourced summary text.
- **The intersection of those two sets is empty. Zero measures.**

So the ceiling on finalizable memberships today is 0, and it stays 0 until
either more Congress.gov full text or more Congress.gov summaries arrive. The
queue reports this per row as `decision_can_finalize_today`, which is currently
false for all 64,004.

**This does not mean the adjudication should wait.** The sweep is the long pole
— 352,583 rows — it is the only work not gated on a blocked network, and a
decision recorded now finalizes automatically the moment its evidence lands.
But the plan should stop describing the adjudication as the *single* gate.
There are three, and only one of them is reviewer work.

Blocker counts across the queued 64,004 memberships:

| Blocker | Memberships |
|---|---|
| `candidate_19_category_audit_absent` | 64,004 |
| `official_crs_summary_neither_fetched_nor_officially_absent` | 53,893 |
| `official_full_text_not_accepted_from_congress_gov` | 47,371 |
| `official_text_version_inventory_incomplete_blocks_exclusion` | 23,249 |

---

## 10. What this apparatus deliberately does not do

It does not adjudicate. It writes no `category_evidence`, no disposition, no
keep, no junk, no ledger entry. It makes no network request. It never touches
the main corpus, the workbook, scoring, or any promotion artifact. It writes
only into its own hash-addressed output directory, and it re-verifies that
every input file is byte-identical after the run.

It retires a row from the queue only when the project's own
`membership_is_finalizable` contract accepts a supplied decision. It has no
private notion of "done".

---

## 11. Judgement calls made where the sources were ambiguous

1. **Which candidate-evidence generation.** Two exist. `evidence-20260727r` was
   built from the `-PaulComputer` discovery copies and reports 0 accepted full
   text; `evidence-20260727a` matches the canonical discovery files and the
   handoff's recorded hash. `a` was used, and the builder now *refuses* to run
   unless the evidence receipt's declared discovery hashes match the discovery
   files actually passed in. Mixing generations would show reviewers a
   membership set that does not match the corpus being culled.

2. **How a probe edge is identified.** The 42 `title_alias_probe_*` queries
   carry `membership_non_dispositive: true`, but `credit_union_headline_recall_net`
   does not carry the key at all — it predates the flag. Using the flag alone
   would have wrongly promoted 753 recall-net edges to full category status.
   The rule used is `record_kind != "category"` **or**
   `membership_non_dispositive`, which yields 1,776 probe edges (1,023
   title-alias + 753 recall-net). The handoff's figure of 1,771 counts edges
   *absent from the effective ledger*; five recall-net edges did reach it, and
   753 − 5 = 748 reconciles exactly with the handoff's recorded 748.

3. **The 19-category sweep is per measure, not per membership.** The contract
   requires exactly 19 `category_evidence` rows per candidate and the
   architecture states 352,583 rows, so the sweep necessarily covers categories
   the measure was never returned for. The queue therefore emits a per-measure
   worksheet enumerating all 19 slots alongside the per-membership rows.

4. **Grouping beats strict tier order.** Tier is a property of the query;
   full-text acceptance is a property of the measure. A measure can therefore
   hold edges in several tiers. Keeping a measure's rows contiguous was chosen
   over strict global tier ordering, because the 19-slot sweep has to be done
   once per measure anyway.

5. **`decision_can_finalize_today` is a derived column of this tool**, not a
   contract field. It exists so a reviewer knows whether their decision will
   actually clear the gate or sit unresolved pending collection.

6. **The identifiers that caused a membership had to be reconstructed.**
   `identifier_text` and `source_paragraph` are null on all 64,004 discovery
   membership rows, so the causing terms are extracted from the query's own
   Advanced Search expression and classified under the named-Act rules.

7. **Term matching is reported as navigational only**, with `absence_is_neutral`
   stamped on every match block, because the searched surface is larger than
   the surface held locally.

8. **`--limit` truncates at measure boundaries**, never mid-measure, so a
   partial queue never hands a reviewer an incomplete measure.

9. **Tier outranks explicit naming.** The task asked for scoring category,
   accepted full text, and breadth as the leading signals, so those come first
   and the 55 named outline bills sit at the head of T2 rather than the head of
   the queue. The alternative ordering is one line; both are defensible.

10. **The non-scoring category is still fully queued.** All 1,387
    `credit_union_bank_competitive_dynamics` edges need decisions even though
    the category never feeds scoring, because candidate-wide junk requires
    every membership excluded and the 19-slot sweep is required regardless.

---

## 12. Open questions for Paul

1. **The CRS summary gate.** Nothing corpus-wide has summary status
   `available`, so the only measures that pass the summary gate are the 4,122
   with an officially recorded absence — and none of those have full text.
   Either Congress.gov summaries get collected, or the contract is amended to
   accept GovInfo-sourced summary text with honest provenance. This is a
   contract change, not an implementation detail.

2. **GovInfo full text as evidence of record.** Same shape of question. The
   contract requires congress.gov URLs; GovInfo's 18,406 accepted documents do
   not satisfy it.

3. **The 454 review-ledger edges absent from discovery** (`cdfi_fund` 175,
   `mortgage_lending_housing` 138, `examination_supervision` 67,
   `technology_emerging_issues` 62, `fraud_prevention` 12). By decision, this
   review is driven off discovery, so those 454 are **not** in the queue. If
   they represent real discovery under-coverage they need a separate decision.

4. **Who reviews, and does a model lane propose?** The apparatus supports a
   proposer that cannot self-ratify. Whether to use one, at what cost cap, and
   with what transport, is Paul's call.

5. **Batch size.** Default 250 rows, giving 261 batches over the full queue.

---

## 13. Files and commands

| Artifact | Location |
|---|---|
| Queue builder | `fec/legislation/build_legislation_category_review_queue.py` |
| Tests | `fec/tests/test_build_legislation_category_review_queue.py` |
| Current queue generation | `audits/legfix5/category_review_queue/queues/f397218c236a7c4d8d8b2f562f6fd4b1d24b12159da76e506e76dff8e721e3a8/` |

Inside a generation directory:

| File | Rows | Size | What it is |
|---|---|---|---|
| `queue.ndjson` | 64,004 | 203 MB | one row per membership needing adjudication — authoritative |
| `queue.csv` | 64,004 | 35 MB | the same, reviewer-facing, with blank decision columns |
| `candidate_worksheets.ndjson` | 18,557 | 71 MB | one row per measure, with all 19 audit slots |
| `queries.ndjson` | 62 | 208 KB | the full query definitions, stored once |
| `batches.ndjson` | 261 | 45 KB | the review batches |
| `retired_rows.ndjson` | 0 | 0 | memberships a supplied decision already finalized |
| `manifest.json` | — | 30 KB | sealed generation record |
| `invocations/` | — | — | one record per run, so an idempotent rerun never collides |

The data is stored as three normalised tables — memberships, measures,
queries — so no evidence is repeated 64,004 times. The CSV is the only
denormalised surface, and it deliberately omits the ~900-byte highlighted
search-result URL, which stays in `queue.ndjson` as provenance.

**The queue is bound to a specific candidate-evidence generation.** A
Congress.gov full-text collection run is live; when it finishes, candidate
evidence must be rebuilt and the queue regenerated. That produces a new
`queue_id` by design, and any batch signature taken against the old rows will
correctly stop covering rows whose evidence moved.

Rebuild (idempotent — an identical rerun produces the same `queue_id` and
writes nothing new):

```powershell
& 'C:\Users\pgoff\AppData\Local\Programs\Python\Python312\python.exe' `
  fec\legislation\build_legislation_category_review_queue.py `
  --records     audits\legfix5\congress_advanced_pages_20260725\congress_advanced_records.ndjson `
  --memberships audits\legfix5\congress_advanced_pages_20260725\congress_advanced_memberships.ndjson `
  --queries     audits\legfix5\congress_advanced_pages_20260725\queries `
  --evidence    audits\legfix5\candidate_evidence\evidence-20260727a\candidate_evidence.ndjson `
  --root        audits\legfix5\category_review_queue
```

Optional: `--decisions <ndjson>` (repeatable) to retire already-finalizable
memberships, `--batch-size N`, `--limit N`, `--evidence-receipt <path>`.

Tests:

```powershell
& 'C:\Users\pgoff\AppData\Local\Programs\Python\Python312\python.exe' -m pytest `
  fec\tests\legislation\test_build_legislation_category_review_queue.py -q
```
