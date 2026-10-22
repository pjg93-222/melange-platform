# Internet update endpoint setup

The source supports an explicitly configured HTTPS origin for recipients on other networks. No public endpoint, DNS record, cloud host, firewall rule, or signing identity is installed by this change.

## Supported initial topology

Keep the existing publisher on Windows under the account that owns its protected state. Put a TCP/TLS pass-through relay in front of that listener, with a stable public hostname such as `updates.example.com`. Recipients connect to that hostname; the relay must pass the TLS handshake and the original Host through unchanged. The publisher continues terminating TLS using the key pinned in the independently distributed trust descriptor. An HTTP reverse proxy that terminates TLS presents a different key and does not work with that descriptor.

Do not expose the application approval dashboard or Windows remote administration. The recipient listener serves only authenticated update/access APIs. Approval remains local. Existing package signatures, release gates, per-recipient authorization and recovery installer requirements stay active.

## Decisions and prerequisites

- An actual hostname under the administrator's control, a DNS account, and a selected host or managed TCP relay. Any paid account or subscription needs the owner's choice.
- Stable connectivity from the relay to the Windows publisher. The default private startup scripts do not configure this, and public mode does not change firewall/router rules.
- The exact existing publisher state directory and its owning Windows account. Preserve keys and databases together. DPAPI binds protected files to that account; copying the state to another user or Linux does not migrate it.
- A valid, trusted Windows code-signing identity and exact-hash release evidence before publishing Windows installers. Client activation currently supports Windows only. macOS also needs native build/signing/notarization acceptance and supported client activation before it can receive program updates.
- A documented backup and restore check for the publisher state and recipients' data, plus clean recipient acceptance of initial installation, first recovery seed, ordinary update, interrupted download and failed-start recovery.

## Configure after those choices

Use Python 3.12+ with `requirements.txt`. Reuse the existing initialized state. The examples are placeholders and must not be run unchanged.

```powershell
python desktop_admin.py --state 'C:\ExistingProtectedPublisherState' --public-origin https://updates.example.com access-trust --url https://updates.example.com
python desktop_admin.py --state 'C:\ExistingProtectedPublisherState' --public-origin https://updates.example.com serve --listen 192.168.1.25 --port 38443
```

Distribute the first command's descriptor through the administrator's trusted enrollment process. Never distribute a pairing secret or private key in source control. The second command runs the recipient listener in the foreground; use a separately reviewed Windows service/task setup under the same account for unattended operation. Existing LAN startup scripts do not silently opt into internet mode.

Invite codes are not enforced by default. After testers update to a client with the invite field, add `--require-invite` after `serve`, or set `MELANGE_ACCESS_REQUIRE_INVITE` in the publisher account's user environment for the startup task. Accepted values (trimmed, case-insensitive): `1`, `true`, `yes`, `on` enforce; unset, empty, `0`, `false`, `no`, `off` do not; anything else makes `serve` refuse to start. `Start-LanPublisherAtLogin.ps1` captures its environment at logon and relaunches Python from that copy, so after setting the User variable restart the login startup task/supervisor (or log off and on); killing only Python is not enough. Then confirm `inviteRequired` is `true` in `listener-status.json` in the state directory. See the README `enforce invites` entry.

### Bundled `bore.pub` relay

The packaged trust descriptor names `https://bore.pub:38443`. That path needs two processes under the publisher's account. Both must run after every login and reboot:

```powershell
.\Register-LanPublisherStartup.ps1 -Action Install -PublicOrigin https://bore.pub:38443 -PythonExecutable 'C:\Path\To\python.exe'
python relay_tunnel.py --port 38443 --log 'C:\ExistingProtectedPublisherState\relay.log'
```

The first command registers the loopback publisher on `127.0.0.1:38443`, the only local port that `relay_tunnel.py` forwards to. The second command is the relay client. It is not part of the publisher startup entry, so supervise it as a separate task under the same account. While it is down, recipients cannot reach the publisher. The tunnel runs up to 32 forwards at once; up to 32 further connections each wait up to 6 seconds, counted from when the relay announces them, for one to finish (inside the relay's 10-second accept window) rather than being dropped at once. A connection beyond those is skipped and logged.

`bore.pub` is a shared relay, so another client may hold port 38443 while the tunnel reconnects. Recipients then fail the TLS pin check and send nothing. Access resumes once the tunnel reclaims the port.

Configure the chosen relay and DNS so the public hostname reaches the listener without replacing TLS. Verify from another network: exact TLS public-key pin and certificate validity; approved recipient enrollment; correct Host accepted and any other Host rejected; missing/cross-app credentials rejected; signed recovery seed and update downloaded and installed; wrong pin, redirect and modified installer refused. A TLS connection or `/v1` response alone does not prove an update works.

## Linux limits

The Python listener can run on Linux with freshly initialized, protected state, but the current Windows-package publication gate calls Windows Authenticode verification and refuses non-Windows publication. Unix protection relies on filesystem permissions rather than DPAPI; any future Linux deployment must use a dedicated unprivileged account, private state directory, and `umask 077`. Moving the existing Windows state or splitting a publisher from a serving mirror requires an explicit migration/import design and is not supplied here. A Linux TCP relay does not need publisher signing keys or recipient databases.

## Release automation boundary

Committing source does not currently create or publish a signed native installer. `native-build.yml.template` is intentionally inactive. Container releases use a separate signed manifest and VPS updater; they do not update native desktop binaries. A native CI/build-and-publish step must be installed only after source export, OS signing and acceptance are established. Never connect a main-branch push directly to unverified installer publication.
