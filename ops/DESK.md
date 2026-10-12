# Melange desk on the server (Lane D)

On the server Melange is the static desk and the lobby pages only: no live staffer console. Two stdlib
files make that:

| File | Job |
|---|---|
| `fec/surfaces/make_invited_build.py` | Post-processes the BUILT `Moneyball.html` and `lobby-*.html` under `ops/governance/invited_profile.json` and writes `outputs/invited_build/`. Changes no chain code. |
| `fec/surfaces/desk_server.py` | Serves that directory read-only behind the Cloudflare Access gate (`fec/access_gate.py`). |

## Build the invited desk

    python fec/surfaces/make_invited_build.py [--input DIR] [--output DIR] [--profile FILE]

Defaults: input is `MONEYBALL_ROOT` (`fec/paths.py`), output is `<input>/outputs/invited_build`, profile is
`ops/governance/invited_profile.json`. It needs a finished desk build first (`make_combined.py` etc.); it never
runs the chain. On the 2026-10-11 pages it takes about 25 s and 1.5 GB.

Output: `Moneyball.html`, `lobby-<slug>.html` (each with a deterministic `.gz` beside it, gzip level 9, mtime 0)
and `INVITED_BUILD.json`. Nothing is written until every page has been built and checked, and the old manifest is
removed first, so a refused build leaves no manifest next to new files. Any error exits 1.

It fails closed on: a missing or unreadable profile, an unknown profile key, a drop or redaction path that
matches nothing (set `"optional": true` on its `drop_paths_rows` entry, or `"defensive": true` on a redaction),
a data island it cannot find exactly once or cannot reproduce byte for byte, an undescribed JSON island or lobby
page, a string that looks like a local path (`X:\` or `X:/`, `\Users\`, `AppData\`, `melange-data`, `OneDrive`, `file:///`), in an
island or anywhere in the output bytes, a missing dropped-page link, a truncation or drop that did not take, an island the
profile describes that is not in its page (only a dropped surface excuses it, and then none of its `SDATA/GDATA/ODATA/CDATA`
bindings may remain), or any JSON value over 4096 bytes bound in a script outside the described islands (the census of the
2026-10-11 pages found 38 bytes at most). With `pending: false`, every object that carries a matrix `row` must say `ruled: true`.
Rules are described in the module docstring.

`INVITED_BUILD.json` holds `"governance": "pending"` while the profile says `"pending": true` (otherwise
`"ruled"`), the profile sha256, the sha256 and size of every input, output and `.gz`, and per-file counts of each
drop and redaction.

## Serve it

    DESK_ROOT=outputs/invited_build python -m fec.surfaces.desk_server

| Variable | Meaning |
|---|---|
| `DESK_ROOT` | required; the invited-build directory (plus `downloads/<id>/<name>` under it) |
| `DESK_PORT` | default 8080 |
| `BIND_HOST` | default `127.0.0.1`; anything else turns the Access gate on |
| `ALLOWED_HOSTS`, `ACCESS_AUD`, `ACCESS_ISSUER`, `ACCESS_JWKS_FILE`, `ACCESS_SERVICE_CNS`, `ACCESS_ENFORCE` | see the `fec/access_gate.py` docstring; the server exits 2 if the gate is enforced and these are missing |
| `GIT_SHA`, `BUILD_TIME`, `SNAPSHOT_ID_FILE` | reported by `/version` |

Routes (GET and HEAD only; other methods 405): `/` is `Moneyball.html`, `/lobby-<slug>.html`,
`/downloads/<id|latest>/<name>` (sent as attachments), `/healthz`, `/version`. Everything else is 404. The path is
never percent-decoded; the resolved file must stay inside `DESK_ROOT`. `<file>.gz` is sent when the client accepts
gzip. One JSON audit line per request goes to stderr: no query string, no client IP.

## How release consumes it

1. Run the build; read `outputs/invited_build/INVITED_BUILD.json`.
2. `release.py`'s `invited_build` gate refuses to ship unless `"governance"` is `"ruled"` (`--governance-override` is for
   Paul-only testing and is recorded), the sha256 of every page and `.gz` matches the manifest, and the inputs are still the
   live pages. `snapshot_manifest.json` carries that governance and the profile sha256.
3. Put the whole directory in the data snapshot as the desk root. `downloads/<id>/` files go beside them; a rebuild leaves
   `downloads/` alone. `desk_server` sends a `.gz` only if it is not older than its page, so the build writes each page first.
4. The Netlify desk and the reader download are a lock of `outputs/invited_build/Moneyball.html` (`publish_dashboard.py
   --source ...`); `release.py` pins `deploy/index.html` only when its receipt names that file as the source, else it ships
   no desk. (`ops/deploy_desk.py` reads the same `outputs/invited_build/` through `core.invited_dir`.)

## Open items for the profile owner (found by running against the 2026-10-11 pages, read-only)

- `desk:members[].staffdir` matches nothing in a `DESK_SPLIT=1` build (the field lives in `desk_profiles`):
  mark its `drop_paths_rows` entry `"optional": true`, or the build stops there.
- The local-path scan finds 12 strings under `efficiency_layers.receipts.artifact_vintages` in the desk island.
  Adding `efficiency_layers.receipts.artifact_vintages{}.path` and
  `efficiency_layers.receipts.artifact_vintages.strict_bill_lobby_indexes[].path` to `drop_paths.desk` clears
  them. On a scratch copy of the profile with those two paths and the `optional` flag, the full build passed.
- With `lobby-interactions.html` dropped, the industry script still holds `INTERACTIONS_FILE`, so a click that
  opens the filing ledger from there lands on a 404. No data is involved. Member links to
  `staffer-network.html` do the same.

Tests: `ops/tests/test_desk_server.py`, `ops/tests/test_make_invited_build.py`.
