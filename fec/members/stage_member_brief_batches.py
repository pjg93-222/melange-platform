# -*- coding: utf-8 -*-
"""Stage v3 member brief packets into writer batches (one batch file per Haiku writer call).

    python fec/members/stage_member_brief_batches.py [--packets data/members/member_brief_packets_v3.json]
        [--staging C:/melange-data/member_briefs_v3_staging] [--batch-size 15] [--only-ready] [--force]
        [--batch N] [--exemplars fec/members/member_briefs_v3_exemplars.json]

Writes ``<staging>/batches/batch_NNN.json`` ({"items": [{"name", "packet_sha256", "packet"}]}), the shared
``<staging>/avoid_phrases.json`` ({"avoid_phrases": [...]}, removed from each item's packet), and
``<staging>/manifest.json`` (every member and its batch, plus any skipped member and why).  Members are
dealt round-robin from a (chamber, party, name) sort, so every batch mixes chambers and parties and holds
at most ``--batch-size`` members.  The writers save ``<staging>/out/batch_NNN.json`` (a JSON object keyed by
member display name); ``assemble_member_briefs_v3.py`` merges, stamps and validates them.

Exit 2 (nothing written): the packets file is missing or the wrong schema, any packet is not
ready_for_writing (unless ``--only-ready``, which stages the rest and lists the skipped), a packet's
stored packet_sha256 does not match its content, or ``<staging>/out`` already holds writer output (written
against an earlier staging; move it aside, or ``--force``).

Series mode, ``--batch N``: run it after the full staging, once batches 1 to N-1 have been assembled.  It rewrites
only ``batches/batch_NNN.json`` from the same packets file, with the coined-animal registry that
assemble_member_briefs_v3.py grew into ``<staging>/registry.json`` (the exemplars' ``coined_registry`` when there is no
file yet): each packet's ``coined_animals`` is the registry and ``apply_registry`` re-decides ``coin_recommended``, so a
coin from an earlier batch is offered to this one as a ``registry_match``.  The registry is not part of the packet
hash, so ``packet_sha256`` (and every earlier brief's stamp) is unchanged.  Exit 2 also when the batch is not in the
manifest, an earlier batch has not been assembled (``--force`` stages it anyway), or the packets carry coined animals
the registry lacks (the packets were built from other briefs; rebuild them without ``--briefs``).
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fec.members import build_member_brief_packets as P  # noqa: E402

STAGING = Path("C:/melange-data/member_briefs_v3_staging")
REGISTRY_FILE = "registry.json"
BATCH_SCHEMA = "member-brief-batch-v1"
MANIFEST_SCHEMA = "member-brief-batch-manifest-v1"


def party_of(packet: dict) -> str:
    return (packet["member"].get("bio") or {}).get("party") or ""


def restage_batch(args, ready: dict) -> int:
    """Series mode: rewrite one batch file with the registry grown so far (see the module docstring)."""
    staging = Path(args.staging)
    if not (staging / "manifest.json").is_file():
        print("FAIL CLOSED: no %s; stage all batches first (without --batch)" % (staging / "manifest.json"), file=sys.stderr)
        return 2
    batches = P.load_json(staging / "manifest.json")["batches"]
    bid = "batch_%03d" % args.batch
    if bid not in batches:
        print("FAIL CLOSED: %s is not in the manifest (%s)" % (bid, ", ".join(batches)), file=sys.stderr)
        return 2
    held = P.load_json(staging / REGISTRY_FILE) if (staging / REGISTRY_FILE).is_file() else None
    pending = [b for b in batches if b < bid and b not in ((held or {}).get("assembled_batches") or [])]
    if pending and not args.force:
        print("FAIL CLOSED: %s not assembled yet, so its coined animals are not in the registry; assemble %s first (or --force)" % (
            ", ".join(pending), "it" if len(pending) == 1 else "them"), file=sys.stderr)
        return 2
    try:
        registry = held["coined_animals"] if held else (
            P.registry_seed(P.load_json(args.exemplars)) if Path(args.exemplars).is_file() else [])
    except ValueError as error:
        print("FAIL CLOSED: %s" % error, file=sys.stderr)
        return 2
    absent = [n for n in batches[bid] if n not in ready]
    ghosts = {e["animal"] for n in batches[bid] if n in ready for e in ready[n].get("coined_animals") or []} - {e["animal"] for e in registry}
    if absent or ghosts:
        print("FAIL CLOSED: %s" % (("%s lists members that are not ready packets any more: %s" % (bid, ", ".join(absent[:20]))) if absent else
              "the packets carry coined animals the registry lacks (%s); rebuild the packets without --briefs" % ", ".join(sorted(ghosts))), file=sys.stderr)
        return 2
    items = []
    for n in batches[bid]:
        packet = copy.deepcopy(ready[n])
        packet.pop("avoid_phrases", None)
        packet["coined_animals"] = copy.deepcopy(registry)
        P.apply_registry(packet, registry)                       # changes coin_recommended.value/registry_match, which are not hashed
        if P.content_sha256(packet) != ready[n]["packet_sha256"]:
            print("FAIL CLOSED: the registry changed the packet hash of %s" % n, file=sys.stderr)
            return 2
        items.append({"name": n, "packet_sha256": ready[n]["packet_sha256"], "packet": packet})
    P.dump_atomic({"schema": BATCH_SCHEMA, "batch": bid, "items": items}, staging / "batches" / (bid + ".json"))
    print("restaged %s: %d members, registry of %d coined animals" % (bid, len(items), len(registry)))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--packets", type=Path, default=P.PACKETS_PATH)
    ap.add_argument("--staging", type=Path, default=STAGING)
    ap.add_argument("--batch-size", type=int, default=15, help="most members in one batch (default 15)")
    ap.add_argument("--only-ready", action="store_true", help="skip packets that are not ready_for_writing instead of refusing")
    ap.add_argument("--force", action="store_true", help="stage even though <staging>/out already holds writer output "
                                                         "(or, with --batch, an earlier batch is not assembled)")
    ap.add_argument("--batch", type=int, help="series mode: rewrite only this batch, with the registry grown so far")
    ap.add_argument("--exemplars", type=Path, default=P.EXEMPLARS_PATH, help="registry seed for --batch before any assemble")
    args = ap.parse_args(argv)
    if args.batch_size < 1:
        ap.error("--batch-size must be at least 1")
    if not Path(args.packets).is_file():
        print("FAIL CLOSED: missing %s" % args.packets, file=sys.stderr)
        return 2
    doc = P.load_json(args.packets)
    if doc.get("schema") != P.SCHEMA or not isinstance(doc.get("members"), dict):
        print("FAIL CLOSED: %s is not a %s file" % (args.packets, P.SCHEMA), file=sys.stderr)
        return 2
    packets = doc["members"]

    skipped = {n: p.get("not_ready_reasons") or ["not ready_for_writing"] for n, p in packets.items() if not p.get("ready_for_writing")}
    if skipped and not args.only_ready:
        print("FAIL CLOSED: %d packet(s) are not ready_for_writing (--only-ready stages the rest):" % len(skipped), file=sys.stderr)
        for name in sorted(skipped)[:20]:
            print("  %s: %s" % (name, "; ".join(skipped[name])), file=sys.stderr)
        return 2
    ready = {n: p for n, p in packets.items() if n not in skipped}
    stale = sorted(n for n, p in ready.items() if p.get("packet_sha256") != P.content_sha256(p))
    if stale or not ready:
        print("FAIL CLOSED: %s" % ("packet_sha256 does not match the packet content for: " + ", ".join(stale[:20]) if stale else "no ready packet to stage"),
              file=sys.stderr)
        return 2

    if args.batch is not None:
        return restage_batch(args, ready)
    staging = Path(args.staging)
    earlier = sorted(p.name for p in (staging / "out").glob("batch_*.json"))
    if earlier and not args.force:       # the assembler stamps outputs with THIS staging's hashes, so old ones would lie
        print("FAIL CLOSED: %s holds writer output from an earlier staging (%s); move it aside, or pass --force" % (
            staging / "out", ", ".join(earlier[:5])), file=sys.stderr)
        return 2

    ordered = sorted(ready, key=lambda n: (ready[n]["member"]["chamber"], party_of(ready[n]), n))
    count = math.ceil(len(ordered) / args.batch_size)
    batches = {"batch_%03d" % (i + 1): ordered[i::count] for i in range(count)}     # deal, so each batch samples every group

    members = {}
    for bid, names in batches.items():
        items = []
        for n in names:
            items.append({"name": n, "packet_sha256": ready[n]["packet_sha256"],
                          "packet": {k: v for k, v in ready[n].items() if k != "avoid_phrases"}})
            members[n] = {"batch": bid, "packet_sha256": ready[n]["packet_sha256"], "chamber": ready[n]["member"]["chamber"],
                          "party": party_of(ready[n])}
        P.dump_atomic({"schema": BATCH_SCHEMA, "batch": bid, "items": items}, staging / "batches" / (bid + ".json"))

    # The validator enforces the packets' avoid_phrases, so writers always see those; phrases an earlier
    # assemble wrote back stay too (it overwrites the file with a fresh list each run).
    avoid_path = staging / "avoid_phrases.json"
    avoid = [a for p in ready.values() for a in p.get("avoid_phrases") or []]
    if avoid_path.is_file():
        avoid = (P.load_json(avoid_path).get("avoid_phrases") or []) + avoid
    P.dump_atomic({"avoid_phrases": list(dict.fromkeys(avoid))}, avoid_path)

    (staging / "out").mkdir(parents=True, exist_ok=True)
    P.dump_atomic({
        "schema": MANIFEST_SCHEMA, "created_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "packets": str(Path(args.packets).resolve()), "packets_sha256": P.sha256_file(args.packets), "batch_size": args.batch_size,
        "counts": {"staged": len(ready), "skipped": len(skipped), "batches": count},
        "batches": batches, "members": members, "skipped": skipped}, staging / "manifest.json")
    print("staged %d members in %d batches (%s) under %s" % (len(ready), count, ", ".join(str(len(v)) for v in batches.values()), staging))
    for name in sorted(skipped):
        print("skipped %s: %s" % (name, "; ".join(skipped[name])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
