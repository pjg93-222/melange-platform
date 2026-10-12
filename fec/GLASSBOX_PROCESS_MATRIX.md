# Glassbox process matrix

`open_glassbox_process_matrix.ps1` is a workspace-owned, read-only viewer for
the shared Glassbox event stream. It does not alter the Glassbox runtime,
events, collectors, browser sessions, or process state.

## Layout

- The fixed overhead pane shows the latest overall progress bar and one row
  per lane/source/run combination.
- The live-feed pane shows sanitized summaries, commands, inputs, outputs,
  artifacts, failures, and metadata.
- The window is movable, resizable, minimizable, and maximizable. The
  horizontal splitter can also be moved.
- The feed can be paused, cleared, filtered by source, selected, and copied.
- Filesystem reading, JSON parsing, hashing, and redaction run on a background
  thread. The UI only drains a bounded queue, so event bursts do not freeze the
  window. Queue drops, if any, are displayed in the overhead status line.

## Launch

From the Melange workspace:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\fec\open_glassbox_process_matrix.ps1
```

Attach one or more generic progress receipts and artifact paths from an
existing PowerShell prompt:

```powershell
& .\fec\open_glassbox_process_matrix.ps1 `
  -ProgressJson .\audits\run-a\progress.json,.\audits\run-b\progress.json `
  -ArtifactPath .\audits\run-a\outputs,.\audits\run-b\failures `
  -WindowTitle "GLASS BOX - MEDIA + LEGISLATION"
```

The event stream is source-agnostic. `lane`, `source`, and `run_id` can name
Codex, Gemini, Grok, Fable, video, tweets, press, or another producer.

## Producer contract

Emit one immutable JSON file per event into:

`C:\Users\pgoff\.claude\glassbox\events`

Use the existing Glassbox emitter whenever possible. The viewer understands
the standard fields below and tolerates additional metadata:

```json
{
  "schema_version": 1,
  "timestamp_utc": "2026-07-26T23:30:00Z",
  "run_id": "press-harvest-20260726",
  "lane": "gemini",
  "source": "press-collector",
  "kind": "record.accepted",
  "status": "running",
  "stage": "harvest",
  "summary": "37/120 (30%)",
  "detail": "Accepted article metadata",
  "command": "collector --source press",
  "input": {"query": "example"},
  "output": {"records": 4},
  "artifacts": ["audits/press/page-0037.json"],
  "metadata": {"active_workers": 6}
}
```

For an overall progress bar, include either a fraction such as `37/120` in
`summary`, or numeric progress fields. Recognized current fields include
`completed_queries`, `completed`, `current`, `done`, `processed`, `accepted`,
and `finished`. Recognized total fields include `total_queries`, `total`,
`required`, `target`, and `expected`.

A `-ProgressJson` file may use the same fields and can be atomically replaced
in place. The viewer detects content changes and does not modify the file.

An `-ArtifactPath` may name a file or directory. The adapter reports path,
timestamp, and size only; it deliberately does not open artifact contents.
Detailed records should be emitted as sanitized Glassbox event data.

## Privacy and failure behavior

- Producers must never write passwords, access tokens, API keys, cookie
  values, authorization headers, private keys, or other credentials into
  Glassbox events.
- As defense in depth, the viewer redacts common sensitive keys, command-line
  flags, assignments, bearer values, cookies, and credential-bearing URL
  parameters before display.
- Artifact names that appear credential-related are replaced with a redacted
  marker, and artifact contents are never opened by the generic adapter.
- Malformed or partially written JSON is ignored until a later file-change
  notification or poll provides a valid document.
- A failed producer remains visible as a failed matrix row; the viewer never
  retries, restarts, kills, or otherwise controls the observed process.

## Validation

Compile the embedded background engine without opening a window:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\fec\open_glassbox_process_matrix.ps1 -ValidateOnly
```
