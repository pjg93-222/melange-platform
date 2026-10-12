# Mentat on a remote pod

The existing Desktop and Start Menu MENTAT shortcuts keep their normal entry point. When `%LOCALAPPDATA%\Mentat\remote-console.json` exists, the launcher connects to that pod before doing any local backend setup. A missing, invalid, stale, or unreachable configured remote service is an error; it never falls back to local Python, report priming, collection, or model work. Without the profile, the existing local launcher behavior is unchanged.

The runtime profile has `SchemaVersion: 1`, `Host`, `SshPort`, `User`, `KeyPath`, `LocalPort`, `RemotePort`, `RemoteStartScript`, and `ExpectedProject`. The key is a local SSH key path, not key contents. The profile stays outside Git. Local and remote ports must match because the console enforces the request Host and Origin. SSH uses the existing known-host record with strict checking and never prompts for a password.

The launcher first runs the configured idempotent startup script through a hidden SSH control connection. It then creates or reuses its own loopback-only SSH tunnel. A port owned by another process is left untouched. Reuse requires the recorded process identity and start time, the expected executable, and listener ownership. Each launch checks the actual remote project, read-only mode, current code and asset hashes, and the published forecast catalog (v2 or a supported v1 rollback) before opening the app window. The app uses its existing browser profile. `-NoBrowser` makes only the transport connection, for an existing browser or the Codex panel.

All backend and forecast processes stay in the pod. The workstation runs only the display and SSH transport. The remote console uses an exact mirror of the user's authoritative Mentat Complete Files report folder; test fixtures and historical reports are not substitutes. Read-only mode keeps collection and report mutation disabled. Copy refreshed report files through the approved transport before using them remotely.

Connection receipts and SSH logs are saved beside the runtime profile. Removing the profile restores local launch behavior on the next launch; do not remove it while the requirement to keep workloads in the pod is active. An unreachable pod reports a connection error until it is available again.

Validation: the PowerShell parser, injected transport tests, and checks against the actual full pod server run in the pod. Windows PowerShell 5 syntax/API compatibility is reviewed separately; these tests do not claim that a native Windows backend was run.
