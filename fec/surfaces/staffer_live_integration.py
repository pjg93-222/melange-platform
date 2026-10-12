"""Retired navigation hook for the dashboard builders.  Kept importable; now a no-op.

WHAT IT USED TO DO (2026-09-04 and earlier).  ``inject_navigation`` appended, just before
``</body>`` of every page it was handed, a marker plus an inline script that did this::

    if(location.protocol==='file:' || ['localhost','127.0.0.1','::1'].includes(location.hostname)){
      var stafferLiveScript=document.createElement('script');
      stafferLiveScript.src='http://127.0.0.1:8841/staffer-live/launch.js';
      stafferLiveScript.async=true;document.head.appendChild(stafferLiveScript);
    }

That is an EXTERNAL SCRIPT LOAD.  Two builders called it -- fec/surfaces/make_dashboard_html.py
(on the dashboard template) and fec/surfaces/make_combined.py (on the assembled
Moneyball.html) -- so both shipped pages fetched code from a separate live server on port
8841 at load time.  Moneyball.html's contract is the opposite of that: ONE self-contained
file, no CDN, no fonts, no script-src, nothing off-document (see the make_combined.py module
docstring).  Whenever the 8841 service was not up, the page requested
``staffer-live/launch.js`` and took ERR_CONNECTION_REFUSED -- observed on 2026-09-04.

WHY IT IS A NO-OP NOW.  The staffer network is a first-class surface of the dashboard as of
2026-09-04: fec/surfaces/make_staffer_network_html.py builds staffer-network.html, and
make_combined.py merges it as the fourth panel (#app-staffer) with in-page navigation and
history exactly like the member desk and the industry surface.  Reaching the staffer data
therefore no longer needs -- and must not use -- an injected off-document script.  The
optional LOCAL live view is still reachable: the staffer surface itself feature-detects the
8841 service with one ``fetch(..., {mode:'no-cors'})`` probe and reveals a plain link only
if it answers.  A probe that fails is swallowed, and no code is ever loaded from it.

The function stays importable and keeps its signature so the two builders and
fec/surfaces/install_staffer_live.py keep working unchanged; it now returns the html it was
given, byte for byte.  MARKER and HOOK are retained as documentation of what previously
shipped, and so a page still carrying the old marker can be recognised.
"""
MARKER = '<!-- melange-staffer-live-navigation-v1 -->'
HOOK = MARKER + '''<script>
if(location.protocol==='file:' || ['localhost','127.0.0.1','::1'].includes(location.hostname)){
  var stafferLiveScript=document.createElement('script');
  stafferLiveScript.src='http://127.0.0.1:8841/staffer-live/launch.js';
  stafferLiveScript.async=true;document.head.appendChild(stafferLiveScript);
}
</script>'''


def inject_navigation(html):
    """Return ``html`` unchanged.

    Retired 2026-09-04: it injected an external ``<script src=http://127.0.0.1:8841/...>``
    into two single-file surfaces whose stated contract is that they load nothing off the
    document, and the staffer network now ships as an in-page panel of Moneyball.html.
    See this module's docstring for the full reasoning.
    """
    return html
