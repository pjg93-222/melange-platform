# -*- coding: utf-8 -*-
"""Benchmark our classifier against In Song Kim's PUBLISHED END RESULTS.

The earlier harness scored prompts against a stratified sample of the calibration
join.  That answers "does the model agree with Kim on hand-picked items".  This
answers the question that actually matters: **run our pipeline over the same
(organisation, bill) pairs Kim published, and do we land where he landed?**

THE BENCHMARK SET is the intersection of Kim's dataset (doi:10.7910/DVN/D0QWM2,
279,104 pairs) with our full-scope work items (5,045 pairs): **876 pairs**, carrying
4,697 of our activity-level work items.  At natural base rates -- no stratification,
no reweighting, no oversampling of the rare classes.

TWO ANNOTATION LANES, NOT ONE SCHEME.  Kim's ``label_5_class`` column stacks two
annotators with different label spaces, and conflating them corrupts every
five-class metric:

    gnn  541 pairs here   Support 7.9%   Oppose 0.6%   Engage 91.5%
                          Amend 0, Monitor 0 -- structurally impossible
    llm  335 pairs here   Support 12.5%  Oppose 1.2%   Amend 6.9%  Monitor 79.4%
                          Engage 0 -- this lane never declines to refine

So the useful five-class breakdown lives ONLY in the llm lane, and that lane's
prompt offered a FOUR-way forced choice with no "Engage" escape hatch
(``kim_llm4``).  Three-class agreement is scored over all 876 pairs; five-class
agreement is scored over the 335 llm pairs and over four labels.  An "Amend F1 of
0.00" computed across both lanes is an artefact of the gnn label space, not a
statement about whether Amend is learnable.

GRAIN.  Kim publishes ONE label per pair; we produce one per (paragraph, bill), and
a pair can carry many paragraphs across years.  Aggregating our labels up to his
grain therefore requires a rule, and the rule changes the answer, so both are
computed and reported:

  strongest -- any Support makes the pair Support, any Oppose makes it Oppose
               (ties broken by count). Reads as "did this org ever take a
               position on this bill", which is the inferential reading.
  majority  -- the modal label. Reads as "what did this org mostly do".

Neither is privileged here.  Reporting one silently would be the kind of choice
that manufactures whichever conclusion was wanted.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))

import argparse
import collections
import concurrent.futures
import csv
import json
import os
import sys
import threading
import time

from lobbying import classify_bill_positions as classifier
from lobbying import lobby_position_prompt_ab as prompts

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

csv.field_size_limit(10 ** 9)

THREE = {
    "Support": "Support",
    "Oppose": "Oppose",
    "Amend": "Engage",
    "Monitor": "Engage",
    "Engage": "Engage",
    "Mention": "Engage",
}
CLASSES3 = ("Support", "Oppose", "Engage")
CLASSES5 = ("Support", "Oppose", "Amend", "Monitor", "Engage")
# The finer classes exist ONLY in Kim's llm lane. His gnn lane emits Support /
# Oppose / Engage and nothing else -- label_5_class == label_3_class on 100% of
# its rows -- so scoring Amend or Monitor against a gnn pair measures nothing but
# the annotator's label space. Five-class agreement is therefore reported on the
# llm subset alone, and an "Amend F1 of 0.00" computed over both lanes together
# is an artefact, not a finding.
CLASSES_LLM4 = ("Support", "Oppose", "Amend", "Monitor")
FIVE_CLASS_SOURCE = "llm"


def load_kim(path):
    kim = {}
    with open(path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            kim[(row["lob_id"], row["bill_id"])] = {
                "label_3_class": row["label_3_class"],
                "label_5_class": row["label_5_class"],
                "annotation_source": row["annotation_source"],
                "client_name": row["client_name"],
            }
    return kim


def benchmark_items(work_items, kim):
    by_pair = collections.defaultdict(list)
    for item in work_items:
        pair = classifier.work_item_source_pair(item)
        if pair in kim:
            by_pair[pair].append(item)
    return by_pair


def aggregate(labels, rule):
    """Collapse many activity-level labels for one pair into one pair label."""
    labels = [l for l in labels if l]
    if not labels:
        return None
    counts = collections.Counter(labels)
    if rule == "majority":
        # deterministic tie-break: rarest-class-first ordering
        order = {label: index for index, label in enumerate(CLASSES5)}
        return min(counts.items(), key=lambda kv: (-kv[1], order.get(kv[0], 99)))[0]
    if rule != "strongest":
        raise ValueError(rule)
    support = counts.get("Support", 0)
    oppose = counts.get("Oppose", 0)
    if support or oppose:
        if support and oppose:
            return "Support" if support >= oppose else "Oppose"
        return "Support" if support else "Oppose"
    for label in ("Amend", "Monitor", "Engage", "Mention"):
        if counts.get(label):
            return label
    return labels[0]


def score_pairs(pairs, truth_key, classes):
    """Plain per-class precision/recall at natural base rates. No reweighting."""
    confusion = {a: collections.Counter() for a in classes}
    scored = 0
    for record in pairs:
        truth = record["kim"][truth_key]
        pred = record["pred"]
        if pred is None or truth not in confusion:
            continue
        confusion[truth][pred] += 1
        scored += 1
    per_class = {}
    for label in classes:
        tp = confusion[label][label]
        fn = sum(confusion[label][g] for g in classes if g != label)
        fp = sum(confusion[a][label] for a in classes if a != label)
        recall = tp / (tp + fn) if (tp + fn) else None
        precision = tp / (tp + fp) if (tp + fp) else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision and recall
            else 0.0
        )
        per_class[label] = {
            "n_kim": tp + fn,
            "n_ours": tp + fp,
            "recall": round(recall, 4) if recall is not None else None,
            "precision": round(precision, 4) if precision is not None else None,
            "f1": round(f1, 4),
        }
    correct = sum(confusion[a][a] for a in classes)
    return {
        "scored_pairs": scored,
        "accuracy": round(correct / scored, 4) if scored else None,
        "per_class": per_class,
        "confusion": {a: dict(confusion[a]) for a in classes},
    }


def distribution(values, classes):
    counts = collections.Counter(v for v in values if v)
    total = sum(counts.values()) or 1
    return {
        label: {
            "n": counts.get(label, 0),
            "share": round(counts.get(label, 0) / total, 4),
        }
        for label in classes
    }


def _cache_path(root, arm):
    return os.path.join(root, f"benchmark_cache_{arm}.json")


def classify_arm(arm, items, exemplars, api_key, workers, cache_file, progress=None):
    cache = {}
    if os.path.isfile(cache_file):
        with open(cache_file, encoding="utf-8") as handle:
            cache = json.load(handle)
    pending = [
        item
        for item in items
        if classifier.work_item_content_hash(item) not in cache
    ]
    if progress:
        progress(f"    {len(cache)} cached, {len(pending)} to call")
    if pending:
        lock = threading.Lock()
        done = [0]
        started = time.time()

        def work(item):
            reply = prompts._call(prompts.build_prompt(item, arm, exemplars), api_key)
            return classifier.work_item_content_hash(item), prompts.parse_reply(reply, arm)

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            for digest, label in pool.map(work, pending):
                with lock:
                    cache[digest] = label
                    done[0] += 1
                    if done[0] % 400 == 0:
                        tmp = cache_file + ".tmp"
                        with open(tmp, "w", encoding="utf-8") as handle:
                            json.dump(cache, handle)
                        os.replace(tmp, cache_file)
                        if progress:
                            rate = done[0] / max(time.time() - started, 1e-9)
                            progress(
                                f"    {done[0]}/{len(pending)} {rate:.1f}/s "
                                f"ETA {(len(pending)-done[0])/max(rate,1e-9)/60:.0f} min"
                            )
        tmp = cache_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(cache, handle)
        os.replace(tmp, cache_file)
    failures = sum(1 for item in items if cache.get(classifier.work_item_content_hash(item)) is None)
    return cache, failures


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Benchmark our classifier against Kim's published end results"
    )
    parser.add_argument(
        "--arms",
        default="shipped_zeroshot,kim_zeroshot,kim_llm4,kim_llm4_fewshot",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit-pairs", type=int, default=None)
    parser.add_argument("--out", default=None)
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    local = os.environ["LOCALAPPDATA"]
    root = os.path.join(local, "Melange", "lda_rebuild", "derived", "interactions")
    positions = os.path.join(local, "Melange", "lda_rebuild", "raw", "positions")

    work_items = [
        json.loads(line)
        for line in open(os.path.join(root, "position_work_items.jsonl"), encoding="utf-8")
        if line.strip()
    ]
    kim = load_kim(
        os.path.join(positions, "bill_position_dataset_input", "bill_position_dataset.csv")
    )
    by_pair = benchmark_items(work_items, kim)
    pairs = sorted(by_pair)
    if args.limit_pairs:
        pairs = pairs[: args.limit_pairs]
    items = [item for pair in pairs for item in by_pair[pair]]
    print(f"BENCHMARK SET: {len(pairs)} pairs, {len(items)} work items")
    print("Kim end results on these pairs:")
    print("  3-class:", distribution([kim[p]["label_3_class"] for p in pairs], CLASSES3))
    print("  5-class:", distribution([kim[p]["label_5_class"] for p in pairs], CLASSES5))
    print()

    api_key = open(
        os.path.join(local, "Melange", "lda_rebuild", "anthropic_key.txt"),
        encoding="utf-8-sig",
    ).read().strip()

    # Exemplars for the directional few-shot arm, drawn from the calibration pool
    # and held clear of every pair in the benchmark.
    calib = [
        json.loads(l)
        for l in open(os.path.join(positions, "calib_items.jsonl"), encoding="utf-8")
        if l.strip()
    ]
    benchmark_keys = {(i["report_uuid"], i["bill_no"]) for i in items}
    dir_exemplars, dir_receipt = prompts.pick_exemplars(calib, benchmark_keys)
    print("directional exemplars:", json.dumps(dir_receipt))

    results = {}
    for arm in [a.strip() for a in args.arms.split(",") if a.strip()]:
        print(f"\n== {arm} ==", flush=True)
        exemplars = dir_exemplars if prompts._is_fewshot(arm) else ()
        cache, failures = classify_arm(
            arm, items, exemplars, api_key, args.workers,
            _cache_path(root, arm), progress=lambda s: print(s, flush=True),
        )
        failure_rate = failures / max(len(items), 1)
        arm_result = {
            "failed_items": failures,
            "failure_rate": round(failure_rate, 4),
            "valid": failure_rate <= 0.05,
        }
        if failure_rate > 0.05:
            arm_result["verdict"] = (
                f"INVALID - {failures}/{len(items)} requests failed"
            )
            results[arm] = arm_result
            print("   ", arm_result["verdict"])
            continue
        for rule in ("strongest", "majority"):
            records = []
            for pair in pairs:
                labels = [
                    cache.get(classifier.work_item_content_hash(i))
                    for i in by_pair[pair]
                ]
                records.append(
                    {
                        "kim": kim[pair],
                        "pred": aggregate(labels, rule),
                        "source": kim[pair]["annotation_source"],
                    }
                )
            # Five-class: llm-annotated pairs only, and scored over the four
            # labels that lane can actually produce.
            llm_records = [r for r in records if r["source"] == FIVE_CLASS_SOURCE]
            r5 = score_pairs(llm_records, "label_5_class", CLASSES_LLM4)
            five_dist = distribution([r["pred"] for r in llm_records], CLASSES5)
            kim_five = distribution(
                [r["kim"]["label_5_class"] for r in llm_records], CLASSES5
            )
            # Three-class: every pair, both lanes.
            for record in records:
                record["pred"] = THREE.get(record["pred"])
            r3 = score_pairs(records, "label_3_class", CLASSES3)
            arm_result[rule] = {
                "three_class_all_pairs": r3,
                "five_class_llm_pairs_only": r5,
                "five_class_pairs_scored": len(llm_records),
                "our_distribution_3": distribution(
                    [r["pred"] for r in records], CLASSES3
                ),
                "our_distribution_5_llm_pairs": five_dist,
                "kim_distribution_5_llm_pairs": kim_five,
            }
            s = r3["per_class"]["Support"]
            print(
                "   %-9s 3cls acc %-7s Support rec %-6s prec %-6s | 5cls acc %-7s "
                "(n=%d llm pairs)  Amend rec %-6s Monitor rec %s"
                % (
                    rule, r3["accuracy"], s["recall"], s["precision"],
                    r5["accuracy"], len(llm_records),
                    r5["per_class"]["Amend"]["recall"],
                    r5["per_class"]["Monitor"]["recall"],
                )
            )
        results[arm] = arm_result

    out = args.out or os.path.join(root, "position_benchmark_vs_kim.json")
    payload = {
        "benchmark_pairs": len(pairs),
        "benchmark_work_items": len(items),
        "kim_distribution_3": distribution([kim[p]["label_3_class"] for p in pairs], CLASSES3),
        "kim_distribution_5": distribution([kim[p]["label_5_class"] for p in pairs], CLASSES5),
        "kim_annotation_source": dict(
            collections.Counter(kim[p]["annotation_source"] for p in pairs)
        ),
        "directional_exemplars": dir_receipt,
        "arms": results,
        "aggregation_note": (
            "Kim publishes one label per (org, bill); we produce one per "
            "(paragraph, bill). Both aggregation rules are reported because the "
            "choice moves the answer."
        ),
    }
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    print("\nwrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
