# -*- coding: utf-8 -*-
"""Cloudflare Access JWT gate: stdlib only, one file, no app imports.

This file is deployed as three byte-identical copies: fec/access_gate.py (Melange desk),
mentat/app/access_gate.py (Mentat console) and server/research/access_gate.py (Terrier).
Never edit a copy; change one, copy it over the others, and compare sha256 at release.
(.gitattributes pins it to LF so the bytes do not depend on the checkout's autocrlf.)

Environment (all optional unless the gate is enforced):
  BIND_HOST            address the app binds; default 127.0.0.1.  Loopback is 127.0.0.1,
                       localhost, ::1, [::1].  Anything else (including empty, which binds
                       every interface) turns enforcement on.
  ALLOWED_HOSTS        comma-separated exact lowercase Host names, added to the loopback names.
                       A "*" anywhere is a config error.  The :port of a Host is ignored.
  ACCESS_AUD           the Access application's AUD tag.            (required when enforced)
  ACCESS_ISSUER        exact issuer, https://<team>.cloudflareaccess.com.   (required when enforced)
  ACCESS_JWKS_FILE     local copy of https://<team>.cloudflareaccess.com/cdn-cgi/access/certs,
                       refreshed out of band; refused once older than 24 h or dated more than
                       60 s in the future.  (required when enforced)
  ACCESS_SERVICE_CNS   optional comma-separated service-token common_name values, verbatim as
                       they appear in the token (Cloudflare's "<client id>.access").  Such a
                       token may reach /healthz and /version only.
  ACCESS_ENFORCE       "1" enforces even on a loopback bind; "0" or unset adds nothing.  It
                       can only add enforcement: a non-loopback bind is always enforced.
  GIT_SHA, BUILD_TIME, SNAPSHOT_ID_FILE   read by version_payload() for /version.

Not enforced: every check is skipped and the app behaves exactly as before (two guards remain:
HTTP_-style header names, and a real non-loopback bind, are denied; see Wiring below).
Enforced: every request needs a Cf-Access-Jwt-Assertion header (the cookie is never read)
carrying an RS256 token that verifies against ACCESS_JWKS_FILE with the right aud and iss and
an email, plus an allowed Host.  Exceptions: /healthz and /version from a loopback peer with
no proxy headers (no Cf-*, X-Forwarded-For, Forwarded, X-Real-IP) need no token, because a
cloudflared tunnel also connects from loopback; with a token, a service token listed in
ACCESS_SERVICE_CNS may fetch just those two routes.  Everything unexpected is a denial.

Wiring (http.server):
    class Handler(AccessMixin, BaseHTTPRequestHandler):
        access_app = "mentat"
        access_cfg = require_config_or_exit()    # once, at startup
        def do_GET(self):
            ...                                  # self.send_security_headers() before end_headers()
AccessMixin overrides parse_request(), so every request (GET, HEAD, POST, any unknown method) is
gated once, before any do_* runs; a denial has already been answered 403 and logged.  Apps must
NOT call access_check() per method any more (a second call is harmless but redundant).  The
mixin also checks the address the server really bound (self.server.server_address[0]): an
unenforced config on a non-loopback bind is refused with 403, whatever BIND_HOST said.
Denials use send_response_only(), so http.server's own log line (client IP and query string)
is not written for them; override log_message() in the app to keep allowed requests equally clean.

Wiring (any other server, e.g. Django behind Waitress, which binds by its own flags):
    assert_bind_consistent(cfg, bound_host)      # once, at startup, with the address really bound
    check_request(cfg, method, path_with_query, headers, client_ip)    # per request
Pass real header names: Django's request.headers and request.get_full_path(), never request.META.
Any header name starting with HTTP_ (META style, which hides Cf-* and X-Forwarded-For from the
proxy test) is denied, enforced or not.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import NamedTuple, Optional

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
HEALTH_ROUTES = ("/healthz", "/version")
LEEWAY = 60                 # seconds of clock skew allowed on exp and nbf
JWKS_MAX_AGE = 24 * 3600    # seconds; an older JWKS file means the refresh job is dead
MIN_MODULUS_BITS = 2048
MAX_TOKEN = 16384
# DER prefix of DigestInfo for SHA-256 (RFC 8017 section 9.2, note 1).  We build the expected
# encoded message from it; a received one is never parsed.
_SHA256_PREFIX = b"\x30\x31\x30\x0d\x06\x09\x60\x86\x48\x01\x65\x03\x04\x02\x01\x05\x00\x04\x20"
_B64U = re.compile(r"[A-Za-z0-9_-]+")
_HOST = re.compile(r"(\[[0-9a-f:.]+\]|[a-z0-9._-]+)(?::[0-9]{1,5})?")
_FORWARDED = ("x-forwarded-for", "forwarded", "x-real-ip")


class AccessConfigError(Exception):
    """The environment cannot describe a safe gate."""


class AccessDenied(Exception):
    """Fail-closed refusal; .reason is a short fixed string, never request data."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class Decision(NamedTuple):
    allowed: bool
    status: int
    user: Optional[str]
    reason: str


@dataclass(frozen=True)
class AccessConfig:
    bind_host: str = "127.0.0.1"
    allowed_hosts: frozenset = LOOPBACK
    aud: str = ""
    issuer: str = ""
    jwks_file: str = ""
    service_cns: frozenset = frozenset()
    enforce: bool = False       # ACCESS_ENFORCE=1; a non-loopback bind enforces regardless

    @property
    def enforced(self):
        return self.enforce or self.bind_host.strip().lower() not in LOOPBACK

    @classmethod
    def from_env(cls, environ=os.environ):
        def get(name):
            return (environ.get(name) or "").strip()

        bind = environ.get("BIND_HOST")
        bind = "127.0.0.1" if bind is None else bind.strip().lower()
        raw_hosts = get("ALLOWED_HOSTS")
        if "*" in raw_hosts:
            raise AccessConfigError("ALLOWED_HOSTS must list exact names, no wildcard")
        hosts = LOOPBACK | {h.strip().lower() for h in raw_hosts.split(",") if h.strip()}
        flag = get("ACCESS_ENFORCE")
        if flag not in ("", "0", "1"):
            raise AccessConfigError("ACCESS_ENFORCE must be 1 or 0")
        cfg = cls(bind_host=bind, allowed_hosts=frozenset(hosts), aud=get("ACCESS_AUD"),
                  issuer=get("ACCESS_ISSUER"), jwks_file=get("ACCESS_JWKS_FILE"),
                  service_cns=frozenset(c.strip() for c in get("ACCESS_SERVICE_CNS").split(",") if c.strip()),
                  enforce=flag == "1")
        if cfg.enforced:
            missing = [n for n, v in (("ACCESS_AUD", cfg.aud), ("ACCESS_ISSUER", cfg.issuer),
                                      ("ACCESS_JWKS_FILE", cfg.jwks_file)) if not v]
            if missing:
                raise AccessConfigError("access gate is enforced but %s not set" % ", ".join(missing))
        return cfg


def require_config_or_exit(environ=os.environ):
    """Startup helper: the config, or one line on stderr and exit status 2."""
    try:
        return AccessConfig.from_env(environ)
    except AccessConfigError as exc:
        sys.stderr.write("access gate: %s\n" % exc)
        sys.exit(2)


# --- token verification -------------------------------------------------------------------

def _b64u(text):
    if not isinstance(text, str) or not _B64U.fullmatch(text):
        raise AccessDenied("base64url")   # also rejects "=" padding and the empty string
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError:
        raise AccessDenied("base64url")
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != text:   # canonical form only:
        raise AccessDenied("base64url")                                       # stray slack bits decode alike
    return raw


def _json(text):
    try:
        obj = json.loads(_b64u(text).decode("utf-8"))
    except ValueError:
        raise AccessDenied("json")
    if not isinstance(obj, dict):
        raise AccessDenied("json")
    return obj


_JWKS = {}   # path -> (stamp, {kid: jwk}); entries are replaced whole, so threads need no lock


def _jwks(path, now, force=False):
    try:
        st = os.stat(path)
    except OSError:
        raise AccessDenied("jwks unreadable")
    if not -LEEWAY <= now - st.st_mtime <= JWKS_MAX_AGE:   # two-sided: a future-dated file is stale too
        raise AccessDenied("jwks stale")
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _JWKS.get(path)
    if hit and hit[0] == stamp and not force:
        return hit[1]
    try:
        with open(path, "rb") as f:
            doc = json.loads(f.read(1 << 20).decode("utf-8"))
    except (OSError, ValueError):
        raise AccessDenied("jwks unreadable")
    keys = doc.get("keys") if isinstance(doc, dict) else None   # Cloudflare also ships public_cert(s); unused
    if not isinstance(keys, list) or not keys:
        raise AccessDenied("jwks malformed")
    table = {}
    for jwk in keys:
        kid = jwk.get("kid") if isinstance(jwk, dict) else None
        if not isinstance(kid, str) or not kid or kid in table:
            raise AccessDenied("jwks malformed")
        table[kid] = jwk
    _JWKS[path] = (stamp, table)
    return table


def _rsa_key(jwk):
    if jwk.get("kty") != "RSA" or jwk.get("alg", "RS256") != "RS256" or jwk.get("use", "sig") != "sig":
        raise AccessDenied("key type")
    n = int.from_bytes(_b64u(jwk.get("n")), "big")
    e = int.from_bytes(_b64u(jwk.get("e")), "big")
    if n.bit_length() < MIN_MODULUS_BITS:
        raise AccessDenied("key size")
    if e < 3 or e % 2 == 0:
        raise AccessDenied("key exponent")
    return n, e


def _rs256(n, e, sig, signed):
    """RSASSA-PKCS1-v1_5 by encode-and-compare (RFC 8017 section 8.2.2): rebuild the one valid
    encoded message and compare bytes; no padding or ASN.1 is parsed out of the signature."""
    k = (n.bit_length() + 7) // 8
    if len(sig) != k:
        raise AccessDenied("sig length")
    s = int.from_bytes(sig, "big")
    if s >= n:
        raise AccessDenied("sig range")
    em = pow(s, e, n).to_bytes(k, "big")
    t = _SHA256_PREFIX + hashlib.sha256(signed).digest()
    want = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t   # k >= 256, so >= 8 pad bytes
    if not hmac.compare_digest(em, want):
        raise AccessDenied("bad signature")


def _ts(claims, name):
    v = claims.get(name)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise AccessDenied(name)
    return v


def _identity(claims):
    """(user, service common_name or None): an email is a person, else a service token."""
    email = claims.get("email")
    if isinstance(email, str) and email.strip():
        return email.strip(), None
    cn = claims.get("common_name")
    if isinstance(cn, str) and cn.strip():
        return "service:" + cn.strip(), cn.strip()
    raise AccessDenied("no identity")


def _verify(token, cfg, now):
    if not (cfg.aud and cfg.issuer and cfg.jwks_file):
        raise AccessDenied("config")
    if not isinstance(token, str) or not 0 < len(token) <= MAX_TOKEN:
        raise AccessDenied("token size")
    parts = token.split(".")
    if len(parts) != 3:
        raise AccessDenied("token shape")
    h, p, s = parts
    header, claims, sig = _json(h), _json(p), _b64u(s)
    if header.get("alg") != "RS256":
        raise AccessDenied("alg")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise AccessDenied("kid")
    if "crit" in header:
        raise AccessDenied("crit")
    keys = _jwks(cfg.jwks_file, now)
    if kid not in keys:
        # ponytail: unthrottled; a flood of bogus kids costs one small file read each.
        keys = _jwks(cfg.jwks_file, now, force=True)
        if kid not in keys:
            raise AccessDenied("unknown kid")
    n, e = _rsa_key(keys[kid])
    _rs256(n, e, sig, (h + "." + p).encode("ascii"))
    # The signature holds; only now are the claims believed.
    aud = claims.get("aud")
    if cfg.aud not in ([aud] if isinstance(aud, str) else aud if isinstance(aud, list) else []):
        raise AccessDenied("aud")
    if claims.get("iss") != cfg.issuer:
        raise AccessDenied("iss")
    if not now < _ts(claims, "exp") + LEEWAY:
        raise AccessDenied("exp")
    if "nbf" in claims and not now >= _ts(claims, "nbf") - LEEWAY:
        raise AccessDenied("nbf")
    _identity(claims)
    return claims


def verify(token, cfg, now=None):
    """Claims of a valid Cloudflare Access token, or AccessDenied.  Never raises anything else."""
    now = time.time() if now is None else now
    try:
        return _verify(token, cfg, now)
    except AccessDenied:
        raise
    except Exception as exc:   # any parser surprise is a denial, never an allow or a 500
        raise AccessDenied("malformed token") from exc


# --- request decision ---------------------------------------------------------------------

def host_ok(host, cfg):
    h = (host or "").strip().lower()
    m = _HOST.fullmatch(h)
    return h in cfg.allowed_hosts or bool(m and m.group(1) in cfg.allowed_hosts)


def _header(headers, name):
    """The one value of a header, matched case-insensitively; absent or repeated gives None."""
    found = [str(v) for k, v in headers.items() if k.lower() == name]
    return found[0] if len(found) == 1 else None


def _loopback_peer(ip):
    try:
        addr = ipaddress.ip_address(str(ip))
    except ValueError:
        return False
    return (getattr(addr, "ipv4_mapped", None) or addr).is_loopback


def _proxied(headers):
    return any(k.lower().startswith("cf-") or k.lower() in _FORWARDED for k, _ in headers.items())


def _loopback_bind(host):
    return str(host).strip().lower() in LOOPBACK or _loopback_peer(host)   # "", 0.0.0.0, :: are not


def assert_bind_consistent(cfg, bound_host):
    """For apps that bind by their own flags: call at startup with the address really bound."""
    if not cfg.enforced and not _loopback_bind(bound_host):
        raise AccessConfigError("bound to %r but the access gate is not enforced" % (bound_host,))


def _deny(reason):
    return Decision(False, 403, None, reason)


def check_request(cfg, method, path, headers, client_ip, now=None):
    if any(str(k).lower().startswith("http_") for k, _ in headers.items()):
        return _deny("meta header names")   # a Django META key would hide Cf-* / X-Forwarded-For
    if not cfg.enforced:
        return Decision(True, 200, None, "not enforced")
    route = str(path).split("?", 1)[0]
    health = route in HEALTH_ROUTES
    if not host_ok(_header(headers, "host"), cfg):
        return _deny("host")
    if health and _loopback_peer(client_ip) and not _proxied(headers):
        return Decision(True, 200, None, "loopback health")
    token = _header(headers, "cf-access-jwt-assertion")
    if not token:
        return _deny("no token")
    try:
        user, cn = _identity(verify(token, cfg, now))
    except AccessDenied as exc:
        return _deny(exc.reason)
    if cn is not None and not (health and cn in cfg.service_cns):
        return _deny("service token")
    return Decision(True, 200, user, "ok")


# --- logging, headers, /version, http.server glue -----------------------------------------

def audit_line(app, user, method, path, status, reason=None):
    """One JSON line: when, which app, who, what route (no query), which status.  Never the
    query string, client IP, headers or body.  reason is a fixed denial code from this module."""
    rec = {"ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "app": app, "user": user or "-", "method": method,
           "route": str(path).split("?", 1)[0][:256], "status": status}
    if reason:
        rec["reason"] = reason
    return json.dumps(rec, separators=(",", ":"))


def security_headers(cfg, frame_options="SAMEORIGIN"):
    out = [("Referrer-Policy", "no-referrer"), ("X-Content-Type-Options", "nosniff"),
           ("Cross-Origin-Resource-Policy", "same-origin"), ("X-Frame-Options", frame_options)]
    if cfg.enforced:
        out.append(("Cache-Control", "no-store"))
    return out


def version_payload(app, environ=os.environ):
    snap = "unknown"
    path = environ.get("SNAPSHOT_ID_FILE")
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                snap = f.readline(256).strip() or "unknown"
        except (OSError, ValueError):
            pass
    return {"app": app, "git_sha": environ.get("GIT_SHA") or "unknown",
            "build_time": environ.get("BUILD_TIME") or "unknown", "snapshot_id": snap}


class AccessMixin:
    """For BaseHTTPRequestHandler subclasses; set access_app and access_cfg on the class."""
    access_app = "app"
    access_cfg = None
    access_frame_options = "SAMEORIGIN"
    access_user = None

    def send_security_headers(self):
        for name, value in security_headers(self.access_cfg, self.access_frame_options):
            self.send_header(name, value)

    def parse_request(self):
        """Gate every method once, before any do_*: handle_one_request stops on False.  If the
        parse itself failed, http.server has already answered and access_check is not reached."""
        return super().parse_request() and self.access_check()

    def access_check(self):
        """True to carry on.  False means a 403 has been sent and logged; just return."""
        if self.access_cfg is None:
            raise AccessConfigError("access_cfg is not set on the handler")   # no response: fail closed
        if not self.access_cfg.enforced and not _loopback_bind(self.server.server_address[0]):
            d = _deny("unenforced public bind")   # the address really bound decides, not BIND_HOST
        else:
            d = check_request(self.access_cfg, self.command, self.path, self.headers, self.client_address[0])
        self.access_user = d.user
        if d.allowed:
            return True
        body = b"forbidden\n"
        self.send_response_only(d.status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")   # the request body, if any, is unread
        self.send_security_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True
        sys.stderr.write(audit_line(self.access_app, None, self.command, self.path, d.status, d.reason) + "\n")
        return False
