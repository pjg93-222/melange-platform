# Public GitHub desktop updates

The Electron apps can use public GitHub Releases in
`pjg93-222/melange-platform`. Binary update checks are independent of application
feature approval. The app still verifies an Ed25519 manifest against its
independently installed update roots, exact app/OS/architecture, increasing
sequence/version, installer hashes, and the native OS signer before activation.
Feature access keeps its separate signed approval checks. No GitHub PAT,
publisher private key, access credential, or recipient list belongs in an app
or release asset. Making the repository public is an owner action; none of the
tools here change repository visibility.

## From committed sources to a prepared installer

1. Commit completed source to local Melange `main`; Mentat source uses the
   configured `codex/mentat-crypto` worktree ref. Run the source-only sync in
   `../SOURCE_SYNC.md` to send the filtered committed tree to platform main.
   The exported `desktop-source-checks.yml` runs source tests on pushes. It
   neither creates binaries nor deploys servers.
2. On a Windows x64 builder, follow `packaging/README.md` to freeze each native
   runtime and build the three NSIS installers with the GitHub source selected.
   The builder defaults to `DESKTOP_UPDATE_PROVIDER=github` and
   `DESKTOP_GITHUB_REPOSITORY=pjg93-222/melange-platform`. Supply the independently
   reviewed public descriptor through `DESKTOP_ACCESS_TRUST`; its
   `updatePublicKeysByApp[appId]` supplies initial update roots without login or
   server enrollment. `DESKTOP_UPDATE_PROVIDER=publisher` explicitly selects
   the existing custom publisher instead. Use one new stable three-part version
   for all three apps and a strictly
   increasing positive release sequence. Record the exact source commits used;
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
5. Prepare a **new** output directory, then reverify it before upload:

```powershell
python ops/desktop/packaging/github_release.py --state $publisherState --spec $releaseSpec --output $newBundle
python ops/desktop/packaging/github_release.py --state $publisherState --verify-output $newBundle
```

The JSON specification has this structure; replace every placeholder with the
actual completed build inputs. The all-zero commit below is deliberately invalid.

```json
{
  "repository": "pjg93-222/melange-platform",
  "version": "1.0.0",
  "sequence": 1,
  "platform": "win32",
  "arch": "x64",
  "sourceRefs": {
    "melange": {"repository": "pjg93-222/Melange", "ref": "main", "commit": "0000000000000000000000000000000000000000"},
    "mentat": {"repository": "pjg93-222/Melange", "ref": "codex/mentat-crypto", "commit": "0000000000000000000000000000000000000000"},
    "terrier": {"repository": "pjg93-222/Geo", "ref": "claude/walk-list", "commit": "0000000000000000000000000000000000000000"}
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
Repeating the identical release at a new directory reproduces its envelopes.
No network request or upload occurs. The output contains exactly eleven files:

- For each of `melange`, `mentat`, `terrier`: `<app>-win32-x64.manifest.json`,
  `<app>-<version>-win32-x64.exe`, and `<app>-trust.json`.
- `release.json` and `SHA256SUMS`.

Each signed manifest binds `purpose: melange-desktop-github-release`,
`distribution: public-installations`, the lowercase GitHub repository, all
three source references, app/version/sequence/platform/architecture and package
hashes. The signed logical package path remains
`/v1/packages/<app>/<sequence>/<installer-name>`; the GitHub adapter maps it to
the exact asset in `desktop-v<version>`. Trust files contain only signed key
transitions rooted in keys the installed app already trusts. A failed download
cannot replace those roots. Recovery looks up the exact installed version tag,
so keep older accepted release assets available.

## Publish the verified bundle

These are explicit operator steps after real native acceptance. They require
an authenticated `gh` with release-write permission on the already public
destination. The installer never receives that credential. Use one publisher
and its preserved sequence ledger; review `gh release list --repo $repo` before
choosing the next version. Never overwrite/delete an accepted tag or asset.

Set `$newBundle`, `$publisherState`, `$notesFile` and `$platformCommit` to the
reviewed bundle, private state, release notes file, and the exact pushed platform
source commit whose checks passed. The following commands stop on every native
command error:

```powershell
$ErrorActionPreference = 'Stop'
$bundle = (Resolve-Path -LiteralPath $newBundle).Path
$index = Get-Content -LiteralPath (Join-Path $bundle 'release.json') -Raw | ConvertFrom-Json
$repo = $index.repository
$tag = $index.tag
python ops/desktop/packaging/github_release.py --state $publisherState --verify-output $bundle
if ($LASTEXITCODE -ne 0) { throw 'Bundle verification failed' }
$visibility = gh repo view $repo --json visibility --jq '.visibility'
if ($LASTEXITCODE -ne 0 -or $visibility -ne 'PUBLIC') { throw 'Destination must already be public' }
$remoteCommit = gh api "repos/$repo/commits/$platformCommit" --jq '.sha'
if ($LASTEXITCODE -ne 0 -or $remoteCommit -ne $platformCommit) { throw 'Reviewed platform commit is not on the destination' }
gh release create $tag --repo $repo --target $platformCommit --draft --title "Desktop $($index.version)" --notes-file $notesFile
if ($LASTEXITCODE -ne 0) { throw 'Draft creation failed; inspect existing tag/release before retrying' }
$assets = @($index.assets) + @('release.json', 'SHA256SUMS')
$paths = @($assets | ForEach-Object { Join-Path $bundle $_ })
gh release upload $tag @paths --repo $repo
if ($LASTEXITCODE -ne 0) { throw 'Upload incomplete; leave draft for review, never clobber assets' }
$downloaded = Join-Path ([IO.Path]::GetTempPath()) ('melange-release-' + [guid]::NewGuid().ToString('N'))
gh release download $tag --repo $repo --dir $downloaded
if ($LASTEXITCODE -ne 0) { throw 'Uploaded bundle could not be downloaded for verification' }
python ops/desktop/packaging/github_release.py --state $publisherState --verify-output $downloaded
if ($LASTEXITCODE -ne 0) { throw 'Uploaded bytes failed verification; keep the release draft' }
gh release view $tag --repo $repo --json tagName,isDraft,assets,url
if ($LASTEXITCODE -ne 0) { throw 'Release review failed' }
```

After reviewing that draft and its exact eleven assets, make it discoverable:

```powershell
gh release edit $tag --repo $repo --draft=false --latest
if ($LASTEXITCODE -ne 0) { throw 'Publication failed; inspect release state before retrying' }
```

The CLI argument behavior is documented by GitHub for
[create](https://cli.github.com/manual/gh_release_create),
[upload](https://cli.github.com/manual/gh_release_upload), and
[edit](https://cli.github.com/manual/gh_release_edit). The local
`--verify-output` mode rejects extra/private files, renamed or changed assets,
signature/identity mismatch, stale trust transition files, and altered checksum
lists. A failed upload stays a draft; do not use `--clobber` to hide a mismatch.

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
