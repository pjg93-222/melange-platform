import gzip
import http.client
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from fec.surfaces.melange_dashboard import integrate, resolve, MARKER
from fec.surfaces.staffer_live_server import Handler


def test_local_shell_keeps_published_content_intact_and_injects_at_document_end():
    raw = b'<html><head><title>Fixture</title></head><body><script>const text="</body>";</script><p>All original records</p></body></html>'
    out = integrate(raw)
    assert out.count(MARKER) == 1 and integrate(out) == out
    assert b'const text="</body>";' in out
    assert out.index(b'melange-dashboard.js') > out.index(b'All original records')
    assert out.replace(MARKER+b'<link rel="stylesheet" href="/staffer-live/melange-dashboard.css">', b'').replace(b'<script src="/staffer-live/melange-dashboard.js"></script>', b'').replace(b'<body class="melange-integrated">', b'<body>') == raw


def test_legacy_links_preserve_member_and_office_context():
    for path, view in [('/cu-member-desk.html','desk'),('/lobby-network.html','industry'),('/staffer-network.html','staffer')]:
        result = resolve(path, 's=Alex+Padilla&m=Example')
        query = parse_qs(urlsplit(result['redirect']).query)
        assert query == {'s':['Alex Padilla'], 'v':[view]}
    assert resolve('/Moneyball%20Dashboard.html')['redirect'] == '/'
    for path in ['/../staffer_user_assertions.json','/%2e%2e/secrets','/data/members/staff_directory.json','//Moneyball.html']:
        assert resolve(path) is None


def test_mixed_member_staffer_url_is_canonicalized_before_member_app_starts():
    result = resolve('/', 'm=Example&v=staffer&person=politico%3A1&route_target=person%3A2')
    assert parse_qs(urlsplit(result['redirect']).query) == {
        'v': ['staffer'], 'person': ['politico:1'], 'route_target': ['person:2']}
    assert resolve('/', 'm=Example') == {'file': 'Moneyball.html', 'html': True}


def test_new_shell_is_selected_before_the_first_body_paint():
    for body in [b'<body class=netmode>', b'<body class="netmode">', b"<body class='netmode'>"]:
        output = integrate(b'<html><head></head>' + body + b'</body></html>')
        assert b'<body class="melange-integrated netmode">' in output
        assert integrate(output) == output


def test_local_profiles_are_externalized_only_when_identical():
    profiles = b'window.__melangeProfileMerge({"all":"records"});'
    raw = b'<html><head></head><body><script>\n' + profiles + b'\n</script></body></html>'
    output = integrate(raw, profiles)
    assert b'<script src="/cu-member-desk-profiles.js"></script>' in output
    assert profiles not in output
    assert profiles in integrate(raw, b'different generation')


def test_clean_workspace_bypasses_legacy_injection():
    raw = b'<!doctype html><!-- melange-clean-workspace-v2 --><html><body>Clean workspace</body></html>'
    assert integrate(raw, b'large legacy profile payload') == raw


def test_clean_preview_routes_and_assets_are_scoped_cached_and_compressed(tmp_path, monkeypatch):
    from fec.surfaces import staffer_live_server
    raw = b'<!doctype html><!-- melange-clean-workspace-v2 --><html><body>Clean workspace</body></html>'
    (tmp_path / 'Moneyball_Redesign.html').write_bytes(raw)
    (tmp_path / 'Moneyball.html').write_bytes(b'Published workspace')
    assets = tmp_path / 'assets'
    (assets / 'approved' / 'data').mkdir(parents=True)
    (assets / 'approved' / 'data' / 'catalog.json').write_bytes(b'{"members":[]}')
    (assets / 'private.json').write_bytes(b'{"private":true}')
    monkeypatch.setattr(staffer_live_server, 'ASSETS', assets)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.dashboard = SimpleNamespace(source_root=tmp_path)
    server.dashboard_file = 'Moneyball_Redesign.html'
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    def get(path, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
        try:
            connection.request('GET', path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()
    try:
        for path in ['/', '/lobby-bills.html', '/lobby-forecast.html']:
            status, headers, body = get(path, {'Accept-Encoding': 'gzip'})
            assert status == 200 and gzip.decompress(body) == raw
            assert b'melange-dashboard.js' not in gzip.decompress(body)
        path = '/staffer-live/approved/data/catalog.json'
        status, headers, body = get(path, {'Accept-Encoding': 'gzip'})
        assert status == 200 and gzip.decompress(body) == b'{"members":[]}'
        assert get(path, {'If-None-Match': headers['ETag']})[0] == 304
        assert get('/staffer-live/approved/../private.json')[0] == 404
        assert get('/staffer-live/approved/data/../../private.json')[0] == 404
        assert (tmp_path / 'Moneyball.html').read_bytes() == b'Published workspace'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_member_staffer_links_resolve_people_and_prefill_path_target():
    root = Path(__file__).resolve().parents[3]
    member_template = (root / 'fec/surfaces/templates/make_member_desk_00.tpl').read_text(encoding='utf-8')
    shell = (root / 'fec/surfaces/staffer_live/melange-dashboard.js').read_text(encoding='utf-8')
    route = (root / 'fec/surfaces/staffer_live/staffer-route.js').read_text(encoding='utf-8')
    assert 'data-staffer-source' in member_template and 'quorum:${sf.id}' in member_template
    assert '__melangeOpenStaffer' in member_template and '__melangeOpenStaffer' in shell
    assert '/api/staffer-person-link' in shell and "url.searchParams.set('route_target'" in shell
    assert 'window.StafferRoute={openTarget}' in route and "openTarget(routeTarget)" in route
    assert "$('route-form').requestSubmit()" not in route[route.index('async function openTarget'):route.index('window.StafferRoute')]


def test_dashboard_http_serves_detail_views_gzip_cache_and_latest_people_navigation(tmp_path):
    raw = b'<html><head></head><body>Published data</body></html>'
    for name in ['Moneyball.html','lobby-interactions.html','lobby-bills.html','lobby-champions.html','lobby-forecast.html','lobby-screen.html']:
        (tmp_path/name).write_bytes(raw)
    profiles = b'window.__melangeProfileMerge({"members_heavy":[{"name":"Example"}]});'
    (tmp_path/'cu-member-desk-profiles.js').write_bytes(profiles)
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.dashboard = SimpleNamespace(source_root=tmp_path)
    thread = threading.Thread(target=lambda:server.serve_forever(poll_interval=.01),daemon=True);thread.start()
    def get(path, headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=5)
        try:
            connection.request('GET',path,headers=headers or {})
            response=connection.getresponse()
            return response.status,dict(response.getheaders()),response.read()
        finally: connection.close()
    try:
        for path in ['/','/Moneyball.html','/lobby-interactions.html','/lobby-bills.html','/lobby-champions.html','/lobby-forecast.html','/lobby-screen.html']:
            status, headers, body = get(path,{'Accept-Encoding':'gzip'})
            assert status == 200 and headers['Content-Encoding'] == 'gzip'
            assert gzip.decompress(body) == integrate(raw)
            assert get(path,{'If-None-Match':headers['ETag']})[0] == 304
        status, headers, body = get('/staffer-network.html?s=Alex+Padilla')
        assert status == 302 and headers['Location'] == '/?s=Alex+Padilla&v=staffer'
        assert get('/staffer-live/melange-dashboard.js')[0] == 200
        assert get('/staffer-live/melange-dashboard.css')[0] == 200
        status, headers, body = get('/cu-member-desk-profiles.js', {'Accept-Encoding':'gzip'})
        assert status == 200 and gzip.decompress(body) == profiles
        assert headers['Content-Type'].startswith('application/javascript')
        assert get('/cu-member-desk-profiles.js', {'If-None-Match':headers['ETag']})[0] == 304
        assert get('/not-a-dashboard.html')[0] == 404
        assert (tmp_path/'Moneyball.html').read_bytes() == raw
    finally:
        server.shutdown();server.server_close();thread.join(timeout=1)
