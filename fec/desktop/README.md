# Moneyball desktop app

The coordinated release uses Microsoft Edge in app-window mode, with a separate
Moneyball profile and Desktop and Start Menu launchers. The launcher starts the
verified local service when needed and then opens the full dashboard without
ordinary browser chrome.

Desktop and Start Menu shortcuts invoke `launch_moneyball_edge.py` with the
installed `pythonw.exe`. This avoids the PowerShell script-policy failure that
previously closed the hidden launcher before it opened a window. The service is
started directly with Python when absent; unrelated listeners are never stopped.
Startup failures show a message and are logged under
`%LOCALAPPDATA%\Melange\MoneyballApp\edge\launcher.log`.

`moneyball_app.py` is a Windows WebView2 shell with a separate persistent profile,
a single-instance window, and native Workspace menu. It reuses the validated local
Moneyball service. If the port is occupied by an unrelated service, it refuses to
replace it. Closing the window does not stop the data service or collectors.

Build dependencies are isolated under
`%LOCALAPPDATA%\Melange\MoneyballApp\build-env`. PyInstaller built the candidate in
`%LOCALAPPDATA%\Melange\MoneyballApp\staging\Moneyball`.

Windows Application Control blocked execution of the separately staged unsigned
WebView2 binary on 2026-09-08. It remains unreleased. The Edge app-window package
uses installed signed system applications and does not alter that policy.

The user approved the Higgsfield composition from generation
`39a57dd3-763b-4f5a-aee0-1c49d21be6fe`. Its large canvas, compact pathfinder and
person panel are reconciled to America's Credit Unions branding.

`install_moneyball_edge.ps1` requires a coordinated PASS release receipt before
creating launchers. It preserves a prior launcher as a rollback target.

The original shell checks still cover configuration validation, unsupported
paths and ports, and refusal to replace an unrelated listener. The coordinated
receipt records the Edge launcher and live dashboard checks.
