# Public GitHub desktop updates

The Electron apps can use public GitHub Releases. Each app has its own public
repository (Paul's correction, 2026-10-15): Melange `pjg93-222/Melange`, Mentat
`pjg93-222/Mentat`, Terrier `pjg93-222/Geo`. The combined `melange-platform`
repository is no longer a destination. Binary update checks are independent of application
feature approval. The app still verifies an Ed25519 manifest against its
independently installed update roots, exact app/OS/architecture, increasing
sequence/version, installer hashes, and the native OS signer before activation.
Feature access keeps its separate signed approval checks. No GitHub PAT,
publisher private key, access credential, or recipient list belongs in an app
or release asset. Making the repository public is an owner action; none of the
tools here change repository visibility.

> **Signing prerequisite.** `packaging/github_release.py` now prepares each app's
> signed release for that app's own repository (below). Producing a real signed
> release still needs Paul's Windows Authenticode identity on each installer and
> the per-app Ed25519 update roots and pinned Windows publisher subject enrolled
> in the protected publisher state (`server/desktop_admin.py` `init` and
> `trust-publisher`). Neither is part of this repository, and no tool here creates
> them. Until both exist, per-app releases remain unsigned manual-install previews.

> **Self-signed route (Paul's choice, 2026-10-16).** The Authenticode identity is a
> self-signed code-signing certificate whose non-exportable key stays in Paul's
> `CurrentUser\My`; only machines that import its public certificate into `Root`
> report its signatures as `Valid`, so only those machines install updates. The
> hosted build stays unsigned but bakes the public pin
> `WINDOWS_PUBLISHER_NAME = CN=Melange Systems Desktop Release (self-signed)`
> (workflow `RELEASE_PUBLISHER`, signed-release input mode, with `STABLE_TAG`
> reserved). On Paul's PC the installer is then signed (RFC 3161 timestamp),
> `packaging/authenticode_binding.py` proves the signed file is the hosted bytes
> plus only an appended signature, and the steps below prepare and publish the
> stable `desktop-v<version>` release. Nothing in the updater checks changes.

## From committed sources to a prepared installer

1. Commit completed source to each app's own repository `main` (Melange to
   `pjg93-222/Melange`, Mentat to `pjg93-222/Mentat`, Terrier to
   `pjg93-222/Geo`). The earlier instruction to run the combined source-only sync
   in `../SOURCE_SYNC.md` to `melange-platform` main is **superseded** by that
   correction, and `ops/sync_sources.py` now refuses `--apply` and `--push`.
   No step here creates binaries or deploys servers.
2. On a Windows x64 builder, follow `packaging/README.md` to freeze each native
   runtime and build the three NSIS installers with the GitHub source selected.
   The builder defaults to `DESKTOP_UPDATE_PROVIDER=github` and, when
   `DESKTOP_GITHUB_REPOSITORY` is unset, to the selected app's repository named in the
   introduction (`DESKTOP_APP_ID` = `melange`, `mentat` or `terrier`). Set the
   variable only to override that default. Supply the independently
   reviewed public descriptor through `DESKTOP_ACCESS_TRUST`; its
   `updatePublicKeysByApp[appId]` supplies initial update roots without login or
   server enrollment. `DESKTOP_UPDATE_PROVIDER=publisher` explicitly selects
   the existing custom publisher instead. Use a new stable three-part version
   and a strictly increasing positive release sequence for each released app
   (its own ledger row). Record the exact source commits used;
   Terrier's original source checkout is separate from Melange/Mentat.
3. Complete native application acceptance and the existing `tests`, `security`,
   `dependencies`, `redistribution`, and `dataAllowlist` gates. Sign each installer
   with the configured Windows Authenticode identity. Each reviewed gate receipt
   must bind its installer SHA-256, app/version/platform/architecture, gate evidence,
   and a `source` object equal to that app's entry in the release specification.
   Source provenance is an operator attestation in that exact-byte receipt;
   this tool does not reconstruct or independently prove installer source.
4. Use the existing protected publisher state. Configure its independently
   provisioned per-app Ed25519 update roots and exact Windows publisher subject
   using the existing administrator tools. Preserve this state and the
   `github-prepared.sqlite3` sequence ledger across builds. It is not an asset.
5. Prepare a **new** output directory, then reverify each app's release before upload:

```powershell
python ops/desktop/packaging/github_release.py --state $publisherState --spec $releaseSpec --output $newBundle
foreach ($app in (Get-ChildItem -LiteralPath $newBundle -Directory).Name) {  # the apps this bundle releases
  python ops/desktop/packaging/github_release.py --state $publisherState --verify-output (Join-Path $newBundle $app)
  if ($LASTEXITCODE -ne 0) { throw "$app release verification failed" }
}
```

One specification names one or more apps under `packages` (release owner,
2026-10-16: the first signed release is Melange only). The named apps are signed
together or not at all. Each takes the top-level `version` and `sequence` unless
its `packages` entry sets its own, and each passes every per-app gate:
Authenticode Valid by the exact subject, the receipt bound to its bytes and
source, and its own ledger advance. `sourceRefs` always names all three apps,
because every signed manifest binds them and the installed client requires all
three (`client/github-source.cjs` `githubPolicy`). For an app that is not released,
give the reviewed commit of its own repository's main. Each app's release is bound to its own
repository from the fixed map in `github_release.py` (`melange` to
`pjg93-222/Melange`, `mentat` to `pjg93-222/Mentat`, `terrier` to `pjg93-222/Geo`).
An optional `repositories` object may restate entries of that map (case is
ignored); any other repository, an unknown app key, or the retired single
`repository` field is refused. Replace every placeholder with the actual
completed build inputs. The all-zero commit below is deliberately invalid.

```json
{
  "version": "1.0.0",
  "sequence": 1,
  "platform": "win32",
  "arch": "x64",
  "repositories": {"melange": "pjg93-222/Melange", "mentat": "pjg93-222/Mentat", "terrier": "pjg93-222/Geo"},
  "sourceRefs": {
    "melange": {"repository": "pjg93-222/Melange", "ref": "main", "commit": "0000000000000000000000000000000000000000"},
    "mentat": {"repository": "pjg93-222/Mentat", "ref": "main", "commit": "0000000000000000000000000000000000000000"},
    "terrier": {"repository": "pjg93-222/Geo", "ref": "main", "commit": "0000000000000000000000000000000000000000"}
  },
  "packages": {
    "melange": {"package": "C:/build/melange.exe", "receipt": "C:/receipts/melange.json"},
    "mentat": {"package": "C:/build/mentat.exe", "receipt": "C:/receipts/mentat.json"},
    "terrier": {"package": "C:/build/terrier.exe", "receipt": "C:/receipts/terrier.json"}
  }
}
```

Preparation checks the actual staged Windows signatures and exact-byte gate
receipts before signing. It refuses partial three-app releases, source-receipt
mismatch, old versions/sequences, and reusing a sequence for different bytes.
The sequence ledger is checked per app across every repository it has recorded,
and each new row records that app's own repository. Repeating the identical
release at a new directory reproduces its envelopes. No network request or upload
occurs. The output holds one directory per app, each with exactly five files:

- `<app>-win32-x64.manifest.json`, `<app>-<version>-win32-x64.exe`, `<app>-trust.json`;
- that app's `release.json` (with its `appId` and `repository`) and `SHA256SUMS`.

Each signed manifest binds `purpose: melange-desktop-github-release`,
`distribution: public-installations`, that app's lowercase GitHub repository,
all three source references, app/version/sequence/platform/architecture and
package hashes. The installed client accepts it only when that repository equals
its own configured repository (`client/github-source.cjs` `githubPolicy`), so a
release uploaded to another app's repository cannot install. The signed logical
package path remains `/v1/packages/<app>/<sequence>/<installer-name>`; the GitHub
adapter maps it to the exact asset in `desktop-v<version>`. Trust files contain
only signed key transitions rooted in keys the installed app already trusts. A
failed download cannot replace those roots. Recovery looks up the exact installed
version tag, so keep older accepted release assets available.

## Publish the verified releases

These are explicit operator steps after real native acceptance. They require
an authenticated `gh` with release-write permission on each already public
destination. The installer never receives that credential. Use one publisher
and its preserved sequence ledger; review `gh release list --repo $repo` for
every app repository before choosing the next version. Never overwrite/delete
an accepted tag or asset.

Set `$newBundle`, `$publisherState` and `$notesFile` to the reviewed output,
private state and release notes file. Set `$sourceCommits` to a map from app to
the exact pushed commit, in that app's own repository, whose checks passed (for
example `@{melange='...'; mentat='...'; terrier='...'}`). The loop handles each
app in turn and stops on every native command error:

```powershell
$ErrorActionPreference = 'Stop'
foreach ($app in (Get-ChildItem -LiteralPath $newBundle -Directory).Name) {  # the apps this bundle releases
  $bundle = (Resolve-Path -LiteralPath (Join-Path $newBundle $app)).Path
  $index = Get-Content -LiteralPath (Join-Path $bundle 'release.json') -Raw | ConvertFrom-Json
  if ($index.appId -ne $app) { throw "$bundle is not the $app release" }
  $repo = $index.repository
  $tag = $index.tag
  $commit = $sourceCommits[$app]
  python ops/desktop/packaging/github_release.py --state $publisherState --verify-output $bundle --repository $repo
  if ($LASTEXITCODE -ne 0) { throw "$app release verification failed" }
  $visibility = gh repo view $repo --json visibility --jq '.visibility'
  if ($LASTEXITCODE -ne 0 -or $visibility -ne 'PUBLIC') { throw "$repo must already be public" }
  $remoteCommit = gh api "repos/$repo/commits/$commit" --jq '.sha'
  if ($LASTEXITCODE -ne 0 -or $remoteCommit -ne $commit) { throw "Reviewed $app commit is not on $repo" }
  gh release create $tag --repo $repo --target $commit --draft --title "Desktop $($index.version)" --notes-file $notesFile
  if ($LASTEXITCODE -ne 0) { throw "$app draft creation failed; inspect existing tag/release before retrying" }
  $paths = @(@($index.assets) + @('release.json', 'SHA256SUMS') | ForEach-Object { Join-Path $bundle $_ })
  gh release upload $tag @paths --repo $repo
  if ($LASTEXITCODE -ne 0) { throw "$app upload incomplete; leave draft for review, never clobber assets" }
  $downloaded = Join-Path ([IO.Path]::GetTempPath()) ("melange-release-$app-" + [guid]::NewGuid().ToString('N'))
  gh release download $tag --repo $repo --dir $downloaded
  if ($LASTEXITCODE -ne 0) { throw "$app release could not be downloaded for verification" }
  python ops/desktop/packaging/github_release.py --state $publisherState --verify-output $downloaded --repository $repo
  if ($LASTEXITCODE -ne 0) { throw "$app uploaded bytes failed verification; keep the release draft" }
  gh release view $tag --repo $repo --json tagName,isDraft,assets,url
  if ($LASTEXITCODE -ne 0) { throw "$app release review failed" }
}
```

After reviewing each draft and its exact five assets, make it discoverable in
that app's repository (set `$repo` and `$tag` from that app's `release.json`):

```powershell
gh release edit $tag --repo $repo --draft=false --latest
if ($LASTEXITCODE -ne 0) { throw 'Publication failed; inspect release state before retrying' }
```

The CLI argument behavior is documented by GitHub for
[create](https://cli.github.com/manual/gh_release_create),
[upload](https://cli.github.com/manual/gh_release_upload), and
[edit](https://cli.github.com/manual/gh_release_edit). The local
`--verify-output` mode rejects extra/private files (including another app's
assets), renamed or changed assets, signature/identity mismatch, a release
directory checked against a repository other than its app's own
(`--repository`), stale trust transition files, and altered checksum lists. A
failed upload stays a draft; do not use `--clobber` to hide a mismatch.

## First install and remaining prerequisites

An existing app needs the GitHub receiver and independently installed trusted
update roots before it can discover this lane. Distribute the first signed
installer through a reviewed channel, verify its OS publisher, and include its
exact version's signed release as the recovery seed. New manifest keys require
an authenticated transition from an already trusted root. Downloaded GitHub
metadata or a new access grant cannot bootstrap arbitrary update keys.

This release producer intentionally accepts Windows x64 only. Windows signer
credentials, full native runtimes, redistribution approval, and real native
acceptance receipts remain external prerequisites. Mac builds additionally need
Apple signing/notarization, native runtime/Keychain acceptance, and a completed
activation path; Linux desktop activation is not implemented. Do not enable the
inert `native-build.yml.template` or claim cross-OS deployment from source tests.
The source-check workflow is ready to run without those signing secrets. A
source push alone will not produce or install a binary until this native lane
has been completed and a new verified desktop release is published.
