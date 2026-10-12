"""Serve the audited dashboard pages with local, live navigation.

Published standalone artifacts remain byte-for-byte intact. The local response
adds a shell that embeds the current people application on the same origin.
"""
from functools import lru_cache
import gzip
import hashlib
import re
from urllib.parse import parse_qs, urlencode, unquote

PAGES = frozenset(('Moneyball.html', 'lobby-bills.html', 'lobby-interactions.html',
                  'lobby-champions.html', 'lobby-forecast.html', 'lobby-screen.html'))
ASSETS = frozenset(('cu-member-desk-profiles.js',))
ALIASES = {'Moneyball Dashboard.html': None, 'cu-member-desk.html': 'desk',
           'lobby-network.html': 'industry', 'staffer-network.html': 'staffer'}
MARKER = b'<!-- melange-local-dashboard-v1 -->'


def resolve(path, query=''):
    """An explicit allowlist; never resolve arbitrary workspace paths."""
    path = unquote(path)
    params = parse_qs(query, keep_blank_values=True)
    # Older member-to-person links carried both m and v. The member app reads m
    # during startup, before the combined router can select People.
    if path in ('/', '/melange', '/melange/', '/Moneyball.html') and params.get('v', [''])[0] in ('staffer', 'desk', 'industry') and 'm' in params:
        params.pop('m')
        return {'redirect': '/?' + urlencode(params, doseq=True)}
    if path in ('/', '/melange', '/melange/'):
        return {'file': 'Moneyball.html', 'html': True}
    name = path.removeprefix('/')
    if name in ALIASES:
        if ALIASES[name]:
            params['v'] = [ALIASES[name]]
            params.pop('m', None)
        else:
            params.pop('v', None)
        return {'redirect': '/' + ('?' + urlencode(params, doseq=True) if params else '')}
    if name in PAGES or name in ASSETS:
        return {'file': name, 'html': name in PAGES}
    return None


def integrate(raw, profiles=None):
    if b'<!-- melange-clean-workspace-v2 -->' in raw:
        return raw
    if MARKER in raw:
        return raw
    # Preserve legacy evidence consumers without copying or mutating records.
    bridge = b'''window.__melangeMemberEvidence=function(name){
      if(!window.__profilesReady)return null;
      return M.find(function(m){return m.name===name;})||null;
    };
    '''
    anchor = b'window.__deskOpen=function(n)'
    if anchor in raw and b'window.__melangeMemberEvidence=' not in raw:
        raw = raw.replace(anchor, bridge + anchor, 1)
    # Use the same organic coordinates; fit their finite bounds safely when the
    # network pane is visible. Hidden zero-width panes must not invert the camera.
    fitted = b'''function fit(){
      if(!Number.isFinite(W)||!Number.isFinite(H)||W<=48||H<=72)return;
      const xs=LX.filter(Number.isFinite),ys=LY.filter(Number.isFinite);
      if(!xs.length||!ys.length)return;
      const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
      const s=Math.min((W-48)/Math.max(1,x1-x0),(H-72)/Math.max(1,y1-y0));
      cam.s=s;cam.tx=W/2-(x0+x1)*s/2;cam.ty=H/2-(y0+y1)*s/2;
    }
    window.__melangeFitInfluence=fit;'''
    raw = re.sub(rb'function fit\(\)\{[^\n]*\}', lambda _: fitted, raw, count=1)
    # The offline bundle embeds 61 MB of member profiles. Serve the identical
    # sibling script separately locally, reducing DOM size without dropping data.
    if profiles is not None:
        raw = raw.replace(b'<script>\n' + profiles + b'\n</script>',
                          b'<script src="/cu-member-desk-profiles.js"></script>', 1)
    head = MARKER + b'<link rel="stylesheet" href="/staffer-live/melange-dashboard.css">'
    script = b'<script src="/staffer-live/melange-dashboard.js"></script>'
    if b'</head>' not in raw or b'</body>' not in raw:
        raise ValueError('Dashboard page is missing its document boundaries')
    raw = raw.replace(b'</head>', head + b'</head>', 1)
    # Apply the local presentation before the first paint, not after the large
    # embedded datasets execute. Offline artifacts retain their own shell.
    def local_body(match):
        tag = match.group(0)
        classes = re.search(rb'\bclass=(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))', tag)
        if classes:
            value = next(group for group in classes.groups() if group is not None)
            return tag[:classes.start()] + b'class="melange-integrated ' + value + b'"' + tag[classes.end():]
        return tag[:-1] + b' class="melange-integrated">'
    raw = re.sub(rb'<body\b[^>]*>', local_body, raw, count=1)
    before, boundary, after = raw.rpartition(b'</body>')
    return before + script + boundary + after


@lru_cache(maxsize=12)
def prepared(path, mtime_ns, size, profiles_stamp=None):
    raw = path.read_bytes()
    if path.suffix == '.html' and b'<!-- melange-clean-workspace-v2 -->' not in raw:
        profiles = path.with_name('cu-member-desk-profiles.js')
        raw = integrate(raw, profiles.read_bytes() if profiles_stamp else None)
    return gzip.compress(raw, compresslevel=3), '"melange-' + hashlib.sha256(raw).hexdigest()[:24] + '"'
