# ops/governance

This folder holds the machine-readable side of the data-governance matrix,
`audits/deploy_20261011/GOVERNANCE_MATRIX.md`. That matrix gates who may see what once Melange, Mentat
and Terrier are hosted for invited users (D-164). Nothing here is legal advice.

| File | What it is |
|---|---|
| `invited_profile.json` | The profile for the Melange **invited build**: the four-surface desk (`Moneyball.html`), the `lobby-*.html` family and the reader downloads. It lists what the invited build drops, truncates and attributes. |

## Status: everything is pending

`"pending": true` means Paul has not ruled the matrix rows yet. While that flag is true:

- `ops/release.py` and the Netlify publisher must **refuse** to ship an invited build (fail closed, D-169).
- An invited build may be produced only for local preview and review.
- Any value marked `"ruled": false` is a **recommendation**, not a decision.

**Only Paul-ruled values ship.** When Paul rules a matrix row (Adopt, Stricter or Looser), transcribe the
ruling into the rule that carries that row's `row` id, and set its `"ruled": true` and `"ruled_on"`. Record
the ruling as a brain D-entry. Flip the top-level `"pending"` to false only when every rule is ruled. Never
edit a value without a ruling behind it.

## How the profile is applied (Lane D builds this; nothing here runs yet)

1. `fec/surfaces/make_combined.py --profile ops/governance/invited_profile.json` extracts the four built
   pages exactly as today. Before `assemble()`, it then:
   - honours `drop_surfaces`, e.g. `staffer` → `assemble(pieces, include_staffer=False)`, the existing
     three-surface path at make_combined.py:863-957;
   - parses each data island named in `islands` and applies `drop_paths`, then `redact_fields`;
   - re-serialises with `moneyball_core.json_for_script`;
   - appends the `attribution` footer;
   - writes `Moneyball_Invited.html`. It never overwrites `Moneyball.html`.
2. The lobby builder (`fec/surfaces/make_lobby_network_html.py`) applies the same profile to the
   `lobby-*.html` islands and writes `invited/lobby-*.html`.
3. `lock_dashboard.py --source Moneyball_Invited.html` produces the Netlify artifact (D-171). The VPS
   `desk_server.py` allowlist serves only the invited outputs and `downloads/<id>/`.
4. **Verification gate** (the build must fail if any check fails):
   - every `drop_paths` entry matches 0 nodes in the output, and every one matched at least 1 node in the
     input (so a renamed key fails loudly instead of silently passing). The exceptions are a rule marked
     `"defensive": true`, which guards a key that is absent today, and a `drop_paths_rows` entry marked
     `"optional": true`, for a key that exists only in some builds (e.g. `desk:members[].staffdir`, which
     is absent from a `DESK_SPLIT=1` build). Every `drop_paths` entry must have
     exactly one owner in `drop_paths_rows`;
   - no truncated field exceeds its `max_chars`;
   - no string anywhere in the output matches a local filesystem path (`[A-Za-z]:\`, `\Users\`, `AppData\`,
     `melange-data`). This generic check catches a new leak that no listed path covers (row X-09);
   - the attribution footer is present;
   - rank delta is 0 over every member. The profile is display-only and must not move a rank.
   - The desk renderers must tolerate a dropped key; check each one in a browser render of the invited
     page.

## Path grammar used in the profile

| Form | Meaning |
|---|---|
| `a.b` | dict key `b` under key `a` |
| `a[]` | every element of list `a` |
| `a{}` | every value of dict `a`, whatever its key (e.g. every bill in `bill_lobbying.bills`) |

A `redact_fields` rule may add `"where": {"<sibling key>": [values]}`. The rule then applies only to
elements whose sibling key has one of those values. Paths are relative to the island root named in
`islands`.

## Not covered here

Mentat and Terrier are controlled at the route level, in their own lanes:

- **Mentat:** the read-only allowlist, Access groups and refused export routes.
- **Terrier:** `local_only`, district groups, per-person audit, watermarking and rate limits.

The matrix names the exact mechanism for each of those rows. A Paul ruling on one of them is transcribed
into that app's code and tests, not into this file.
