# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT,DRAFT as MB_DRAFT
"""PRESS FULL-TEXT FETCHER (user 2026-07-04 press campaign: "get it to 85"). Headline-only readers
capped press accuracy twice tonight; the long-form research says body text matters. Fetches every
production article URL (member .gov press releases mostly), extracts main text (all <p> blocks,
capped), caches resumable jsonl. Polite: 0.5s spacing, honest UA, per-URL once.
Usage: python fec/collect/fetch_press_text.py   (resumable; press_text_cache.jsonl)"""
import csv, datetime, io, ipaddress, json, os, re, socket, sys, time, urllib.parse, urllib.request
from collect.campaign_sites_2026 import private_target_reason
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
CACHE = B + r'\press_text_cache.jsonl'
UA = {'User-Agent': 'MelangeSystemsResearchCollector/1.0 (public-records research)'}

# Keep IPv6 as a fallback, but avoid the intermittent Windows/Schannel resets observed on
# otherwise reachable public sites by trying their IPv4 route first.
_SYSTEM_GETADDRINFO = socket.getaddrinfo
def _ipv4_first(host, port, family=0, type=0, proto=0, flags=0):
    rows = _SYSTEM_GETADDRINFO(host, port, family, type, proto, flags)
    return sorted(rows, key=lambda row: row[0] != socket.AF_INET)
socket.getaddrinfo = _ipv4_first


_LOCAL_NAT64 = ipaddress.ip_network('64:ff9b:1::/48')   # RFC 8215 local-use prefix: never a public host


def _embedded_ipv4(address):
    """The IPv4 address inside a NAT64 (64:ff9b::/96) or 6to4 (2002::/16) IPv6 address, else None."""
    raw = address.packed
    if raw[:12] == bytes.fromhex('0064ff9b') + bytes(8):
        return ipaddress.IPv4Address(raw[12:])
    if raw[:2] == b'\x20\x02':
        return ipaddress.IPv4Address(raw[2:6])


def _is_public(address):
    """Global unicast only. is_global alone passes multicast, site-local fec0::/10 and IPv4-compatible ::a.b.c.d, so
    those are refused here as well. A NAT64 or 6to4 address is only as public as the IPv4 inside it (the wrapper's own
    range reads reserved or private, so it is ignored). Teredo 2001::/32 is refused as a whole range."""
    inner = _embedded_ipv4(address) if address.version == 6 else None
    if inner is not None:
        return _is_public(inner)
    return address.is_global and address not in _LOCAL_NAT64 and not (
        address.is_multicast or address.is_reserved or address.is_unspecified or address.is_loopback
        or address.is_link_local or address.is_private or getattr(address, 'is_site_local', False))


def _require_public(url):
    """The URLs come from a CSV, so refuse any that is not http(s) to a public address.

    Literal private hosts are refused by the campaign-site guard; the DNS answer is checked too, because a
    public-looking name can resolve inward. ponytail: urlopen resolves again after this, so a DNS server that
    answers differently the second time still gets through; pin the checked address if that ever matters."""
    reason = private_target_reason(url)
    if reason:
        raise ValueError(reason)
    parsed = urllib.parse.urlparse(url)
    try:
        answers = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme.lower() == 'https' else 80),
                                     type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError('target host does not resolve: %s' % e)
    for answer in answers:
        address = ipaddress.ip_address(answer[4][0].split('%')[0])
        address = getattr(address, 'ipv4_mapped', None) or address
        if not _is_public(address):
            raise ValueError('target host resolves to non-public address %s' % address)


class _PublicOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _require_public(newurl)   # a public page can still redirect inward
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_PublicOnlyRedirects)

done = set()
if os.path.exists(CACHE):
    for ln in open(CACHE, encoding='utf-8'):
        try:
            row = json.loads(ln)
            if (len((row.get('text') or '').split()) >= 20 or
                    row.get('classification') == 'out_of_scope_nonarticle_video'):
                done.add(row['url'])
        except Exception: pass
arts = list(csv.DictReader(io.open(os.path.join(MB_DRAFT, 'cu_press_stance_tone.csv'), encoding='utf-8-sig', newline='')))
todo = [r for r in arts if (r.get('url') or '').strip() and (r.get('url') or '').strip() not in done]
print('articles: %d | cached %d | to fetch %d' % (len(arts), len(done), len(todo)))

def extract(html):
    html = re.sub(r'<script.*?</script>|<style.*?</style>|<nav.*?</nav>|<footer.*?</footer>', ' ', html, flags=re.S | re.I)
    paras = re.findall(r'<p[^>]*>(.*?)</p>', html, flags=re.S | re.I)
    txt = ' '.join(re.sub(r'<[^>]+>', ' ', p) for p in paras)
    import html as H
    txt = H.unescape(txt)
    return re.sub(r'\s+', ' ', txt).strip()[:6000]


class _NoArticleBody(ValueError):
    """The page was FETCHED successfully but carries no extractable article body.
    Only this failure may be classified terminal; a transport failure must stay retryable."""


cf = io.open(CACHE, 'a', encoding='utf-8')
ok = fail = 0
for i, r in enumerate(todo):
    u = (r.get('url') or '').strip()
    try:
        _require_public(u)
        with _opener.open(urllib.request.Request(u, headers=UA), timeout=25) as resp:
            body = extract(resp.read().decode('utf-8', 'replace'))
        if len(body.split()) < 20:
            raise _NoArticleBody('HTTP response contained no extractable article body')
        cf.write(json.dumps({'url': u, 'text': body, 'n': len(body)}, ensure_ascii=True) + '\n')
        ok += 1
    except Exception as e:
        # The terminal classification is what audit_collection.audit_press counts as RESOLVED and
        # what the resume set at the top of this file treats as permanently done, so a DNS/429/
        # timeout failure must never earn it -- only a page we read and found bodyless.
        classification = (
            'out_of_scope_nonarticle_video'
            if isinstance(e, _NoArticleBody) and re.search(r'/(?:news/)?videos?/watch/', u, flags=re.I)
            else 'live_retry_failed'
        )
        cf.write(json.dumps({
            'url': u,
            'text': '',
            'err': str(e)[:80],
            'classification': classification,
            'attempted_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }, ensure_ascii=True) + '\n')
        fail += 1
    if (i + 1) % 100 == 0:
        cf.flush(); print('  %d/%d (ok %d, fail %d)' % (i + 1, len(todo), ok, fail), flush=True)
    time.sleep(0.5)
cf.close()
print('done: %d fetched, %d failed -> press_text_cache.jsonl' % (ok, fail))
if fail:
    sys.exit(2)
