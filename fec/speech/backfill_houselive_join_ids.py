"""Add deterministic artifact/turn IDs to existing HouseLive clip-manifest rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def ids(row: dict) -> tuple[str, str]:
    required = ("date", "package", "seg", "member", "text_sha256")
    missing = [field for field in required if row.get(field) in (None, "")]
    if missing:
        raise RuntimeError("HouseLive row lacks join fields: %s" % ",".join(missing))
    artifact = hashlib.sha256(
        ("houselive\0%s\0%s" % (row["date"], row["package"])).encode("utf-8")
    ).hexdigest()
    turn = hashlib.sha256(
        ("%s\0%d\0%s\0%s" % (
            artifact, int(row["seg"]), row["member"], row["text_sha256"]
        )).encode("utf-8")
    ).hexdigest()
    return artifact, turn


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--expected-updated", type=int, required=True)
    args = parser.parse_args()
    path = args.manifest.resolve()

    output = []
    updated = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(raw)
        if row.get("outlet") != "HouseLive" or row.get("artifact_id"):
            output.append(raw)
            continue
        artifact, turn = ids(row)
        row["artifact_id"] = artifact
        row["turn_id"] = turn
        row["original_ordinal"] = int(row["seg"])
        output.append(json.dumps(row, ensure_ascii=True, sort_keys=True))
        updated += 1
    if updated != args.expected_updated:
        raise RuntimeError("expected to update %d rows, found %d" % (args.expected_updated, updated))

    temp = path.with_name(path.name + ".join-id.tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        for line in output:
            handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    print("updated=%d total=%d" % (updated, len(output)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
