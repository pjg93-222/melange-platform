# -*- coding: utf-8 -*-
"""A/B the classification prompt against In Song Kim's published labels.

WHY THIS EXISTS.  The shipped prompt is measurably more conservative than Kim's
annotation it is meant to reproduce.  Two causes, both visible in artefacts already
on disk:

1. VOCABULARY.  Kim's five classes are Support / Oppose / Amend / Monitor /
   ENGAGE.  The shipped prompt substitutes "Mention" for "Engage"
   (``THEIR_TO_OUR_LABEL``).  "Mention" invites the passive read -- the text merely
   mentions the bill -- where "Engage" asserts the organisation is working it.  In
   the full-scope run that word absorbed 59.5% of all labels.
2. EXEMPLARS.  The cached few-shot set contains ZERO directional examples
   (``selected_by_group``: Support 0, Oppose 0) because only 6 candidate pairs
   survived its leakage guard.  A prompt whose only worked examples are Mention and
   Amend teaches the model not to call Support.

The measured consequence, from the shipped agreement report: of Kim's 149 gnn
Support cases the shipped prompt called 70 "Engage" -- recall 0.53.  Kim's own
published distribution is 30.5% Support; the shipped prompt produced 6.6% on this
corpus.

WHAT THIS DOES.  Runs candidate prompts over a stratified sample of the 4,515
Kim-labelled calibration items and scores each against Kim.  RECALL is the headline
because it is unaffected by the stratification; precision is reported reweighted to
Kim's true base rate and is labelled as such.  Nothing here writes to the ledger --
it exists to pick a prompt on evidence instead of on taste.

This module does not modify ``lobbying.classify_bill_positions``.  It reuses that module's
API call, reply parsing and label canonicalisation, and defines only the prompt
text that is under test.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))

import argparse
import collections
import concurrent.futures
import json
import os
import random
import string
import sys

from lobbying import classify_bill_positions as classifier

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


IngestError = classifier.IngestError if hasattr(classifier, "IngestError") else Exception

# Kim's own vocabulary. The only difference from the shipped OUR_LABELS is
# Engage in place of Mention -- which is the whole point of the experiment.
KIM_LABELS = ("Support", "Oppose", "Amend", "Monitor", "Engage")
SHIPPED_LABELS = classifier.OUR_LABELS

KIM_PROMPT_TEMPLATE = (
    "Classify the given text that explicitly describes lobbying activities "
    "for bill {bill_no} into one of the five types: 'Support', 'Oppose', "
    "'Amend', 'Monitor', or 'Engage' without explanation.\n"
    "{bill_no} short title: {short_title}\n"
    "{bill_no} official title: {official_title}\n"
    "Text:{paragraph}\n"
    "Answer:"
)
KIM_PROMPT_INSTRUCTION = (
    "Classify the given text that explicitly describes lobbying activities "
    "for bill {bill_no} into one of the five types: 'Support', 'Oppose', "
    "'Amend', 'Monitor', or 'Engage' without explanation."
)

# Kim's dataset is TWO annotation lanes stacked in one column, not one scheme:
#   gnn (196,683 rows) emits only Support / Oppose / ENGAGE -- zero Amend, zero
#       Monitor, and label_5_class == label_3_class on 100% of its rows.
#   llm  (82,421 rows) emits only Support / Oppose / AMEND / MONITOR -- zero
#       Engage.
# So the finer classes live exclusively in the llm lane, and that lane's prompt
# offered FOUR options with no non-committal "Engage" escape. Reproducing the
# useful five-class breakdown means reproducing that four-way forced choice.
KIM_LLM4_LABELS = ("Support", "Oppose", "Amend", "Monitor")
KIM_LLM4_PROMPT_TEMPLATE = (
    "Classify the given text that explicitly describes lobbying activities "
    "for bill {bill_no} into one of the four types: 'Support', 'Oppose', "
    "'Amend', or 'Monitor' without explanation.\n"
    "{bill_no} short title: {short_title}\n"
    "{bill_no} official title: {official_title}\n"
    "Text:{paragraph}\n"
    "Answer:"
)
KIM_LLM4_INSTRUCTION = (
    "Classify the given text that explicitly describes lobbying activities "
    "for bill {bill_no} into one of the four types: 'Support', 'Oppose', "
    "'Amend', or 'Monitor' without explanation."
)
TARGET = classifier.PROMPT_TARGET_TEMPLATE

# Balanced few-shot: the shipped selector asked for these four groups and got
# nothing for the first two. Here they come from the calibration pool, which has
# paragraphs and Kim labels together, held out of the evaluation sample.
FEWSHOT_GROUPS = (
    ("Support", ("Support",)),
    ("Oppose", ("Oppose",)),
    ("Monitor/Amend", ("Monitor", "Amend")),
    ("Engage", ("Engage",)),
)
FEWSHOT_PER_GROUP = 2
# A worked example has to be readable to work as one. Filing descriptions run to
# 19k characters; eight of those produced 86k-character prompts and a 62% request
# failure rate, which scored as "directional exemplars hurt" when it was really
# "the request died". Exemplars are therefore drawn from concise paragraphs.
# This biases exemplars toward shorter filings, which is recorded in the receipt.
EXEMPLAR_MAX_CHARS = 2500

# kim_fewshot_dir is kim_fewshot with REAL Support and Oppose worked examples.
# The first A/B could not test it: every directional case had been spent on the
# evaluation sample, so the "few-shot" arm demonstrated only non-directional
# labels -- and still won. Reserving a handful of directional cases as exemplars
# is the strongest form of the inferential hypothesis and has to be measured
# separately, on the same evaluation items, or the comparison means nothing.
# kim_llm4_cached is kim_llm4 with the SAME SENTENCES in a different order, so the
# filing text becomes a cacheable prefix.
#
# Why the reorder is needed at all: 22,660 work items come from 3,698 distinct
# paragraphs -- the same filing description is classified against 6.13 bills on
# average -- but Kim's layout puts {bill_no} in the opening sentence, so two items
# sharing a paragraph diverge within the first ~15 tokens and share no cacheable
# prefix. Moving the text first makes it the shared prefix. 107 paragraphs clear
# Haiku's 2048-token cache minimum and account for 6.7M redundant input tokens,
# about half the corpus.
#
# This IS a prompt change and it is re-benchmarked against the same 876 pairs
# rather than assumed equivalent.
ARMS = (
    "shipped_zeroshot",
    "kim_zeroshot",
    "kim_fewshot",
    "kim_fewshot_dir",
    "kim_llm4",
    "kim_llm4_fewshot",
    "kim_llm4_cached",
)
# Haiku 4.5's minimum cacheable prefix, established by bisection against the live
# API, not from memory: a 4,014-token block does not cache, a 4,209-token block
# does. (Assuming the widely-quoted 2048 would have marked ~2,200 items for a
# cache that never materialises.)
#
# A cache WRITE costs 1.25x base and only pays back on a subsequent read, so a
# block is marked only when it is both long enough AND known to be reused --
# fan-out is knowable before the run, so there is no reason to guess.
HAIKU_CACHE_MIN_TOKENS = 4096
CHARS_PER_TOKEN = 4.6          # observed on real filing prose
CACHE_MIN_CHARS = int(HAIKU_CACHE_MIN_TOKENS * CHARS_PER_TOKEN)


def _labels_for(arm):
    if arm == "shipped_zeroshot":
        return SHIPPED_LABELS
    if arm in ("kim_llm4", "kim_llm4_fewshot", "kim_llm4_cached"):
        return KIM_LLM4_LABELS
    return KIM_LABELS


def build_cached_blocks(item, reuse_count=2):
    """kim_llm4_cached as Anthropic content blocks: text first, then the ask.

    Returns (blocks, cacheable). ``reuse_count`` is how many work items quote this
    same filing; a block is only marked for caching when it will actually be read
    back, because the write costs 1.25x and a single-use cache is a pure loss.
    """
    paragraph = item["paragraph"]
    text_block = {"type": "text", "text": f"Text:{paragraph}\n"}
    cacheable = len(paragraph) >= CACHE_MIN_CHARS and reuse_count >= 2
    if cacheable:
        text_block["cache_control"] = {"type": "ephemeral"}
    ask = (
        KIM_LLM4_INSTRUCTION.format(bill_no=item["bill_no"])
        + "\n"
        + f"{item['bill_no']} short title: {item.get('short_title') or ''}\n"
        + f"{item['bill_no']} official title: {item.get('official_title') or ''}\n"
        + "Answer:"
    )
    return [text_block, {"type": "text", "text": ask}], cacheable


def _is_fewshot(arm):
    return arm in ("kim_fewshot", "kim_fewshot_dir", "kim_llm4_fewshot")


def _render_target(item):
    return TARGET.format(
        bill_no=item["bill_no"],
        short_title=item.get("short_title") or "",
        official_title=item.get("official_title") or "",
        paragraph=item["paragraph"],
    )


def build_prompt(item, arm, exemplars=(), reuse_count=2):
    if arm == "shipped_zeroshot":
        return classifier.PROMPT_TEMPLATE.format(
            bill_no=item["bill_no"],
            short_title=item.get("short_title") or "",
            official_title=item.get("official_title") or "",
            paragraph=item["paragraph"],
        )
    if arm == "kim_zeroshot":
        return KIM_PROMPT_TEMPLATE.format(
            bill_no=item["bill_no"],
            short_title=item.get("short_title") or "",
            official_title=item.get("official_title") or "",
            paragraph=item["paragraph"],
        )
    if arm == "kim_llm4":
        return KIM_LLM4_PROMPT_TEMPLATE.format(
            bill_no=item["bill_no"],
            short_title=item.get("short_title") or "",
            official_title=item.get("official_title") or "",
            paragraph=item["paragraph"],
        )
    if arm == "kim_llm4_cached":
        return build_cached_blocks(item, reuse_count)[0]
    if not _is_fewshot(arm):
        raise ValueError(f"unknown arm {arm!r}")
    instruction = (
        KIM_LLM4_INSTRUCTION if arm == "kim_llm4_fewshot" else KIM_PROMPT_INSTRUCTION
    ).format(bill_no=item["bill_no"])
    allowed = set(_labels_for(arm))
    # An exemplar answered "Engage" would teach a label this arm cannot emit.
    usable = [ex for ex in exemplars if ex["their_label_5_class"] in allowed]
    prefix = "\n\n".join(
        _render_target(ex) + ex["their_label_5_class"] for ex in usable
    )
    return instruction + "\n\n" + (prefix + "\n\n" if prefix else "") + _render_target(item)


def parse_reply(reply, arm):
    if not isinstance(reply, str):
        return None
    normalized = reply.strip().strip(string.punctuation).strip().casefold()
    return {label.casefold(): label for label in _labels_for(arm)}.get(normalized)


def to_three(label, arm):
    """Collapse to Kim's three-class primary."""
    if label in ("Support", "Oppose"):
        return label
    return "Engage" if label else None


def stratified_sample(items, n_support, n_oppose, n_engage, seed=7):
    """Oversample the rare directional classes so recall is measurable at all."""
    rng = random.Random(seed)
    by_class = collections.defaultdict(list)
    for item in items:
        by_class[item["their_label_3_class"]].append(item)
    sample = []
    for label, want in (
        ("Support", n_support),
        ("Oppose", n_oppose),
        ("Engage", n_engage),
    ):
        pool = sorted(by_class.get(label, []), key=lambda r: r["report_uuid"] + r["bill_no"])
        rng.shuffle(pool)
        sample.extend(pool[:want])
    rng.shuffle(sample)
    return sample


def pick_exemplars(pool, exclude_keys, seed=7, max_chars=EXEMPLAR_MAX_CHARS):
    rng = random.Random(seed)
    chosen = []
    receipt = {}
    # Callers build the pool by concatenating a reserved set with a general one,
    # so the reserved items appear twice; without this the same filing can be
    # shown to the model as two separate worked examples.
    seen_keys = set()
    deduped = []
    for item in pool:
        key = (item["report_uuid"], item["bill_no"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(item)
    pool = deduped
    for group_name, labels in FEWSHOT_GROUPS:
        candidates = [
            item
            for item in pool
            if item["their_label_5_class"] in labels
            and (item["report_uuid"], item["bill_no"]) not in exclude_keys
            and (item.get("paragraph") or "").strip()
        ]
        concise = [c for c in candidates if len(c["paragraph"]) <= max_chars]
        # Fall back to the shortest available rather than dropping a group
        # entirely -- a missing Support exemplar is what caused the original
        # defect, so an over-long one still beats none.
        if concise:
            candidates = concise
            candidates.sort(key=lambda r: r["report_uuid"] + r["bill_no"])
            rng.shuffle(candidates)
        else:
            candidates.sort(key=lambda r: (len(r["paragraph"]), r["report_uuid"]))
        picked = candidates[:FEWSHOT_PER_GROUP]
        chosen.append(picked)
        receipt[group_name] = {
            "selected": len(picked),
            "chars": [len(p["paragraph"]) for p in picked],
        }
    # Round-robin the groups so the prompt never ends on a run of one label --
    # a trailing block of Supports is itself a nudge toward answering Support.
    interleaved = []
    for position in range(FEWSHOT_PER_GROUP):
        for group in chosen:
            if position < len(group):
                interleaved.append(group[position])
    return interleaved, receipt


def score(rows, base_rates):
    """Per-class recall (stratification-safe) and base-rate-reweighted precision."""
    classes = ("Support", "Oppose", "Engage")
    truth_counts = collections.Counter(r["truth3"] for r in rows)
    confusion = {a: collections.Counter() for a in classes}
    unparseable = 0
    for row in rows:
        if row["pred3"] is None:
            unparseable += 1
            continue
        confusion[row["truth3"]][row["pred3"]] += 1
    per_class = {}
    for label in classes:
        support = truth_counts.get(label, 0)
        hit = confusion[label][label]
        recall = hit / support if support else None
        # Reweight predicted mass to Kim's true base rate before computing
        # precision, so a deliberately unbalanced sample cannot flatter it.
        weighted_tp = 0.0
        weighted_fp = 0.0
        for actual in classes:
            n_actual = truth_counts.get(actual, 0)
            if not n_actual:
                continue
            w = base_rates.get(actual, 0.0) / n_actual
            count = confusion[actual][label]
            if actual == label:
                weighted_tp += count * w
            else:
                weighted_fp += count * w
        precision = (
            weighted_tp / (weighted_tp + weighted_fp)
            if (weighted_tp + weighted_fp) > 0
            else None
        )
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision and recall
            else 0.0
        )
        per_class[label] = {
            "n_truth": support,
            "recall": round(recall, 4) if recall is not None else None,
            "precision_reweighted": round(precision, 4) if precision is not None else None,
            "f1_reweighted": round(f1, 4),
        }
    correct = sum(confusion[a][a] for a in classes)
    total = sum(truth_counts.values())
    scored = total - unparseable
    # A failed request scores as a miss, so a broken arm reports low recall and
    # respectable accuracy -- it looks like a bad prompt instead of a dead one.
    # Anything above a few percent is a transport problem and must not be read
    # as a result.
    failure_rate = unparseable / total if total else 0.0
    return {
        "n": total,
        "unparseable": unparseable,
        "failure_rate": round(failure_rate, 4),
        "valid": failure_rate <= 0.05,
        "verdict": (
            "OK" if failure_rate <= 0.05
            else f"INVALID - {unparseable}/{total} requests failed; "
                 "recall here measures the transport, not the prompt"
        ),
        "raw_accuracy_on_stratified_sample": round(correct / scored, 4) if scored else None,
        "per_class": per_class,
        "predicted_mix": dict(
            collections.Counter(r["pred5"] for r in rows if r["pred5"]).most_common()
        ),
    }


def run_arm(arm, sample, exemplars, api_key, workers):
    def one(item):
        # The shipped helper builds its own prompt, so the prompt under test goes
        # through an identical request shape rather than through that helper.
        return _call(build_prompt(item, arm, exemplars), api_key)

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, item): item for item in sample}
        for future in concurrent.futures.as_completed(futures):
            item = futures[future]
            reply = future.result()
            pred5 = parse_reply(reply, arm)
            rows.append(
                {
                    "truth5": item["their_label_5_class"],
                    "truth3": item["their_label_3_class"],
                    "pred5": pred5,
                    "pred3": to_three(pred5, arm),
                    "raw": reply,
                }
            )
    return rows


CACHE_STATS = collections.Counter()


def _call(prompt, api_key, tries=6):
    """prompt is either a string or a list of Anthropic content blocks."""
    import http.client
    import time
    import urllib.error
    import urllib.request

    payload = {
        "model": classifier.MODEL,
        "max_tokens": 16,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    for attempt in range(tries):
        try:
            request = urllib.request.Request(
                classifier.ANTHROPIC_URL,
                data=body,
                headers={
                    "content-type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": classifier.ANTHROPIC_VERSION,
                    "user-agent": "lda-position-prompt-ab/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8"))
            usage = payload.get("usage") or {}
            CACHE_STATS["input"] += usage.get("input_tokens", 0) or 0
            CACHE_STATS["cache_write"] += (
                usage.get("cache_creation_input_tokens", 0) or 0
            )
            CACHE_STATS["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
            CACHE_STATS["calls"] += 1
            blocks = [
                b.get("text")
                for b in payload.get("content") or []
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if len(blocks) == 1 and isinstance(blocks[0], str):
                return blocks[0]
            raise ValueError("unexpected content shape")
        except ValueError:
            return None  # deterministic at temperature 0; a retry re-buys the same reply
        except urllib.error.HTTPError as exc:
            # retry only 408/429/5xx/529; any other 4xx (bad key, credit, request) is final
            if not (exc.code in (408, 429) or 500 <= exc.code < 600):
                return None
            delay = min(2 ** attempt, 8)
            try:
                delay = min(max(float(exc.headers.get("retry-after")), delay), 60)
            except (TypeError, ValueError, AttributeError):
                pass
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            # URLError, TimeoutError, ConnectionError and ssl errors are all OSError
            delay = min(2 ** attempt, 8)
        if attempt == tries - 1:
            return None
        time.sleep(delay)
    return None


def _build_parser():
    parser = argparse.ArgumentParser(description="A/B the position prompt against Kim")
    parser.add_argument("--calib", default=None)
    parser.add_argument("--support", type=int, default=200)
    parser.add_argument("--oppose", type=int, default=30)
    parser.add_argument("--engage", type=int, default=370)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--reserve-directional",
        type=int,
        default=3,
        help="Support/Oppose cases held OUT of evaluation to serve as "
             "directional few-shot exemplars for the kim_fewshot_dir arm",
    )
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    local = os.environ["LOCALAPPDATA"]
    calib = args.calib or os.path.join(
        local, "Melange", "lda_rebuild", "raw", "positions", "calib_items.jsonl"
    )
    items = [json.loads(l) for l in open(calib, encoding="utf-8") if l.strip()]
    base_counts = collections.Counter(i["their_label_3_class"] for i in items)
    total = sum(base_counts.values())
    base_rates = {k: v / total for k, v in base_counts.items()}
    print("Kim calibration corpus:", dict(base_counts), "->",
          {k: "%.3f" % v for k, v in base_rates.items()})

    # Reserve directional cases FIRST, so they can be worked examples without
    # ever appearing in the evaluation. Without this the few-shot arm silently
    # degenerates to non-directional exemplars, which is what happened on the
    # first run.
    rng = random.Random(11)
    reserved = []
    if args.reserve_directional:
        for label in ("Support", "Oppose"):
            pool = sorted(
                (i for i in items if i["their_label_5_class"] == label),
                key=lambda r: r["report_uuid"] + r["bill_no"],
            )
            rng.shuffle(pool)
            reserved.extend(pool[: args.reserve_directional])
    reserved_keys = {(i["report_uuid"], i["bill_no"]) for i in reserved}
    evaluable = [
        i for i in items if (i["report_uuid"], i["bill_no"]) not in reserved_keys
    ]

    sample = stratified_sample(evaluable, args.support, args.oppose, args.engage)
    sample_keys = {(i["report_uuid"], i["bill_no"]) for i in sample}
    # Arm 3: whatever the pool yields after the eval sample is removed (this is
    # the shipped selector's situation). Arm 4: the reserved directional cases
    # plus non-directional fill.
    exemplars, ex_receipt = pick_exemplars(items, sample_keys | reserved_keys)
    dir_exemplars, dir_receipt = pick_exemplars(
        reserved + [i for i in items if (i["report_uuid"], i["bill_no"]) not in sample_keys],
        sample_keys,
    )
    print("reserved for exemplars:",
          dict(collections.Counter(i["their_label_5_class"] for i in reserved)))
    print("eval sample:", len(sample),
          dict(collections.Counter(i["their_label_3_class"] for i in sample)))
    print("kim_fewshot     exemplars:", ex_receipt,
          "->", [e["their_label_5_class"] for e in exemplars])
    print("kim_fewshot_dir exemplars:", dir_receipt,
          "->", [e["their_label_5_class"] for e in dir_exemplars])
    print()

    api_key = open(
        os.path.join(local, "Melange", "lda_rebuild", "anthropic_key.txt"),
        encoding="utf-8-sig",
    ).read().strip()

    results = {}
    arm_exemplars = {
        "kim_fewshot": exemplars,
        "kim_fewshot_dir": dir_exemplars,
    }
    for arm in [a.strip() for a in args.arms.split(",") if a.strip()]:
        print(f"== {arm} ==", flush=True)
        rows = run_arm(arm, sample, arm_exemplars.get(arm, ()), api_key, args.workers)
        results[arm] = score(rows, base_rates)
        s = results[arm]
        print("   verdict:", s["verdict"]); print("   3-class acc (stratified): ", s["raw_accuracy_on_stratified_sample"])
        for label in ("Support", "Oppose", "Engage"):
            c = s["per_class"][label]
            print("   %-8s n=%-4d recall=%-7s prec*=%-7s f1*=%s"
                  % (label, c["n_truth"], c["recall"], c["precision_reweighted"], c["f1_reweighted"]))
        print("   predicted mix:", s["predicted_mix"])
        print(flush=True)

    out = args.out or os.path.join(
        local, "Melange", "lda_rebuild", "derived", "interactions",
        "position_prompt_ab.json",
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "kim_base_rates": base_rates,
                "sample": {
                    "support": args.support,
                    "oppose": args.oppose,
                    "engage": args.engage,
                },
                "fewshot_groups": ex_receipt,
                "fewshot_labels": [e["their_label_5_class"] for e in exemplars],
                "fewshot_dir_groups": dir_receipt,
                "fewshot_dir_labels": [
                    e["their_label_5_class"] for e in dir_exemplars
                ],
                "reserved_directional": dict(
                    collections.Counter(i["their_label_5_class"] for i in reserved)
                ),
                "arms": results,
                "note": (
                    "Recall is stratification-safe. Precision is reweighted to Kim's "
                    "base rates and marked prec*/f1* accordingly."
                ),
            },
            handle,
            indent=2,
            sort_keys=True,
        )
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
