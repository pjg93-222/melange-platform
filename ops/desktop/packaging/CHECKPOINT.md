# Desktop implementation checkpoint — 2026-10-14

**Superseded after reboot:** read `NATIVE_CHECKPOINT.md` first. The text below preserves the earlier diagnostic state only. Private CPython runtimes now replace frozen binaries, and current user scope is local Windows installation.

Only additive files under `ops/desktop/client` and `ops/desktop/packaging` were changed. Existing application code and the parent's publisher server were not edited. Nothing was published or installed on another computer.

## Verified

- Seven Node tests: LAN pairing scope, signature/app/architecture checks, version/sequence rollback, unsupported schema refusal, rotation, path confinement, launch-header isolation and verified loopback feed.
- Three Python boundary/audit tests: authentication before all application routes; WSGI boundary; private write audit and tamper refusal.
- Real Node-to-Python HTTPS enrollment, pin rejection before token transmission, cross-app credential refusal, key rotation, signed manifest, fixture hash verification and status. The integration fixture is deliberately non-installable; production release gates are not bypassed.
- Real Mentat source server started using empty independent temporary data, accepted its launch token and returned 403 without it. Exact owned process tree cleanup completed. This is not full feature acceptance.
- Real Electron 44.7.2 main/preload/setup window launched and exited successfully. Sandbox/context isolation are enabled; Node is absent in the renderer. This is not a frozen-backend or installer acceptance result.
- Native Windows Credential Manager helper completed all-three-app ephemeral read/write/update/delete and negative tests. Test credentials were removed. macOS Keychain remains untested on a signed Mac helper.
- Production npm dependencies had zero reported vulnerabilities before the final direct semver dependency declaration; the full development tree reports eight moderate findings and requires resolution/review before a release.

## Build state and restart

The first Terrier PyInstaller scan in a venv exposing global site-packages was stopped before output. It must not be resumed. No native runtime or installer has been produced. Its log is `.build/terrier-build.log`.

A new **isolated** Windows Terrier environment is fully installed at `.build/terrier-venv/Scripts/python.exe` with Terrier requirements and PyInstaller 6.22.3. Electron/native npm dependencies are installed under the client. No build is running at this checkpoint. After restart, run the current source's ordinary tests, then start one bounded build:

```powershell
cd 'C:\Users\pgoff\OneDrive\Desktop\Melange Systems\ops\desktop\packaging'
& '.\.build\terrier-venv\Scripts\python.exe' build_runtime.py --app terrier --source 'C:\Users\pgoff\Documents\Terrier\outputs\cu-geography-v3'
```

Do not start parallel native builds while host memory is scarce. Inspect the generated inventory and dependency warnings. Then run `smoke_backend.py` against the produced binary and approved test data, followed by the client `build:dir` and NSIS build. Neither a green transport test nor `runtime-status.json` establishes feature or distribution readiness.

## Remaining release blockers

- Build and exercise all three frozen backends, full workflows and subprocesses. Python subprocess commands that assume `sys.executable` is an ordinary interpreter need a reviewed frozen-runtime dispatch seam; no compatibility bypass was added.
- Mentat data-path relocation is an adapter, not comprehensive evidence. Complete collection/index/report acceptance and review source-level portable path seams. Bundle reviewed R and packages, Node/Chromium where used, and Melange LibreOffice; test native numeric parity.
- Source staging is a conservative code inventory, not a secret/data scan. Run the existing release scanner and an explicit redistribution/data allowlist. Never package live databases, reports, keys or accounts. Pocket source export remains refused until scrubbed.
- Windows code signing and Mac Developer ID/notarization/Keychain entitlements are not provisioned here. Mac native builds require Mac runners. CI remains an inert template and needs pinned actions before activation.
- Prove signed native update activation, safe interruption, backups/migration and recovery of a previously authorized package. Only schema version 1 is accepted today. Automatic reconnect-specific checks are not implemented; startup/15-minute/resume/unlock checks are implemented.
- No passing publisher receipt was generated because native acceptance, full dependencies, redistribution/data and OS-signature gates are not complete. Publishing must stay blocked.
