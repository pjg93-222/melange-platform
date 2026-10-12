# MENTAT desktop app

Double-click **MENTAT** on the Desktop or find **MENTAT** in the Start menu.
It opens Loading in a dedicated app window. The local server starts automatically;
subsequent launches reuse it. No terminal needs to stay open.
If the MENTAT window is minimized or behind another window, clicking the shortcut
restores it and brings it forward.

Closing the app window leaves collection running. Reopen MENTAT to check progress.
Restarting Windows ends the server; opening MENTAT starts it again. Existing
collection receipts remain available. The desktop app does not automatically
restart interrupted collection jobs.

Graph retains the saved reports, shared scope, chart builder, and PNG/SVG/CSV
exports. Its browser drafts are stored in the dedicated MENTAT browser profile at
`%LOCALAPPDATA%\Mentat\browser`. Drafts entered in a regular browser profile do
not automatically move into this app profile. Reports and collection runs share
the existing `%LOCALAPPDATA%\Mentat\console` data directory and workspace caches.

## Install or recreate shortcuts

Keep the application folder in its permanent location, then double-click
**Install MENTAT.cmd**. Installation builds a Windows launcher and multi-size
icon and adds Desktop and Start menu shortcuts. No administrator access is needed.
The shortcut points to this folder; rerun the installer if you move it.

The app uses Microsoft Edge (or Chrome) for its window. Python 3.11+ and `openpyxl`
are required; the launcher first checks the installed Codex Python runtime, then
`python` on PATH. Collection additionally uses the existing R dependencies,
Node/report tooling, source inputs, and your locally stored FEC keys. The email
package includes application code and an installer, but does not bundle these
runtimes, source datasets, reports, or credentials. A fresh computer needs those
dependencies configured before it can collect and generate reports.

Startup checks the service identity and waits for readiness before opening the
window. It skips occupied ports, starting at 8802, and serializes simultaneous
launches. Startup failures appear in a dialog; per-port server logs live in
`%LOCALAPPDATA%\Mentat\console`.

For a terminal-only check:

```powershell
& .\app\Start-MentatApp.ps1 -NoBrowser
```

The icon uses MENTAT's plum, ivory, and gold palette. It is an original M mark,
not the America's Credit Unions organization logo. All app assets are local.
