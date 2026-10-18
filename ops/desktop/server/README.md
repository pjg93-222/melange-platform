# Private desktop release publisher

This is an additive desktop distribution lane. It does not change the existing hosted `melange-platform-release-v1` manifest, Docker images, source application security, or Claude-owned changes.

The administrator explicitly publishes **one application and one platform/architecture package** to named enrolled recipients. There is no HTTP endpoint for publishing, executing commands, reading application data, or changing signing trust. Applications remain independent and work locally without the publisher.

## Requirements and current release gates

Python 3.12+ and the pinned `requirements.txt` are required on the administrator laptop. The native clients bundle their runtimes. Windows packages require valid Authenticode signatures matching a locally pinned publisher. Mac packages require the native signing/notarization build and an Ed25519-signed gate receipt from a locally pinned Mac build attestation key. Missing signing identities, native dependencies, reviewed distribution data, or successful release gates **block publishing**. There is no skip-gates flag.

The publisher tests use deliberately non-installable fixtures and an injected OS-verification test double to exercise transport/transactions. Those fixtures are not release artifacts and do not establish native application readiness.

## Local administration

Run `python desktop_admin.py --help`. Default private state is `%USERPROFILE%\Documents\MelangeDesktopAdmin`, outside the repository and AppData virtualization. `--state PATH` selects a different private state directory **before** the subcommand. Login registration pins this exact state path, so changing the launching application's environment cannot select different keys. Existing initialized state must be migrated with its keys and database intact; do not initialize a replacement identity.

Before installing the launcher, run `python desktop_admin.py init` and `python desktop_admin.py access-init` (with the same `--state PATH` before each subcommand for a custom state directory).
Desktop launcher: `powershell -NoProfile -ExecutionPolicy Bypass -File Install-AdminLauncher.ps1` adds a **Melange Admin** shortcut to the Desktop and Start menu (`-Uninstall` removes both; `-PythonExecutable` and `-StateDirectory` override the defaults).
Clicking it opens the loopback dashboard in an Edge (else Chrome) app window on a one-time 60-second link; clicking again while the dashboard runs opens a fresh link (at most four sessions), and the dashboard still shuts down after 8 hours.

- `init`: initialize three application signing identities, a TLS identity, and enrollment/audit databases. Existing identities are never overwritten. Windows keys are protected with current-user DPAPI and a restricted directory ACL. Do not copy this state to recipient machines.
- `trust-publisher --windows-subject "VERIFIED CERTIFICATE SUBJECT"`: pin the expected Windows signing certificate identity after independently checking it. `--mac-receipt-key BASE64_PUBLIC_KEY` pins the separately provisioned native Mac build attestation identity.
- `pair --app melange --url https://PRIVATE_LAN_IP:38443`: issue a 10-minute, one-use pairing bundle. Transfer it directly to the intended recipient. It includes the TLS SPKI pin, application release public keys, and one-use enrollment secret; do not put it in logs or issue trackers.
- `devices`: show enrolled application/device IDs, versions, and update status; no bearer credentials are returned.
- `publish --app melange --package PACKAGE.exe --receipt GATES.json --version 1.0.1 --sequence 1 --platform win32 --arch x64 --recipient DEVICE_ID`: publish only after all gates pass. Repeat `--recipient` for more devices. Mac update payloads use `.zip`; installers may additionally be distributed as signed `.dmg` files. Versions and per-target release sequences must increase.
- `withdraw --app melange --sequence 1`: stop further downloads of that release. Installed applications/data remain unchanged. Publish corrected code under a new version/sequence.
- `authorize-existing-release --app melange --sequence 1 --recipient DEVICE_ID`: explicitly approve an already published active release for a later enrolled recipient. Target platform and application must match, and revoked recipients or withdrawn releases are refused. This is a local administrator approval with an audit entry; there is no HTTP equivalent. Approving the recipient's current version supplies its first recovery installer, without authorizing installation of an older version.
- `revoke --app melange --device DEVICE_ID`: immediately refuse further update requests from that credential. It does not remotely erase data or disable already-installed software.
- `rotate-key --app melange`: create a new protected signing key and a transition signed by the prior trusted key. The client verifies transitions before trusting new update signatures. Old verification keys and prior packages are retained for recovery. A compromised signing key requires an out-of-band trust recovery, not an unauthenticated network reset.

### Apps section of the dashboard

The first page of `access-dashboard` lists the local Docker services (Melange desk, Mentat console, Terrier) and can start, restart, update and back them up, show redacted logs, and create Terrier invitations. It runs on the host: `apps_admin.py` calls the `docker` CLI with argv built only from a fixed allowlist of templates (no shell, a timeout per command, a minimal environment). No Docker socket is mounted into any container and no image is added (D-239). Terrier is one service (D-241).

- The compose project (`melange-local`), the compose file (`ops/compose.local.yaml`) and the display names are one block at the top of `apps_admin.py`. `MELANGE_ADMIN_COMPOSE_PROJECT` and `MELANGE_ADMIN_COMPOSE_FILE` (absolute path) override the first two. Compose reads the image digests from the `.env` beside that file, so every required variable there must be set. The dashboard runs compose with a minimal environment (`ENV_KEEP` in `apps_admin.py`: the variables Windows needs plus `DOCKER_HOST`, `DOCKER_CONTEXT`, `DOCKER_CONFIG`, `DOCKER_CERT_PATH` and `DOCKER_TLS_VERIFY`), so variables exported in the shell that started the dashboard never reach compose: `ops/.env` is the only source of the image digests and every other compose variable. If either `MELANGE_ADMIN_COMPOSE_*` setting is invalid the dashboard still starts and approvals work; the Apps page says the settings need correcting.
- How the list is read, and why it stays quick. The services come from `docker ps -a -q --no-trunc` with the one filter `label=com.docker.compose.project=<project>` (the project name above, which is the compose file's `name:`), plus one `docker inspect` of those ids: the service is the container's `com.docker.compose.service` label, its state, health, image and start time come from the inspect document, and the port the Open link uses is the relay's published `NetworkSettings.Ports` entry on a loopback or wildcard address. `compose ps` is not used (it took 77 s on a busy machine). The parsed `compose config` (the pinned images) is kept until the compose file, `ops/.env` or an `env_file` the config lists changes (path, modified time and size); a failed read is never kept. One read has 10 seconds in all (the page gives up at 12) and runs in its own thread, which a request waits for 10 seconds at most (a docker call that is slow to be killed cannot hold the page; the read finishes in the background and the next request finds its list): `ps` 6 s then `inspect` 4 s, with `compose config` running beside them (10 s), and no call starts with less than half a second left.
- The last good list is kept in the dashboard process. While a read is already running, or when Docker times out or fails, `/apps` and `/apps/detail` answer at once with that list and `"stale": true` (`checkedAt` is when it was read, `staleReason` says why; busy, last and the activity log are always current). The page then shows it with "Showing the list from HH:MM. Docker is slow to answer; trying again." and asks again every 10 seconds, one request at a time, until an answer is fresh. "Could not read the services" appears only when no list has ever been read. A request that arrives while the first read is running waits for it (10 s at most).
- "Update ready" means the image the compose file now pins differs from the image the container was created from (compared by digest when both references carry one). It is shown for a stopped service too, next to Stopped. Update is `compose pull <service>` then `compose up -d <service>` and always asks for confirmation. Start is `compose start <service>`: it never pulls or recreates, so it cannot move a service to a new release.
- Every action except the invitation runs in the background, one per service at a time, and writes one `access_audit` row (`apps|admin|<service>|<action>|succeeded|failed`). Successful ones also appear under Successful changes. If the dashboard is closed while an action is running, that action gets an `interrupted` row. A docker command that exceeds its timeout is killed together with the helper processes it started.
- The Terrier invitation runs in the dashboard's request thread, not in the background: the request waits for `manage.py invite` (30 s at most) and returns the link in its own response; the link is never stored, and the audit row holds only the service, action and outcome.
- The Last backup shown for Terrier is read with a direct query for the newest succeeded `backup` row of `access_audit`, so it stays correct however many other actions happen afterwards. Backups accumulate in `/state/backups` of the data volume; nothing prunes them.
- Logs are redacted on the server (bearer, basic, token and digest credentials, key/value pairs whose name is or ends in `password`, `secret`, `token`, `key`, `cookie`, `session`, `invite`, `signature` and similar, every pair of a `Cookie:` or `Set-Cookie:` header, passwords inside URLs, private key blocks, JWTs, the PII patterns of `ops/security_gate.py`, the canary tokens) before they leave it. The server reads one service's logs at a time; a second request for the same service answers 409 at once.
- Backups exist for Terrier only (its `backup` management command, into `/state/backups` of its data volume). Listing Terrier accounts is not available: Terrier has no command for it.

## LAN listener

`serve` binds loopback by default for local verification. For real LAN use, review `Start-LanPublisher.ps1`. It requires the specified adapter to already be classified **Private**, refuses wildcard/public addresses, and can install an inbound rule limited to its Python program, one local address/port, Private profile, and LocalSubnet. Firewall installation requires Windows administrator rights. The script never reclassifies a Public network or broadly opens Windows Firewall.

Example on a trusted private LAN:

```powershell
.\Start-LanPublisher.ps1 -ListenIPAddress 192.168.1.25 -InstallFirewallRule
```

The publisher certificate is private and authenticated through the enrollment SPKI pin. Do not disable TLS verification globally or install a broad root certificate. The client must check the exact enrolled pin on every connection. A changed DHCP address needs the recipient's saved publisher address updated through a trusted pairing/configuration step; discovering an address does not create trust. No VPN, router port-forwarding, public domain, or public update storage is used.

## Explicit internet recipient origin

The default listener and pairing mode remain private. To prepare internet recipients, supply the same top-level `--public-origin https://updates.example.com` before `access-trust`, `pair`, and `serve`. This explicitly permits public client addresses. Pairing and trust descriptors must name that exact canonical HTTPS origin, and every access/update request must send its exact Host authority. Forwarded headers cannot select or replace it. The local approval dashboard remains loopback-only and is never an internet route.

For example, after configuring the actual endpoint, `python desktop_admin.py --state STATE --public-origin https://updates.example.com serve --listen 192.168.1.25 --port 38443` serves the recipient API using the existing TLS identity. This command does not provision DNS, connectivity, certificates, firewall rules, or startup. Use a TLS pass-through endpoint that preserves the pinned publisher key and Host; ordinary TLS termination substitutes a different key and will fail the client's pin check. Distribute the approved public-origin trust descriptor independently to each recipient. A changed origin needs newly distributed trusted configuration; redirects and discovery do not authorize changes. Signed packages, recipient approval and recovery installer gates still apply.

This is server capability and local test coverage, not evidence that an internet endpoint or native cross-OS deployment is installed.

## Client contract

Application IDs are `melange`, `terrier`, and `mentat`; targets are `win32/x64`, `darwin/x64`, and `darwin/arm64`.

- Pairing bundle: base64url canonical JSON containing `schemaVersion: 1`, `url`, `appId`, `code`, `tlsSpkiSha256`, and `publicKeys` keyed by signing key ID. This bundle, transferred by the administrator, establishes trust.
- `POST /v1/enroll`: JSON `enrollmentCode`, `appId`, UUID `deviceId`, `platform`, `arch`, `currentVersion`; returns only `deviceToken`, `deviceId`, `appId`. Credentials are application-scoped and stored by clients in their OS-protected secret store.
- `GET /v1/apps/{appId}/trust`: bearer-authenticated signed key transitions. Each transition must be verified by an already trusted key; its application, sequence, and public-key hash must match before it can extend trust.
- `GET /v1/apps/{appId}/updates?platform=win32&arch=x64&version=1.0.0`: a signed envelope or HTTP 204. Envelope fields are `schemaVersion`, `keyId`, base64 `payload`, and base64 Ed25519 `signature` over the exact payload bytes.
- `GET /v1/apps/{appId}/recovery?platform=win32&arch=x64&version=1.0.0`: exact-version signed recovery metadata, restricted to active releases approved for this enrolled recipient and target. The client accepts a seed only for its running version, validates native signing and hashes, and retains it before permitting the first upgrade. Missing initial recovery approval defers activation. This lookup grants download access, never arbitrary downgrade or installation authority.
- Signed payload binds `appId`, `version`, monotonic `sequence`, target platform/architecture, `issuedAt`, updater/data compatibility, package name/path/size/SHA-256/SHA-512. The path is relative, same-origin, and application-bound. Never accept package URLs from unsigned metadata.
- `GET /v1/apps/{appId}/feed/latest.yml` or `latest-mac.yml`: authenticated native updater metadata derived from that same authorized release. Clients must first verify the signed envelope and verify downloaded bytes against it. Differential downloads are disabled initially.
- `GET /v1/packages/{appId}/{sequence}/{filename}`: recipient- and target-restricted immutable package download. Redirects and cross-origin credentials are not supported.
- `POST /v1/status`: only application ID, version, a fixed status enum, and an optional bounded error code. No record contents, paths, free-form logs, or personal data are accepted as status fields.

Only literal private LAN/loopback peers are accepted by default; explicit public-origin mode permits internet peers while retaining the same authentication and exact configured Host checks. Browser-origin requests are refused. TLS handshakes and request concurrency are bounded; request bodies are limited to 16 KiB. Logs omit URLs, pairing codes and credentials. The local SQLite audit stores release/enrollment/status events, not application records.

## Gate receipt

The packaging pipeline must produce a receipt bound to the exact package SHA-256 with application/version/platform/architecture and successful `tests`, `security`, `dependencies`, `redistribution`, and `dataAllowlist` gates, each linking its actual evidence. `osSignature.status` must also be `passed`; a self-asserted receipt cannot bypass Windows Authenticode verification. On Mac, `macAttestation` signs canonical receipt bytes excluding that field using the separately trusted native-build attestation key.

Publication stages a copy, rehashes it, signs the final manifest, then activates recipient visibility transactionally. Writable application state is never included in publisher packages. Client migration, backup, activation and rollback acceptance tests are required before real distribution.

## Tests

```powershell
python -m pytest test_desktop_admin.py -q
```

Coverage includes one-use/expired/concurrent enrollment, per-application credentials, explicit recipient checks, native-signature gate refusal, signed payload tampering, version/sequence rollback refusal, wrong-target packages, protected keys, key transitions, withdrawal/revocation, private-address validation, real HTTPS enrollment/download/status, cross-app denial, and sanitized errors.
