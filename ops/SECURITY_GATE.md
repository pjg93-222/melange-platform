# Security gate

`python ops/security_gate.py {export,packages,farm,images,all} [--ci] ...` is the fail-closed check on everything that
leaves the laptop (plan R2). Stdlib only. Exit 0 only if every
required check for the stage ran and passed.

**Fail closed.** A missing tool, a tool error, unparseable tool output, a missing required argument, a vulnerability
DB older than 24 h (trivy reports its age) or a bug in the gate itself is a FAIL that names the tool. The only SKIP
is `exact_secrets` under `--ci`. Output is one `PASS|FAIL|SKIP stage/check: reason` line per check; secret values are
never printed (gitleaks `Secret`/`Match` fields are never read; exact-secret hits print the source name only).

| Stage | Required checks |
|---|---|
| `export` | gitleaks (`detect --no-git`, JSON), data_leak, exact_secrets (local only) on each `--target` dir |
| `packages` | safe_unpack each archive (`.zip` `.tar.gz` `.tgz`; no traversal, absolute paths, links or special files), then gitleaks + data_leak + exact_secrets on the contents; a directory target is scanned and its archives unpacked |
| `farm` | the release's data farm: denied_names (`DENY_NAMES` plus `ops/release_lib/data.py` `DENY_PATTERNS`, on files and directories) and exact_secrets (local only). No data_leak: data files are expected there |
| `images` | trivy per `--image REF@sha256:...` (`vuln,misconfig,secret`, HIGH+CRITICAL, `--ignore-unfixed`: D-181; licences in their own run, language packages block and OS packages are reported: D-182) and compose_policy on `--compose-json` (`docker compose config --format json`) |
| `all` | export + deps (`pip-audit -r <lock> --require-hashes --strict`, `npm audit --omit=dev --audit-level=high` in `<terrier-dir>/frontend`, osv-scanner) + static (bandit HIGH, semgrep `p/python p/django p/secrets` + `ops/security/semgrep`) + terrier (`manage.py check --deploy --fail-level WARNING` and a hosted-mode no-local-admin assert, both with `CU_DEPLOYMENT=hosted` and dummy non-secret env) + images |

Tools are found on PATH, then in `ops/security/bin/` (gitignored). Versions used are printed and written to the
report. Pin versions in CI; the gate does not install anything.

**data_leak** fails on: denied extensions (databases, tables, workbooks, models, pickles, jsonl, audio/video, and
containers and dumps the gate cannot read: `.gz .tgz .tar .7z .bz2 .xz .rar .rds .rda .RData .feather .npz .sql .bak`; and
`.zip` unless it is a top-level kit file whose hash is in `SHA256SUMS`); denied names (`keys.json`, `*key*.txt`,
`dashboard_password*`, `.secret`, `*salt*`, `.demo-key`, `*.dpapi`, `.env*`, and gitleaks config files, which could
silence the scan); files over `--max-file-mb` (default 5); symlinks and `.git` inside a target; an SSN pattern, or
more than 10 non-allowlisted emails or phone numbers in one text file (counts and paths only); canaries from
`ops/security/canaries.txt`; and any HTML under a `downloads/` or `deploy/` segment that is not a
`lock_dashboard.py` envelope (marker plus incompressible ciphertext plus a small wrapper). A verified envelope is
exempt from the size cap. Limits: only `packages` unpacks archives (`export` rejects `.zip` outright and does not look
inside `.tar.gz`); PII is a regex sample, not DLP.

**exact_secrets** (local runs only) loads values at run time from `keys.json` (whitespace-free, non-URL string
leaves), live `fec/*key*.txt` and `fec/icloud_smtp.txt`, the dashboard password file
(`DASHBOARD_PASSWORD_FILE` or `%LOCALAPPDATA%\Melange\dashboard_password.txt`) and the API-key env vars. Values under
12 characters are ignored; UTF-8 and UTF-16LE forms are searched, binary-safe, every needle in one pass per file. With
`--scan-cache FILE` (the release passes `releases\scan_cache.sqlite` for the farm) a file already scanned clean (same path, size,
mtime_ns, same needle set) is not read again; the cache holds a sha256 fingerprint of the sorted needle hashes, never a needle. Zero loadable values is a FAIL.
Every file source it names must exist and parse, otherwise the check fails naming it (environment variables may simply be
unset; `--ci` is the only opt-out). It also reads the Netlify DPAPI file (`%LOCALAPPDATA%\Melange\secrets\netlify.dpapi`)
and the Credential Manager targets `melange-restic`, `melange-svc-push`, `melange-svc-status`. Those three are local-only:
absent is a FAIL once `ops/release_config.json` puts them to use (`netlify.site_id` set for the token file, `domain` set for
the credentials) and otherwise a "not configured" note in the PASS line. Values are never printed.

**safe_unpack** also refuses an archive inside an unpacked archive (it would never be scanned, and an archive could carry its own
`SHA256SUMS`). **terrier_no_local_admin** accepts exactly deploy/terrier-hosted's startup refusal (`RuntimeError: CU_LOCAL_ADMIN=1
is refused in a hosted deployment ...`, exit 3) or a clean start that exits 0 printing `ok HOSTED=True LOCAL_ADMIN_ACCESS=False`;
a message that merely contains "admin" fails.

**trivy and unfixed findings (D-181, 2026-10-12).** The blocking run is `trivy image --severity HIGH,CRITICAL --ignore-unfixed`, so the gate
fails only on HIGH/CRITICAL vulnerabilities that have a fixed version (and on misconfig and secret findings, which have no
"unfixed" notion). A second, report-only run (`--scanners vuln`, no `--ignore-unfixed`) counts the HIGH/CRITICAL vulnerabilities
with no `FixedVersion`; the count is printed as `INFO images/trivy: <image> unfixed, not blocking (D-181): CRITICAL n, HIGH n` and
written to the `--report` JSON as `unfixed` (`{<REF@sha256:...>: {"CRITICAL": n, "HIGH": n}}`). That run never fails the gate: if it
cannot complete, the entry is `{"error": ...}` and the blocking result stands. Base images are Debian 13 (trixie), pinned in
`ops/images.lock`; a vulnerability left unfixed upstream is reported on every release and is Paul's to accept or not.

**trivy licences (D-182, 2026-10-12).** Licences have their own run per image (`--scanners license --severity CRITICAL,HIGH,UNKNOWN`), and
the gate splits the findings by class. A licence finding on an **OS package** (Debian GPL, LGPL and the like) is reported, never blocking:
trivy's licence scan puts them in the result with Target `OS Packages` (a result with Class `os-pkgs`, or a Type that is a distro such as
`debian`, counts too). They are printed as `INFO images/trivy: <image> OS-package licences, not blocking (D-182): CRITICAL n, HIGH n, UNKNOWN n`
and written to the `--report` JSON as `os_licenses` (`{<REF@sha256:...>: {"CRITICAL": n, "HIGH": n, "UNKNOWN": n}}`). A licence finding on
anything else, i.e. a **language package** we add (pip, npm) that is forbidden (CRITICAL), restricted (HIGH) or unknown (UNKNOWN), blocks
as a finding named `license <licence> <package> <severity>`. One reading of "unknown" is fixed in code (`KNOWN_PERMISSIVE`): a licence
trivy cannot categorise (so it says UNKNOWN) but that has an SPDX id and is permissive, today `MIT-0` (cffi) and `PSF-2.0` (the Python
Software Foundation License 2.0: typing-extensions), is printed as
`INFO ... known permissive that trivy cannot categorise` and written to the report as `known_licenses`; add a name only after reading its
licence. A result that is not recognisably an OS-package result also blocks (fail closed). The licence run is fail closed too: a trivy error or an unreadable report fails the gate. The images are private and never
redistributed; if one is distributed outside Paul's accounts, or a language-package licence slips through, D-182 is reopened.
Terrier's own release workflow (the Geo repo, job `scan`) applies the same policy with a stand-alone copy of this stage, `tools/image_gate.py`; change the rules in both.

**compose_policy**: every service except `cloudflared` needs `read_only: true`, `cap_drop: [ALL]`,
`no-new-privileges:true` and a non-root `user`; no service may publish a port except on a loopback address, run
`privileged`, or use `network_mode: host`; every bind mount under `/srv/data` must be read-only.

**Local vs CI.** Local runs add exact_secrets and check each exception's `decision` against the brain's
`DECISIONS.md`. `--ci` skips both. The local pre-push run is the real control (private repos on Free/Pro have no
GitHub code scanning).

**Exceptions** live in `ops/security/exceptions.toml` (13 gitleaks entries under D-176 until 2026-11-11). See the commented example there. Each needs
`id stage check match reason approver expires decision`; approver must be `Paul`; HIGH/CRITICAL expiry is at most
30 days out (optional `severity`, default HIGH; others 90; always HIGH for gitleaks, data_leak, denied_names and exact_secrets);
a `match` made only of wildcards is refused; an expired or malformed entry fails the gate and
suppresses nothing. An exception hides only the findings its `match` glob selects; tool and infrastructure failures
cannot be excepted.

Tests: `python -m unittest ops.tests.test_security_gate -v`.
