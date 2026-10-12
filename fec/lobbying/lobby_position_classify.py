# -*- coding: utf-8 -*-
"""Classify bill positions at full-scope scale, in In Song Kim's label format.

WHY NOT ``lobbying.classify_bill_positions --classify``.  That runner deadlocks on a corpus
this size.  Observed twice: at ``--workers 8`` it froze after 405 items, at
``--workers 4`` after 881, and in the second case sat completely dead for 77
minutes -- manifest last written 13:37:59, process still alive at 14:55.  The API
was verifiably healthy throughout (0.6 s/call, 10k req/min limit, nothing
throttled), so it is a client-side hang, not rate limiting.  Its per-item
``write_json_atomic`` of a growing manifest is also O(n^2): at 22,660 items that is
several GB of rewrites.

This runner keeps everything that matters from that module -- the work-item
contract, the reply parsing, the resumable manifest -- and replaces only the
transport: a bounded worker pool, a hard per-request timeout, capped retries, and
CHECKPOINTING EVERY N ITEMS instead of every item.

LABEL FORMAT IS KIM'S.  Five classes Support / Oppose / Amend / Monitor / ENGAGE,
and every row carries ``label_5_class``, ``label_3_class`` and ``annotation_source``
so the output joins directly against his published dataset (doi:10.7910/DVN/D0QWM2)
rather than living in a private encoding.  The shipped prompt substituted "Mention"
for "Engage", which invites the passive read and measurably suppressed directional
labels; ``--variant`` selects which prompt is used and the choice is recorded in the
receipt.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))

import argparse
import collections
import concurrent.futures
import json
import os
import queue
import sys
import threading
import time

from lobbying import classify_bill_positions as classifier
from lobbying import lobby_position_prompt_ab as prompts

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


CHECKPOINT_EVERY = 250
KIM_LABELS = prompts.KIM_LABELS
THREE_CLASS = {
    "Support": "Support",
    "Oppose": "Oppose",
    "Amend": "Engage",
    "Monitor": "Engage",
    "Engage": "Engage",
    "Mention": "Engage",
}
ANNOTATION_SOURCE = "llm_haiku"


def load_manifest(path, variant):
    if not os.path.isfile(path):
        return {"variant": variant, "model": classifier.MODEL, "completed": {}}
    with open(path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("model") != classifier.MODEL:
        raise SystemExit("manifest model mismatch; move it aside to restart")
    if manifest.get("variant") != variant:
        raise SystemExit(
            f"manifest was built with variant {manifest.get('variant')!r}, "
            f"not {variant!r}; move it aside to restart"
        )
    manifest.setdefault("completed", {})
    return manifest


def save_manifest(path, manifest):
    temp = f"{path}.tmp.{os.getpid()}"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle)
    os.replace(temp, path)


def classify(items, api_key, variant, exemplars, workers, manifest_path, progress=None):
    manifest = load_manifest(manifest_path, variant)
    completed = manifest["completed"]
    # A cached FAILURE is not a result. Resuming must retry it, or one bad spell
    # (an expired key, a network blip) is permanently baked into the output as a
    # null label that looks like "the model had no opinion".
    pending = [
        item
        for item in items
        if (completed.get(classifier.work_item_content_hash(item)) or {}).get("status")
        != "ok"
    ]
    retried = sum(1 for item in items if classifier.work_item_content_hash(item) in completed) - (
        len(items) - len(pending)
    )
    if retried and progress:
        progress(f"  retrying {retried} previously-failed items")
    if progress:
        progress(
            f"  {len(completed)} already done, {len(pending)} pending, "
            f"variant={variant}, workers={workers}"
        )
    if not pending:
        return manifest, {"newly_classified": 0, "failed": 0}

    lock = threading.Lock()
    counters = collections.Counter()
    started = time.time()

    def work(item):
        prompt = prompts.build_prompt(item, variant, exemplars)
        reply = prompts._call(prompt, api_key)
        label = prompts.parse_reply(reply, variant)
        return classifier.work_item_content_hash(item), {
            "label_5_class": label,
            "label_3_class": THREE_CLASS.get(label),
            "annotation_source": ANNOTATION_SOURCE,
            "status": "ok" if label else "unparseable",
            "raw_reply": reply,
        }

    # Circuit breaker. The first full run of this module wrote 1,500 consecutive
    # null labels at 0.3/s and projected a 19-hour ETA before anyone noticed the
    # API key had run out of credit. A run that is failing everything is not a
    # run -- stop and say so rather than spend hours recording nothing.
    CHECK_AFTER = 100
    ABORT_RATE = 0.9
    aborted = [False]

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for digest, receipt in pool.map(work, pending):
            with lock:
                if aborted[0]:
                    continue
                completed[digest] = receipt
                counters["done"] += 1
                if receipt["status"] != "ok":
                    counters["failed"] += 1
                n = counters["done"]
                if n >= CHECK_AFTER and counters["failed"] / n >= ABORT_RATE:
                    aborted[0] = True
                    save_manifest(manifest_path, manifest)
                    raise SystemExit(
                        f"ABORTED: {counters['failed']}/{n} requests failed "
                        f"({counters['failed']/n:.0%}). The API is refusing this "
                        "workload -- check credit balance and key, then re-run "
                        "(completed items resume from the manifest)."
                    )
                # Periodic, not per-item: the manifest is rewritten whole, so
                # checkpointing every row is quadratic in corpus size.
                if n % CHECKPOINT_EVERY == 0:
                    save_manifest(manifest_path, manifest)
                    if progress:
                        rate = n / max(time.time() - started, 1e-9)
                        remaining = (len(pending) - n) / rate if rate else 0
                        progress(
                            f"  {n}/{len(pending)}  {rate:.1f}/s  "
                            f"ETA {remaining/60:.0f} min  failed={counters['failed']}"
                        )
    save_manifest(manifest_path, manifest)
    return manifest, {
        "newly_classified": counters["done"] - counters["failed"],
        "failed": counters["failed"],
    }


def write_results(path, items, manifest):
    completed = manifest["completed"]
    rows = []
    for item in items:
        receipt = completed.get(classifier.work_item_content_hash(item))
        if not receipt or receipt["status"] != "ok":
            continue
        rows.append(
            {
                "lob_id": item["lob_id"],
                "client_name": item["client_name"],
                "bill_key": item["bill_key"],
                "bill_no": item["bill_no"],
                "paragraph": item["paragraph"],
                "report_uuid": item["report_uuid"],
                "filing_year": item["filing_year"],
                "quarter": item["quarter"],
                # Kim's schema, so this joins straight onto his dataset.
                "label_5_class": receipt["label_5_class"],
                "label_3_class": receipt["label_3_class"],
                "annotation_source": receipt["annotation_source"],
                "model": classifier.MODEL,
                "prompt_variant": manifest["variant"],
                "classification_status": receipt["status"],
            }
        )
    temp = f"{path}.tmp.{os.getpid()}"
    with open(temp, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temp, path)
    return rows


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Full-scope bill-position classification in Kim's label format"
    )
    parser.add_argument("--input", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--report", default=None)
    parser.add_argument(
        "--variant", default="kim_zeroshot", choices=list(prompts.ARMS)
    )
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=None)
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    local = os.environ["LOCALAPPDATA"]
    root = os.path.join(local, "Melange", "lda_rebuild", "derived", "interactions")
    input_path = args.input or os.path.join(root, "position_work_items.jsonl")
    output = args.output or os.path.join(root, "bill_positions_kim.jsonl")
    report_path = args.report or os.path.join(root, "bill_positions_kim_report.json")
    manifest_path = output + ".manifest.json"

    items = [json.loads(line) for line in open(input_path, encoding="utf-8") if line.strip()]
    if args.limit:
        items = items[: args.limit]
    api_key = open(
        os.path.join(local, "Melange", "lda_rebuild", "anthropic_key.txt"),
        encoding="utf-8-sig",
    ).read().strip()

    exemplars = ()
    exemplar_receipt = None
    if args.variant == "kim_fewshot":
        calib = os.path.join(local, "Melange", "lda_rebuild", "raw", "positions", "calib_items.jsonl")
        pool = [json.loads(l) for l in open(calib, encoding="utf-8") if l.strip()]
        # The calibration corpus is drawn from these same filings, so an exemplar
        # can carry the answer to an item being scored. Exclude every production
        # key, exactly as the calibration harness excludes its eval sample.
        production_keys = {(i["report_uuid"], i["bill_no"]) for i in items}
        exemplars, exemplar_receipt = prompts.pick_exemplars(pool, production_keys)
        leaked = [
            e for e in exemplars
            if (e["report_uuid"], e["bill_no"]) in production_keys
        ]
        if leaked:
            raise SystemExit(
                f"refusing to run: {len(leaked)} exemplars collide with the "
                "corpus being classified"
            )
        print(f"few-shot exemplars: {exemplar_receipt} "
              f"({len(exemplars)} total, leakage-checked)", flush=True)

    print(f"classifying {len(items)} work items -> {output}", flush=True)
    manifest, run_receipt = classify(
        items,
        api_key,
        args.variant,
        exemplars,
        args.workers,
        manifest_path,
        progress=lambda line: print(line, flush=True),
    )
    rows = write_results(output, items, manifest)

    five = collections.Counter(r["label_5_class"] for r in rows)
    three = collections.Counter(r["label_3_class"] for r in rows)
    n = max(len(rows), 1)
    receipt = {
        "work_items": len(items),
        "classified": len(rows),
        "prompt_variant": args.variant,
        "model": classifier.MODEL,
        "annotation_source": ANNOTATION_SOURCE,
        "label_5_class": dict(five.most_common()),
        "label_3_class": dict(three.most_common()),
        "label_3_class_share": {k: round(v / n, 4) for k, v in three.most_common()},
        "directional_share": round(
            (three.get("Support", 0) + three.get("Oppose", 0)) / n, 4
        ),
        "fewshot_exemplars": exemplar_receipt,
        **run_receipt,
    }
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True)
    print()
    print("five-class :", dict(five.most_common()))
    print("three-class:", dict(three.most_common()))
    print("directional share: %.1f%%" % (100 * receipt["directional_share"]))
    print("wrote", output)
    print("wrote", report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
