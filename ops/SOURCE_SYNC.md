# Source propagation

`main` in the local Melange repository supplies `fec/`, `ops/`, and the other
allowlisted source roots. `codex/mentat-crypto` in the Mentat worktree supplies
`mentat/`. These exact committed trees are filtered into
`pjg93-222/melange-platform` main. The raw Melange repository has a separate
history/routing policy: do not create or force-push its remote main to achieve
this synchronization. Uncommitted work is never exported. Terrier's Geo source
repository is a separate build input; this existing export covers Melange and
Mentat and does not synchronize Geo history.

From the canonical Melange checkout, review and then synchronize:

```powershell
python ops/sync_sources.py
python ops/sync_sources.py --apply
python ops/sync_sources.py --apply --push
```

The first command only reads and prints a diff. `--apply` takes a serialized
lock, builds a filtered commit with the existing platform main as parent,
proves blob identity, and runs the complete exported-source security gate. It
advances local platform main with a compare-and-swap only after those checks.
`--push` additionally verifies the expected remote parent and performs an
ordinary fast-forward push. No force push, tag, release, data copy, restart, or
native installer publication occurs. No data-release signing key is required.
The destination origin and both canonical source refs are pinned in the tool;
changing the routing requires review.

An unchanged source tree exits before building commits or repeating security
scans. A stopped/failed push retains its exact candidate and expected remote
parent in `%LOCALAPPDATA%\Melange\source-sync`; the same command verifies and
retries that candidate without a duplicate scan or commit. If the remote or
local parent has independently changed, the tool stops for review. Preserve
these receipts during recovery; do not force/reset past another publisher.

After a successful manual push, opt in to polling this committed source lane:

```powershell
.\ops\auto\Register-SourceSync.ps1 -Push -WhatIf
.\ops\auto\Register-SourceSync.ps1 -Push
```

The installer requires the matching successful verification/push receipt,
runs as the current interactive limited user every 15 minutes and at logon,
uses the matching windowless Python interpreter (`pythonw.exe`), and ignores overlapping instances. It does not replace
an existing task. Git authentication must already work in that user's session;
the task stores no credential. Omitting `-Push` keeps synchronization local.
Logs and reports are in `%LOCALAPPDATA%\Melange\source-sync`. Pause explicitly
with `Disable-ScheduledTask -TaskName MelangeSourceSync`; resume with
`Enable-ScheduledTask -TaskName MelangeSourceSync` after reviewing any failure.

The source push triggers platform source checks. It makes reviewed source
available to container/native builders; it is not a claim that a server has
pulled an image or that a signed desktop installer exists. Desktop release
preparation and publication are described in `desktop/GITHUB_RELEASES.md`.
The existing signed data/container release path remains `ops/release.py` and
requires its independently configured signing/deployment prerequisites.
