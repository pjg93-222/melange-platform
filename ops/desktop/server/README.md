# Private desktop release publisher

This is an additive desktop distribution lane. It does not change the existing hosted `melange-platform-release-v1` manifest, Docker images, source application security, or Claude-owned changes.

The administrator explicitly publishes **one application and one platform/architecture package** to named enrolled recipients. There is no HTTP endpoint for publishing, executing commands, reading application data, or changing signing trust. Applications remain independent and work locally without the publisher.

## Requirements and current release gates

Python 3.12+ and the pinned `requirements.txt` are required on the administrator laptop. The native clients bundle their runtimes. Windows packages require valid Authenticode signatures matching a locally pinned publisher. Mac packages require the native signing/notarization build and an Ed25519-signed gate receipt from a locally pinned Mac build attestation key. Missing signing identities, native dependencies, reviewed distribution data, or successful release gates **block publishing**. There is no skip-gates flag.

The publisher tests use deliberately non-installable fixtures and an injected OS-verification test double to exercise transport/transactions. Those fixtures are not release artifacts and do not establish native application readiness.

## Local administration

Run `python desktop_admin.py --help`. Default private state is `%USERPROFILE%\Documents\MelangeDesktopAdmin`, outside the repository and AppData virtualization. `--state PATH` selects a different private state directory **before** the subcommand. Login registration pins this exact state path, so changing the launching application's environment cannot select different keys. Existing initialized state must be migrated with its keys and database intact; do not initialize a replacement identity.

- `init`: initialize three application signing identities, a TLS identity, and enrollment/audit databases. Existing identities are never overwritten. Windows keys are protected with current-user DPAPI and a restricted directory ACL. Do not copy this state to recipient machines.
- `trust-publisher --windows-subject "VERIFIED CERTIFICATE SUBJECT"`: pin the expected Windows signing certificate identity after independently checking it. `--mac-receipt-key BASE64_PUBLIC_KEY` pins the separately provisioned native Mac build attestation identity.
- `pair --app melange --url https://PRIVATE_LAN_IP:38443`: issue a 10-minute, one-use pairing bundle. Transfer it directly to the intended recipient. It includes the TLS SPKI pin, application release public keys, and one-use enrollment secret; do not put it in logs or issue trackers.
- `devices`: show enrolled application/device IDs, versions, and update status; no bearer credentials are returned.
- `publish --app melange --package PACKAGE.exe --receipt GATES.json --version 1.0.1 --sequence 1 --platform win32 --arch x64 --recipient DEVICE_ID`: publish only after all gates pass. Repeat `--recipient` for more devices. Mac update payloads use `.zip`; installers may additionally be distributed as signed `.dmg` files. Versions and per-target release sequences must increase.
- `withdraw --app melange --sequence 1`: stop further downloads of that release. Installed applications/data remain unchanged. Publish corrected code under a new version/sequence.
- `authorize-existing-release --app melange --sequence 1 --recipient DEVICE_ID`: explicitly approve an already published active release for a later enrolled recipient. Target platform and application must match, and revoked recipients or withdrawn releases are refused. This is a local administrator approval with an audit entry; there is no HTTP equivalent. Approving the recipient's current version supplies its first recovery installer, without authorizing installation of an older version.
- `revoke --app melange --device DEVICE_ID`: immediately refuse further update requests from that credential. It does not remotely erase data or disable already-installed software.
- `rotate-key --app melange`: create a new protected signing key and a transition signed by the prior trusted key. The client verifies transitions before trusting new update signatures. Old verification keys and prior packages are retained for recovery. A compromised signing key requires an out-of-band trust recovery, not an unauthenticated network reset.

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
