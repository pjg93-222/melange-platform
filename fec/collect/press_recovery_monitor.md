# Press recovery display

Operate surface for the independent September 20 press recovery worker. Inherits
Melange's paper, plum, serif headings, and restrained controls from DESIGN.md.
User asked for a live display of parsing with minimal ongoing model usage.

## Direction contract

THESIS: Show real requests and extracted text together, with observable worker health.

OWN-WORLD: Existing Melange palette and typography; flat rows, thin dividers, small controls.

STORY: Check the heartbeat, follow active source requests, select recovered text or a failure, and inspect the final receipt.

FIRST VIEWPORT: Status and progress above two columns. Active requests and event stream on the left; article results and readable text on the right. Display pause lives beside the receipt link.

FORM: Narrow operational extension, code-led. No new identity or concept seed required. Mobile stacks the two columns.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.

## Runtime

Run `python fec/collect/press_recovery_monitor.py` from the workspace. Binds only
127.0.0.1:8876. Reads checkpoint files incrementally; no AI calls or external
dependencies. Polls every 1.5 seconds; the worker records heartbeat every 2 seconds.
Only the display and specific read-only JSON endpoints are served. Source HTML is
never rendered. Closing or pausing the display does not pause the worker.

## Verification and design record

September 21: Chrome smoke test passed against the actual local server: heartbeat
and worker liveness, live parsing events, result selection, readable recovered
bodies, explicit failure details, filtering, pause/resume, restricted routes,
and no JavaScript errors. Desktop at 1440px and mobile at 390px were captured
and visually inspected. Mobile has no horizontal overflow.

Bounded in-thread review: functional display accepted; no whole-project design
approval implied. The existing DESIGN.md and design sidecar are preserved. The
detector's right-column padding warning is covered by 32px desktop inset and a
24px top inset on mobile; the left border is removed on mobile. Status colors
are labeled semantic states. Consolas is limited to raw log output. Local
system body fonts and Georgia use the incumbent documented fallback families.
No raster assets ship in this display. Screenshots are review evidence only.

Worker repairs: refresh bootstrap invalidates import caches; a dead worker lock
can be resumed after process liveness is checked; completed outcomes survive a
restart; the pending/failure overlap is computed once; www aliases are accepted;
the R replay writes a body checkpoint before recording completion. The worker
keeps per-request events plus a separate heartbeat and continues without the UI.

## Desktop app and Politico extension

`Melange Collection Monitor.lnk` on the user's Desktop launches
`launch_collection_monitor.pyw` with pythonw. The launcher checks the monitor
health endpoint, starts it when needed, and opens an isolated Chrome/Edge app
window. No console window or model calls are needed. Closing the window leaves
the independent worker and server running.

The user clarified that Politico means the news/article collector. The tab now
excludes the staff-directory receipt entirely and shows NOT CONNECTED with
unknown counts. The news collector process/output location has not been found.
A folder, script, or task name is required to bind its real stream.

The press completion banner distinguishes 382 retrieved bodies from the 120
included in the historical refresh. The other 262 have explicit publication
dates earlier than June 25. Both original and recovery receipts are retained.

The follow-up recovery pass adds tested Fireside #press, Senate Elementor post
content, House EVO, and Crapo article containers. All available saved HTML is
reparsed before network retries; prior passes are retained under passes/.
The display distinguishes current-pass progress from accumulated body outcomes.
