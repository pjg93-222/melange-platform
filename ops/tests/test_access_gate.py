# -*- coding: utf-8 -*-
"""Contract suite for fec/access_gate.py, the Cloudflare Access JWT gate shared byte-for-byte
by the Melange desk, the Mentat console and Terrier.

The gate is the only thing between a public hostname and the apps, so this suite attacks it:
every claim, key and signature rule gets a failing case that must be denied FOR ITS OWN
REASON (each denial is a fixed string, so a typo in the gate cannot hide behind a generic
"denied"), the RSA check is fed hand-forged encoded messages to prove it compares bytes and
parses nothing, and the request rules are driven end to end with a real http.client message.

Keys are generated at run time with `cryptography` (pinned in requirements.txt) and signed
in memory; no private key, PEM or pre-signed token is ever written to the tree.  `now` is
injected everywhere, nothing sleeps, and the handler glue is exercised on a BaseHTTPRequestHandler
built without its constructor, except ServerTests, which run real ThreadingHTTPServers on
127.0.0.1:0 (and one on 0.0.0.0:0, reached via 127.0.0.1).  Skips cleanly without `cryptography`.

Runs as:  python -m pytest ops/tests/test_access_gate.py -q   or   python ops/tests/test_access_gate.py
"""
import base64
import contextlib
import hashlib
import hmac
import http.client
import importlib.util
import io
import json
import os
import random
import socket
import string
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False

_HERE = os.path.dirname(os.path.abspath(__file__))
_GATE_PATH = os.path.normpath(os.path.join(_HERE, os.pardir, os.pardir, "fec", "access_gate.py"))
_spec = importlib.util.spec_from_file_location("access_gate_under_test", _GATE_PATH)
gate = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = gate   # dataclasses resolves string annotations through sys.modules
_spec.loader.exec_module(gate)

NOW = 1_800_000_000
AUD = "test-aud-tag"
ISS = "https://example-team.cloudflareaccess.com"
HOST = "desk.example.test"
PUBLIC_IP = "203.0.113.9"
EMAIL = "analyst@example.test"
SERVICE_CN = "svc-client.access"
HEADER = {"alg": "RS256", "kid": "kid-a", "typ": "JWT"}
GONE = object()   # claim override meaning "leave this claim out"

needs_crypto = unittest.skipUnless(HAVE_CRYPTO, "cryptography is not installed")
_KEYS = {}


def key(name, bits=2048):
    if name not in _KEYS:
        _KEYS[name] = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    return _KEYS[name]


def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def be(n):
    return n.to_bytes((n.bit_length() + 7) // 8, "big")


def jwk(k, kid, **over):
    nums = k.public_key().public_numbers()
    out = {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256", "n": b64u(be(nums.n)), "e": b64u(be(nums.e))}
    out.update(over)
    return out


def claims(**over):
    out = {"aud": [AUD], "iss": ISS, "exp": NOW + 3600, "nbf": NOW - 10, "iat": NOW - 10, "email": EMAIL}
    out.update(over)
    return {k: v for k, v in out.items() if v is not GONE}


def sign_text(k, signing):
    return b64u(k.sign(signing.encode("ascii"), padding.PKCS1v15(), hashes.SHA256()))


def sign_parts(k, header, payload):
    enc = lambda o: b64u(o if isinstance(o, bytes) else json.dumps(o).encode("utf-8"))
    signing = enc(header) + "." + enc(payload)
    return signing, sign_text(k, signing)


def with_slack(seg):
    """The same bytes spelled non-canonically: a stray bit set in the unused tail of the last char."""
    alphabet = string.ascii_uppercase + string.ascii_lowercase + string.digits + "-_"
    assert len(seg) % 4 in (2, 3), "this segment has no slack bits"
    out = seg[:-1] + alphabet[alphabet.index(seg[-1]) | 1]
    assert out != seg and base64.urlsafe_b64decode(out + "==") == base64.urlsafe_b64decode(seg + "==")
    return out


def token(k=None, payload=None, header=None):
    signing, sig = sign_parts(k or key("a"), HEADER if header is None else header,
                              claims() if payload is None else payload)
    return signing + "." + sig


def service_token(**over):
    return token(payload=claims(email=GONE, common_name=SERVICE_CN, **over))


def message(pairs):
    msg = http.client.HTTPMessage()
    for name, value in pairs:
        msg.add_header(name, value)
    return msg


class GateCase(unittest.TestCase):
    """A temp JWKS (Cloudflare certs shape) holding key a as kid-a, and an enforced config."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.jwks_path = os.path.join(tmp.name, "certs.json")
        self.write_jwks([jwk(key("a"), "kid-a")])
        self.cfg = self.config()

    def write_jwks(self, jwks, mtime=NOW - 3600):
        # The gate caches by (mtime_ns, size), so every rewrite here gets its own mtime (one more
        # second each time); otherwise a same-size rewrite would be served from the cache.
        self.writes = getattr(self, "writes", 0) + 1
        mtime += self.writes
        doc = {"keys": jwks, "public_cert": {"kid": "kid-a", "cert": "unused"}, "public_certs": []}
        with open(self.jwks_path, "w", encoding="utf-8") as f:
            json.dump(doc, f, sort_keys=True)
        os.utime(self.jwks_path, (mtime, mtime))

    def config(self, **env):
        e = {"BIND_HOST": "0.0.0.0", "ALLOWED_HOSTS": HOST, "ACCESS_AUD": AUD, "ACCESS_ISSUER": ISS,
             "ACCESS_JWKS_FILE": self.jwks_path}
        e.update(env)
        return gate.AccessConfig.from_env(e)

    def denied(self, tok, reason, cfg=None, now=NOW):
        with self.assertRaises(gate.AccessDenied) as cm:
            gate.verify(tok, cfg or self.cfg, now)
        self.assertIn(cm.exception.reason, (reason,) if isinstance(reason, str) else reason)


@needs_crypto
class VerifyTests(GateCase):
    def test_valid_token_passes(self):
        got = gate.verify(token(), self.cfg, NOW)
        self.assertEqual(got["email"], EMAIL)
        self.assertEqual(gate.verify(token(payload=claims(aud=AUD)), self.cfg, NOW)["aud"], AUD)  # aud as a bare string

    def test_flipped_signature_byte_fails(self):
        signing, sig = sign_parts(key("a"), HEADER, claims())
        raw = bytearray(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)))
        for index in (len(raw) // 2, len(raw) - 1):   # not byte 0: that could push s past n and change the reason
            bad = bytearray(raw)
            bad[index] ^= 0x01
            self.denied(signing + "." + b64u(bytes(bad)), "bad signature")

    def test_signature_length_must_equal_modulus_length(self):
        signing, sig = sign_parts(key("a"), HEADER, claims())
        raw = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
        self.assertEqual(len(raw), 256)
        for bad in (raw[:-1], raw + b"\x00", b"\x00" + raw, raw[1:]):
            self.denied(signing + "." + b64u(bad), "sig length")

    def test_signature_value_must_be_below_modulus(self):
        signing, _ = sign_parts(key("a"), HEADER, claims())
        self.denied(signing + "." + b64u(b"\xff" * 256), "sig range")

    def test_wrong_audience_fails(self):
        for aud in ("other-aud", ["x", "y"], [], GONE, 7, None, {"a": AUD}):
            with self.subTest(aud=aud):
                self.denied(token(payload=claims(aud=aud)), "aud")

    def test_audience_list_containing_ours_passes(self):
        self.assertEqual(gate.verify(token(payload=claims(aud=["x", AUD, "y"])), self.cfg, NOW)["email"], EMAIL)

    def test_wrong_issuer_fails(self):
        for iss in ("https://evil.cloudflareaccess.com", ISS + "/", ISS.upper(), GONE, None, [ISS]):
            with self.subTest(iss=iss):
                self.denied(token(payload=claims(iss=iss)), "iss")

    def test_expired_past_leeway_fails(self):
        self.assertTrue(gate.verify(token(payload=claims(exp=NOW - 59)), self.cfg, NOW))   # inside the 60 s leeway
        for exp in (NOW - 60, NOW - 61, NOW - 10_000):
            with self.subTest(exp=exp):
                self.denied(token(payload=claims(exp=exp)), "exp")

    def test_exp_is_required_and_must_be_a_finite_number(self):
        for exp in (GONE, None, "9999999999", True, [NOW + 99], float("inf"), float("nan"), 10 ** 400):
            with self.subTest(exp=exp):
                self.assertRaises(gate.AccessDenied, gate.verify, token(payload=claims(exp=exp)), self.cfg, NOW)
        raw = json.dumps(claims()).replace(str(NOW + 3600), "1e999").encode()   # parses to +inf
        self.denied(token(payload=raw), "exp")

    def test_future_nbf_past_leeway_fails(self):
        self.assertTrue(gate.verify(token(payload=claims(nbf=NOW + 60)), self.cfg, NOW))   # inside the leeway
        self.assertTrue(gate.verify(token(payload=claims(nbf=GONE)), self.cfg, NOW))       # nbf is optional
        for nbf in (NOW + 61, NOW + 10_000, None, "0", float("inf")):
            with self.subTest(nbf=nbf):
                self.denied(token(payload=claims(nbf=nbf)), "nbf")

    def test_alg_none_fails(self):
        signing, sig = sign_parts(key("a"), {"alg": "none", "kid": "kid-a"}, claims())
        self.denied(signing + ".", "base64url")        # unsigned: the empty signature never parses
        self.denied(signing + "." + sig, "alg")        # a real signature does not rescue alg none
        for alg in ("None", "NONE", "rs256", "RS384", "PS256", "", None, ["RS256"]):
            with self.subTest(alg=alg):
                self.denied(token(header={"alg": alg, "kid": "kid-a"}), "alg")

    def test_hs256_signed_with_the_public_key_fails(self):
        k = key("a")
        secrets = (k.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo),
                   be(k.public_key().public_numbers().n), json.dumps(jwk(k, "kid-a")).encode())
        for secret in secrets:
            h, p = b64u(json.dumps({"alg": "HS256", "kid": "kid-a"}).encode()), b64u(json.dumps(claims()).encode())
            mac = hmac.new(secret, (h + "." + p).encode(), hashlib.sha256).digest()
            self.denied(h + "." + p + "." + b64u(mac), "alg")

    def test_missing_kid_fails(self):
        for kid in (GONE, "", None, 7, ["kid-a"]):
            with self.subTest(kid=kid):
                header = {k: v for k, v in {"alg": "RS256", "kid": kid}.items() if v is not GONE}
                self.denied(token(header=header), "kid")

    def test_unknown_kid_fails_after_one_reload(self):
        other = token(k=key("b"), header={"alg": "RS256", "kid": "kid-b"})
        self.denied(other, "unknown kid")
        self.write_jwks([jwk(key("a"), "kid-a"), jwk(key("b"), "kid-b")])   # the file grew: picked up by the stamp
        self.assertEqual(gate.verify(other, self.cfg, NOW)["email"], EMAIL)

    def test_unknown_kid_forces_a_reload_even_when_the_file_stamp_is_unchanged(self):
        self.assertTrue(gate.verify(token(), self.cfg, NOW))   # warm the cache
        before = os.stat(self.jwks_path)
        self.write_jwks([jwk(key("b"), "kid-b")])              # same size (kid-a -> kid-b), key a replaced by b
        os.utime(self.jwks_path, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = os.stat(self.jwks_path)
        self.assertEqual((before.st_mtime_ns, before.st_size), (after.st_mtime_ns, after.st_size))
        self.assertTrue(gate.verify(token(k=key("b"), header={"alg": "RS256", "kid": "kid-b"}), self.cfg, NOW))
        self.denied(token(), "unknown kid")                    # and kid-a is gone now

    def test_stale_jwks_fails(self):
        self.write_jwks([jwk(key("a"), "kid-a")], mtime=NOW - 23 * 3600)
        self.assertTrue(gate.verify(token(), self.cfg, NOW))
        self.write_jwks([jwk(key("a"), "kid-a")], mtime=NOW - 25 * 3600)
        self.denied(token(), "jwks stale")

    def test_future_dated_jwks_is_stale_beyond_the_clock_skew_leeway(self):
        self.write_jwks([jwk(key("a"), "kid-a")], mtime=NOW + 3600)   # an hour ahead: a clock or touch trick
        self.denied(token(), "jwks stale")
        self.write_jwks([jwk(key("a"), "kid-a")], mtime=NOW + 30)     # 30 s ahead is ordinary skew
        self.assertTrue(gate.verify(token(), self.cfg, NOW))

    def test_non_canonical_base64_is_denied_in_every_segment(self):
        pad = lambda o: json.dumps(o).encode() + b" " * ((1 - len(json.dumps(o))) % 3)   # len % 3 == 1: 4 slack bits
        h, p = b64u(pad(HEADER)), b64u(pad(claims()))
        signing = h + "." + p
        good = signing + "." + sign_text(key("a"), signing)
        self.assertTrue(gate.verify(good, self.cfg, NOW))
        sig = good.rsplit(".", 1)[1]
        for name, tok in (("header", self.resigned(with_slack(h), p)), ("payload", self.resigned(h, with_slack(p))),
                          ("signature", signing + "." + with_slack(sig))):   # each carries a VALID signature
            with self.subTest(name):
                self.denied(tok, "base64url")
        self.write_jwks([jwk(key("a"), "kid-a", n=with_slack(jwk(key("a"), "kid-a")["n"]))])   # JWK members too
        self.denied(good, "base64url")

    @staticmethod
    def resigned(h, p):
        return h + "." + p + "." + sign_text(key("a"), h + "." + p)

    def test_missing_or_broken_jwks_fails_closed(self):
        os.remove(self.jwks_path)
        self.denied(token(), "jwks unreadable")
        for body in ("not json", "[]", '{"keys": []}', '{"keys": {"kid": "kid-a"}}', '{"public_cert": {}}',
                     json.dumps({"keys": [jwk(key("a"), "kid-a"), jwk(key("a"), "kid-a")]}),   # duplicate kid
                     json.dumps({"keys": [{"kty": "RSA"}]})):                                 # no kid
            with self.subTest(body=body[:40]):
                with open(self.jwks_path, "w", encoding="utf-8") as f:
                    f.write(body)
                os.utime(self.jwks_path, (NOW - 60, NOW - 60))
                self.assertRaises(gate.AccessDenied, gate.verify, token(), self.cfg, NOW)

    def test_weak_or_unsuitable_keys_are_rejected(self):
        self.write_jwks([jwk(key("small", 1024), "kid-a")])
        self.denied(token(k=key("small", 1024)), "key size")   # a perfectly valid signature by a 1024-bit key
        cases = {"key type": [{"kty": "EC"}, {"alg": "RS384"}, {"use": "enc"}],
                 "key exponent": [{"e": b64u(b"\x02")}, {"e": b64u(b"\x01")}, {"e": b64u(b"\x01\x00\x00")}, {"e": b64u(b"\x00")}],
                 "base64url": [{"n": "!!"}, {"n": None}, {"e": "AQAB="}]}
        for reason, overrides in cases.items():
            for over in overrides:
                with self.subTest(over=over):
                    self.write_jwks([jwk(key("a"), "kid-a", **over)])
                    self.denied(token(), reason)

    def test_crit_header_fails(self):
        for crit in (["exp"], [], "exp", None):
            with self.subTest(crit=crit):
                self.denied(token(header={"alg": "RS256", "kid": "kid-a", "crit": crit}), "crit")

    def test_malformed_tokens_fail(self):
        good = token()
        h, p, s = good.split(".")
        cases = [("", "token size"), (None, "token size"), (good.encode(), "token size"), ("x" * 20000, "token size"),
                 ("a.b", "token shape"), (good + ".AAAA", "token shape"), ("..", "base64url"),
                 (h + "." + p + "." + s + "=", "base64url"), (h + "=." + p + "." + s, "base64url"),
                 (h + "." + p + "." + s[:-2] + "+/", "base64url"), (h + ". " + p + "." + s, "base64url"),
                 (h + "." + p + "." + s + "\n", "base64url"), (h + "." + p + ".éé", "base64url"),
                 (b64u(b"not json") + "." + p + "." + s, "json"), (b64u(b"[1]") + "." + p + "." + s, "json"),
                 (b64u(b"\xff\xfe") + "." + p + "." + s, "json"), (h + "." + b64u(b'"str"') + "." + s, "json"),
                 # nesting deep enough to exhaust the parser's recursion on some builds: still a denial, not a crash (the other builds parse it and deny on the missing alg)
                 (b64u(b'{"a":' + b"[" * 4000 + b"]" * 4000 + b"}") + "." + p + "." + s, ("malformed token", "alg"))]
        for tok, reason in cases:
            with self.subTest(token=str(tok)[:30], reason=reason):
                self.denied(tok, reason)

    def test_garbage_only_ever_raises_access_denied(self):
        rng = random.Random(20261012)
        alphabet = string.ascii_letters + string.digits + "_-.= "
        good = token().split(".")
        for _ in range(300):
            if rng.random() < 0.5:
                tok = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 120)))
            else:
                parts = [list(x) for x in good]   # a real token with a few characters replaced
                for _ in range(rng.randrange(1, 4)):
                    part = rng.choice(parts)
                    part[rng.randrange(len(part))] = rng.choice(alphabet)
                tok = ".".join("".join(x) for x in parts)
            try:
                gate.verify(tok, self.cfg, NOW)
            except gate.AccessDenied:
                pass

    def test_a_token_with_no_identity_fails(self):
        self.denied(token(payload=claims(email=GONE)), "no identity")
        self.denied(token(payload=claims(email="  ", common_name="")), "no identity")
        self.denied(token(payload=claims(email=7, common_name=None)), "no identity")


@needs_crypto
class EncodeAndCompareTests(GateCase):
    """The RSA check rebuilds the single valid encoded message; hand-forged near-misses all fail."""

    def forge(self, build):
        nums = key("a").private_numbers()
        n, k = nums.public_numbers.n, 256
        signing, _ = sign_parts(key("a"), HEADER, claims())
        em = build(k, hashlib.sha256(signing.encode()).digest())
        self.assertEqual(len(em), k)
        return signing + "." + b64u(pow(int.from_bytes(em, "big"), nums.d, n).to_bytes(k, "big"))

    @staticmethod
    def classic(k, digest, prefix=None, tail=b"", ps_type=b"\x01", fill=b"\xff", ps_len=None):
        t = (gate._SHA256_PREFIX if prefix is None else prefix) + digest + tail
        ps_len = k - 3 - len(t) if ps_len is None else ps_len
        return b"\x00" + ps_type + fill * ps_len + b"\x00" + t

    def test_the_canonical_message_is_accepted(self):
        self.assertEqual(gate.verify(self.forge(self.classic), self.cfg, NOW)["email"], EMAIL)   # proves the forger itself

    def test_every_non_canonical_message_is_rejected(self):
        sha1_prefix = bytes([0x30, 0x21, 0x30, 0x09, 0x06, 0x05, 0x2b, 0x0e, 0x03, 0x02, 0x1a, 0x05, 0x00, 0x04, 0x14])
        cases = {
            "junk after the hash (Bleichenbacher-style slack)":
                lambda k, d: self.classic(k, d, tail=b"\x01\x02\x03\x04"),
            "junk after the hash, short padding":
                lambda k, d: self.classic(k, d, tail=b"\x00" * (k - 3 - 7 - 51), ps_len=7),
            "padding type 2": lambda k, d: self.classic(k, d, ps_type=b"\x02"),
            "padding not all ff": lambda k, d: self.classic(k, d, fill=b"\xfe"),
            "sha-1 DigestInfo": lambda k, d: self.classic(k, hashlib.sha1(d).digest(), prefix=sha1_prefix),
            "digest of other data": lambda k, d: self.classic(k, hashlib.sha256(b"other").digest()),
            "no 00 before DigestInfo": lambda k, d: b"\x00\x01" + b"\xff" * (k - 2 - 51) + gate._SHA256_PREFIX + d,
            "NULL parameters dropped":
                lambda k, d: self.classic(k, d, prefix=gate._SHA256_PREFIX[:15] + b"\x04\x20"),
        }
        for name, build in cases.items():
            with self.subTest(name):
                self.denied(self.forge(build), "bad signature")


@needs_crypto
class CheckRequestTests(GateCase):
    def req(self, path="/", tok="user", host=HOST, ip=PUBLIC_IP, extra=(), cfg=None, header_name="Cf-Access-Jwt-Assertion"):
        pairs = [] if host is None else [("Host", host)]
        if tok == "user":
            tok = token()
        if tok is not None:
            pairs.append((header_name, tok))
        return gate.check_request(cfg or self.cfg, "GET", path, message(pairs + list(extra)), ip, now=NOW)

    def test_a_valid_user_token_is_allowed(self):
        d = self.req()
        self.assertEqual((d.allowed, d.status, d.user), (True, 200, EMAIL))
        self.assertTrue(self.req("/deep/page?x=1", header_name="cf-ACCESS-jwt-assertion").allowed)   # header case does not matter

    def test_no_token_is_denied(self):
        d = self.req(tok=None)
        self.assertEqual((d.allowed, d.status, d.reason), (False, 403, "no token"))

    def test_a_bad_token_is_denied_with_its_reason(self):
        self.assertEqual(self.req(tok=token(payload=claims(aud="other"))).reason, "aud")
        self.assertEqual(self.req(tok="garbage").reason, "token shape")

    def test_the_token_is_read_only_from_the_header(self):
        tok = token()
        for extra, path in (([("Cookie", "CF_Authorization=" + tok)], "/"), ([("Authorization", "Bearer " + tok)], "/"),
                            ([("X-Cf-Access-Jwt-Assertion", tok)], "/"), ([], "/?Cf-Access-Jwt-Assertion=" + tok)):
            with self.subTest(extra=extra[:1], path=path[:20]):
                d = self.req(path=path, tok=None, extra=extra)
                self.assertEqual((d.allowed, d.reason), (False, "no token"))

    def test_a_repeated_token_header_is_denied(self):
        d = self.req(extra=[("Cf-Access-Jwt-Assertion", token())])
        self.assertEqual((d.allowed, d.reason), (False, "no token"))
        d = self.req(extra=[("Host", HOST)])   # a repeated Host is just as ambiguous
        self.assertEqual((d.allowed, d.reason), (False, "host"))

    def test_service_token_is_rejected_on_normal_paths_and_accepted_on_version_only_when_listed(self):
        listed = self.config(ACCESS_SERVICE_CNS="other.access, %s" % SERVICE_CN)
        for path in ("/", "/api/data", "/healthz/", "/version/x"):
            with self.subTest(path=path):
                d = self.req(path=path, tok=service_token(), cfg=listed)
                self.assertEqual((d.allowed, d.reason), (False, "service token"))
        for path in ("/version", "/healthz", "/version?verbose=1"):
            with self.subTest(path=path):
                d = self.req(path=path, tok=service_token(), cfg=listed)
                self.assertEqual((d.allowed, d.user), (True, "service:" + SERVICE_CN))
        d = self.req(path="/version", tok=service_token())   # not listed
        self.assertEqual((d.allowed, d.reason), (False, "service token"))
        d = self.req(path="/version", tok=service_token(), cfg=self.config(ACCESS_SERVICE_CNS="svc-client"))   # exact, no suffix guessing
        self.assertFalse(d.allowed)
        self.assertTrue(self.req(path="/version").allowed)   # a person may fetch it too

    def test_loopback_health_needs_no_token_and_other_clients_do(self):
        for path, host, ip in (("/healthz", "127.0.0.1:8806", "127.0.0.1"), ("/version?x=1", "localhost", "127.0.0.1"),
                               ("/healthz", "[::1]:8806", "::1"), ("/version", "127.0.0.1", "::ffff:127.0.0.1"),
                               ("/healthz", HOST, "127.0.0.9")):
            with self.subTest(path=path, ip=ip):
                d = self.req(path=path, tok=None, host=host, ip=ip)
                self.assertEqual((d.allowed, d.user), (True, None))
        for ip in (PUBLIC_IP, "10.0.0.5", "::2", "", "not-an-ip", None):
            with self.subTest(ip=ip):
                d = self.req(path="/healthz", tok=None, host="127.0.0.1", ip=ip)
                self.assertEqual((d.allowed, d.reason), (False, "no token"))

    def test_the_loopback_carve_out_is_exactly_two_routes_and_not_for_tunnelled_requests(self):
        for path in ("/", "/healthz/", "//healthz", "/HEALTHZ", "/healthz;x", "/healthz%2f", "/version.json", "/api/healthz",
                     "http://127.0.0.1/healthz"):
            with self.subTest(path=path):
                self.assertFalse(self.req(path=path, tok=None, host="127.0.0.1", ip="127.0.0.1").allowed)
        # cloudflared connects from loopback too: anything it forwarded carries proxy headers and must show a token
        for extra in ([("Cf-Ray", "x")], [("Cf-Connecting-Ip", "198.51.100.1")], [("X-Forwarded-For", "198.51.100.1")],
                      [("Forwarded", "for=198.51.100.1")], [("X-Real-Ip", "198.51.100.1")], [("Cf-Access-Jwt-Assertion", "junk")]):
            with self.subTest(extra=extra[0][0]):
                d = self.req(path="/healthz", tok=None, host="127.0.0.1", ip="127.0.0.1", extra=extra)
                self.assertFalse(d.allowed)

    def test_bad_host_is_denied_even_with_a_valid_token(self):
        for host in ("evil.example", None, "", HOST + ".evil.example", "evil." + HOST, HOST + "@evil.example",
                     "evil.example@" + HOST, HOST + ":abc", HOST + ":80:80", HOST + ":999999", "*", " ", HOST + "/x",
                     "127.0.0.1.evil.example", "[::2]"):
            with self.subTest(host=host):
                d = self.req(host=host)
                self.assertEqual((d.allowed, d.status, d.reason), (False, 403, "host"))
        for host in (HOST, HOST.upper(), HOST + ":8443", " " + HOST + " ", "localhost:8806", "127.0.0.1", "[::1]:8806"):
            with self.subTest(host=host):
                self.assertTrue(self.req(host=host).allowed)

    def test_meta_style_header_names_are_denied(self):
        # A Django META key (HTTP_CF_RAY) would hide the tunnel's Cf-Ray from the proxy test and open the loopback carve-out.
        for name in ("HTTP_CF_RAY", "http_x_forwarded_for", "Http_Host"):
            with self.subTest(name=name):
                for cfg in (self.cfg, gate.AccessConfig.from_env({})):   # enforced or not: adapters must pass real names
                    d = gate.check_request(cfg, "GET", "/version", {"Host": "127.0.0.1", name: "x"}, "127.0.0.1", now=NOW)
                    self.assertEqual((d.allowed, d.status, d.reason), (False, 403, "meta header names"))
        d = gate.check_request(self.cfg, "GET", "/version", {"Host": "127.0.0.1"}, "127.0.0.1", now=NOW)   # a plain dict works
        self.assertTrue(d.allowed)

    def test_host_is_checked_before_the_loopback_carve_out(self):
        self.assertEqual(self.req(path="/healthz", tok=None, host="evil.example", ip="127.0.0.1").reason, "host")

    def test_not_enforced_allows_everything(self):
        cfg = gate.AccessConfig.from_env({})
        self.assertFalse(cfg.enforced)
        for path, host, ip in (("/", "evil.example", PUBLIC_IP), ("/anything?x=1", None, ""), ("/healthz", HOST, None)):
            d = gate.check_request(cfg, "POST", path, message([("Host", host)] if host else [("Cf-Access-Jwt-Assertion", "garbage")]), ip)
            self.assertEqual((d.allowed, d.status), (True, 200))

    def test_enforce_flag_turns_the_gate_on_for_a_loopback_bind(self):
        cfg = self.config(BIND_HOST="127.0.0.1", ACCESS_ENFORCE="1")
        self.assertTrue(cfg.enforced)
        d = self.req(path="/", tok=None, host="127.0.0.1", ip="127.0.0.1", cfg=cfg)
        self.assertEqual((d.allowed, d.reason), (False, "no token"))
        self.assertTrue(self.req(tok=None, path="/healthz", host="127.0.0.1", ip="127.0.0.1", cfg=cfg).allowed)


class ConfigTests(unittest.TestCase):
    FULL = {"ACCESS_AUD": AUD, "ACCESS_ISSUER": ISS, "ACCESS_JWKS_FILE": "certs.json"}

    def test_defaults_are_loopback_and_not_enforced(self):
        cfg = gate.AccessConfig.from_env({})
        self.assertEqual((cfg.enforced, cfg.bind_host), (False, "127.0.0.1"))
        self.assertEqual(cfg.allowed_hosts, gate.LOOPBACK)
        self.assertEqual(gate.LOOPBACK, {"127.0.0.1", "localhost", "::1", "[::1]"})

    def test_loopback_binds_are_not_enforced_and_every_other_bind_is(self):
        for bind in ("127.0.0.1", "localhost", "::1", "[::1]", " LocalHost ", "::1 "):
            with self.subTest(bind=bind):
                self.assertFalse(gate.AccessConfig.from_env({"BIND_HOST": bind}).enforced)
        for bind in ("0.0.0.0", "", "   ", "::", "192.168.1.5", "example.com", "127.0.0.2", "127.0.0.1.evil", "0"):
            with self.subTest(bind=bind):
                self.assertTrue(gate.AccessConfig.from_env(dict(self.FULL, BIND_HOST=bind)).enforced)
        self.assertTrue(gate.AccessConfig(bind_host="0.0.0.0").enforced)   # built by hand: still enforced

    def test_enforced_with_missing_config_is_an_error(self):
        for missing in ("ACCESS_AUD", "ACCESS_ISSUER", "ACCESS_JWKS_FILE"):
            for env in (dict(self.FULL, BIND_HOST="0.0.0.0"), dict(self.FULL, ACCESS_ENFORCE="1")):
                env = {k: v for k, v in env.items() if k != missing}
                with self.subTest(missing=missing, env=sorted(env)):
                    with self.assertRaises(gate.AccessConfigError) as cm:
                        gate.AccessConfig.from_env(env)
                    self.assertIn(missing, str(cm.exception))
        with self.assertRaises(gate.AccessConfigError):
            gate.AccessConfig.from_env({"BIND_HOST": "0.0.0.0", "ACCESS_AUD": "  ", "ACCESS_ISSUER": ISS, "ACCESS_JWKS_FILE": "x"})
        self.assertTrue(gate.AccessConfig.from_env(dict(self.FULL, ACCESS_ENFORCE="1")).enforced)   # complete: fine

    def test_assert_bind_consistent(self):
        off, on = gate.AccessConfig.from_env({}), gate.AccessConfig.from_env(dict(self.FULL, BIND_HOST="0.0.0.0"))
        for host in ("0.0.0.0", "", "::", "10.0.0.5", "203.0.113.9", "example.com"):
            with self.subTest(host=host):
                with self.assertRaises(gate.AccessConfigError):
                    gate.assert_bind_consistent(off, host)
                gate.assert_bind_consistent(on, host)   # enforced: any bind is fine
        for host in ("127.0.0.1", "localhost", "::1", "[::1]", "::ffff:127.0.0.1"):
            with self.subTest(host=host):
                gate.assert_bind_consistent(off, host)

    def test_wildcard_in_allowed_hosts_is_an_error(self):
        for hosts in ("*", "a.example.test,*", "*.example.test", "a.example.test, *", "a*b"):
            with self.subTest(hosts=hosts):
                with self.assertRaises(gate.AccessConfigError):
                    gate.AccessConfig.from_env({"ALLOWED_HOSTS": hosts})

    def test_enforce_flag_accepts_only_one_zero_or_nothing(self):
        for flag in ("true", "yes", "on", "2", "1 1"):
            with self.subTest(flag=flag):
                with self.assertRaises(gate.AccessConfigError):
                    gate.AccessConfig.from_env({"ACCESS_ENFORCE": flag})
        for flag in ("", "0", None):
            self.assertFalse(gate.AccessConfig.from_env({} if flag is None else {"ACCESS_ENFORCE": flag}).enforced)
        self.assertFalse(gate.AccessConfig.from_env({"ACCESS_ENFORCE": " 0 "}).enforced)
        self.assertTrue(gate.AccessConfig.from_env(dict(self.FULL, BIND_HOST="0.0.0.0", ACCESS_ENFORCE="0")).enforced)   # 0 never relaxes a public bind

    def test_hosts_and_service_names_are_normalised(self):
        cfg = gate.AccessConfig.from_env({"ALLOWED_HOSTS": " Desk.Example.TEST ,, mentat.example.test ",
                                          "ACCESS_SERVICE_CNS": " a.access , B.access ,,"})
        self.assertEqual(cfg.allowed_hosts, gate.LOOPBACK | {"desk.example.test", "mentat.example.test"})
        self.assertEqual(cfg.service_cns, {"a.access", "B.access"})   # service names are exact, never lowercased

    def test_require_config_or_exit(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            gate.require_config_or_exit({"BIND_HOST": "0.0.0.0", "ACCESS_AUD": AUD})
        self.assertNotIn(cm.exception.code, (0, None))
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("ACCESS_ISSUER", lines[0])
        self.assertIn("ACCESS_JWKS_FILE", lines[0])
        self.assertTrue(gate.require_config_or_exit(dict(self.FULL, BIND_HOST="0.0.0.0")).enforced)
        self.assertFalse(gate.require_config_or_exit({}).enforced)


class HelperTests(unittest.TestCase):
    def test_host_ok(self):
        cfg = gate.AccessConfig.from_env({"ALLOWED_HOSTS": "desk.example.test"})
        for host in ("desk.example.test", "DESK.example.test:8080", "localhost:1", "[::1]", "[::1]:8806", "::1", "127.0.0.1:65535"):
            with self.subTest(host=host):
                self.assertTrue(gate.host_ok(host, cfg))
        for host in (None, "", "other.test", "desk.example.test:", "desk.example.test:x", "[::1", "[::1]x", "::1:8806",
                     "desk.example.test\r\nHost: evil", "desk.example.test,evil.test", "дesk.example.test"):
            with self.subTest(host=host):
                self.assertFalse(gate.host_ok(host, cfg))

    def test_audit_line_has_no_query_ip_headers_or_body(self):
        line = gate.audit_line("mentat", EMAIL, "GET", "/api/x?token=SECRET&ip=198.51.100.7#f", 200)
        self.assertNotIn("\n", line)
        rec = json.loads(line)
        self.assertEqual(sorted(rec), ["app", "method", "route", "status", "ts", "user"])
        self.assertEqual((rec["app"], rec["user"], rec["method"], rec["route"], rec["status"]),
                         ("mentat", EMAIL, "GET", "/api/x", 200))
        for leaked in ("SECRET", "token", "198.51.100.7", "?", "ip="):
            self.assertNotIn(leaked, line)
        stamp = datetime.strptime(rec["ts"], "%Y-%m-%dT%H:%M:%SZ")
        self.assertLess(abs((datetime.now(timezone.utc).replace(tzinfo=None) - stamp).total_seconds()), 120)
        self.assertEqual(json.loads(gate.audit_line("t", None, "GET", "/", 403))["user"], "-")
        self.assertEqual(json.loads(gate.audit_line("t", "", "GET", "/", 403, "aud"))["reason"], "aud")

    def test_audit_line_cannot_be_split_or_flooded_by_the_path(self):
        line = gate.audit_line("t", None, "GET", "/a\r\n{\"forged\":1}\x00" + "z" * 5000, 404)
        self.assertEqual(len(line.splitlines()), 1)
        self.assertLess(len(line), 600)
        self.assertEqual(sorted(json.loads(line)), ["app", "method", "route", "status", "ts", "user"])

    def test_security_headers(self):
        off = dict(gate.security_headers(gate.AccessConfig.from_env({})))
        on = dict(gate.security_headers(gate.AccessConfig(bind_host="0.0.0.0"), frame_options="DENY"))
        base = {"Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
                "Cross-Origin-Resource-Policy": "same-origin"}
        self.assertEqual(off, dict(base, **{"X-Frame-Options": "SAMEORIGIN"}))
        self.assertEqual(on, dict(base, **{"X-Frame-Options": "DENY", "Cache-Control": "no-store"}))

    def test_version_payload(self):
        self.assertEqual(gate.version_payload("melange", {}), {"app": "melange", "git_sha": "unknown",
                                                               "build_time": "unknown", "snapshot_id": "unknown"})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "snap.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("snap-2026-10-12\nsecond line is ignored\n")
            env = {"GIT_SHA": "abc1234", "BUILD_TIME": "2026-10-12T00:00:00Z", "SNAPSHOT_ID_FILE": path}
            self.assertEqual(gate.version_payload("terrier", env), {"app": "terrier", "git_sha": "abc1234",
                             "build_time": "2026-10-12T00:00:00Z", "snapshot_id": "snap-2026-10-12"})
            for body in (b"", b"\n", b"\xff\xfe\x00bad"):
                with open(path, "wb") as f:
                    f.write(body)
                self.assertEqual(gate.version_payload("x", env)["snapshot_id"], "unknown")
            self.assertEqual(gate.version_payload("x", {"SNAPSHOT_ID_FILE": os.path.join(tmp, "absent")})["snapshot_id"], "unknown")


class _Handler(gate.AccessMixin, BaseHTTPRequestHandler):
    access_app = "test-app"

    def log_message(self, fmt, *args):
        self.logged.append(fmt % args)


def handler(cfg, path, pairs, ip=PUBLIC_IP, command="GET"):
    """A handler without its constructor: no socket, just the attributes the mixin reads and writes."""
    h = _Handler.__new__(_Handler)
    h.access_cfg, h.headers, h.command, h.path = cfg, message(pairs), command, path
    h.server = types.SimpleNamespace(server_address=("127.0.0.1", 8806))   # the address really bound
    h.client_address, h.request_version, h.requestline = (ip, 50000), "HTTP/1.1", "%s %s HTTP/1.1" % (command, path)
    h.wfile, h.close_connection, h.logged = io.BytesIO(), False, []
    return h


@needs_crypto
class MixinTests(GateCase):
    def run_check(self, h):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok = h.access_check()
        return ok, err.getvalue()

    def test_a_denial_sends_403_with_security_headers_and_one_clean_audit_line(self):
        h = handler(self.cfg, "/secret?x=1&who=me", [("Host", HOST)])
        ok, err = self.run_check(h)
        self.assertFalse(ok)
        wire = h.wfile.getvalue()
        self.assertTrue(wire.startswith(b"HTTP/1.0 403 Forbidden\r\n"), wire[:40])
        self.assertTrue(wire.endswith(b"\r\n\r\nforbidden\n"))
        for line in (b"Referrer-Policy: no-referrer", b"X-Content-Type-Options: nosniff", b"X-Frame-Options: SAMEORIGIN",
                     b"Cross-Origin-Resource-Policy: same-origin", b"Cache-Control: no-store", b"Connection: close",
                     b"Content-Length: 10"):
            self.assertIn(line + b"\r\n", wire)
        self.assertTrue(h.close_connection)
        self.assertEqual(h.logged, [])   # http.server's own line (client IP, query string) is never written
        rec = json.loads(err)
        self.assertEqual((rec["app"], rec["user"], rec["method"], rec["route"], rec["status"], rec["reason"]),
                         ("test-app", "-", "GET", "/secret", 403, "no token"))
        for leaked in ("x=1", "who=me", PUBLIC_IP, "50000"):
            self.assertNotIn(leaked, err)

    def test_head_gets_no_body_and_a_bad_host_is_logged_as_such(self):
        h = handler(self.cfg, "/", [("Host", "evil.example")], command="HEAD")
        ok, err = self.run_check(h)
        self.assertFalse(ok)
        self.assertTrue(h.wfile.getvalue().endswith(b"\r\n\r\n"))
        self.assertEqual(json.loads(err)["reason"], "host")

    def test_loopback_health_is_allowed_and_silent(self):
        h = handler(self.cfg, "/healthz", [("Host", "127.0.0.1:8806")], ip="127.0.0.1")
        ok, err = self.run_check(h)
        self.assertTrue(ok)
        self.assertEqual((h.wfile.getvalue(), err, h.access_user), (b"", "", None))

    def test_a_valid_token_is_allowed_and_the_user_is_kept_for_the_app_to_log(self):
        real = time.time()   # access_check reads the real clock, so this one test signs against it
        self.write_jwks([jwk(key("a"), "kid-a")], mtime=real - 60)
        tok = token(payload=claims(exp=real + 3600, nbf=real - 10))
        h = handler(self.cfg, "/page", [("Host", HOST), ("Cf-Access-Jwt-Assertion", tok)])
        ok, err = self.run_check(h)
        self.assertTrue(ok)
        self.assertEqual((h.wfile.getvalue(), err, h.access_user), (b"", "", EMAIL))
        h.send_response_only(200)
        h.send_security_headers()
        h.end_headers()
        self.assertIn(b"Cache-Control: no-store", h.wfile.getvalue())

    def test_a_handler_with_no_config_serves_nothing(self):
        h = handler(None, "/", [("Host", HOST)])
        with self.assertRaises(gate.AccessConfigError):
            h.access_check()
        self.assertEqual(h.wfile.getvalue(), b"")

    def test_not_enforced_the_mixin_is_transparent_and_adds_no_cache_header(self):
        h = handler(gate.AccessConfig.from_env({}), "/anything", [("Host", "whatever")])
        ok, err = self.run_check(h)
        self.assertTrue(ok)
        self.assertEqual((h.wfile.getvalue(), err), (b"", ""))
        h.send_response_only(200)
        h.send_security_headers()
        h.end_headers()
        self.assertNotIn(b"Cache-Control", h.wfile.getvalue())
        self.assertIn(b"Referrer-Policy: no-referrer", h.wfile.getvalue())


class _Recorder(gate.AccessMixin, BaseHTTPRequestHandler):
    """An app that answers 200 to GET, HEAD, POST and FOO, and records every call it gets."""
    access_app = "test-app"

    def log_message(self, fmt, *args):
        pass

    def hit(self):
        self.hits.append(self.command)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_HEAD = do_POST = do_FOO = hit


@contextlib.contextmanager
def serving(cfg, bind="127.0.0.1"):
    """A real server in a thread; yields (port, hits).  Exit joins the handler threads."""
    hits = []
    srv = ThreadingHTTPServer((bind, 0), type("H", (_Recorder,), {"access_cfg": cfg, "hits": hits}))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv.server_address[1], hits
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(10)


def fetch(port, method, host=HOST):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, "/page", headers={"Host": host})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


@needs_crypto
class ServerTests(GateCase):
    """The mixin on real sockets: parse_request gates every method, the real bind is checked."""

    def test_every_method_is_gated_before_any_app_method_runs(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), serving(self.cfg) as (port, hits):
            got = {m: fetch(port, m) for m in ("GET", "HEAD", "POST", "FOO")}
        self.assertEqual(hits, [])   # no do_* ever ran
        for method, (status, body) in got.items():
            with self.subTest(method):
                self.assertEqual(status, 403)
                self.assertEqual(body, b"" if method == "HEAD" else b"forbidden\n")
        lines = [json.loads(x) for x in err.getvalue().splitlines()]
        self.assertEqual(sorted((r["method"], r["reason"]) for r in lines), sorted((m, "no token") for m in got))

    def test_a_valid_token_reaches_every_method_exactly_once(self):
        real = time.time()
        self.write_jwks([jwk(key("a"), "kid-a")], mtime=real - 60)
        tok = token(payload=claims(exp=real + 3600, nbf=real - 10))
        with serving(self.cfg) as (port, hits):
            for method in ("GET", "HEAD", "POST", "FOO"):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                conn.request(method, "/page", headers={"Host": HOST, "Cf-Access-Jwt-Assertion": tok})
                self.assertEqual(conn.getresponse().status, 200)
                conn.close()
        self.assertEqual(hits, ["GET", "HEAD", "POST", "FOO"])

    def test_a_request_that_fails_to_parse_gets_one_response_and_no_app_call(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), serving(self.cfg) as (port, hits):
            with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
                s.sendall(b"GET /page HTTP/1.1\r\nHost: " + HOST.encode() + b"\r\n" + b"X-A: b\r\n" * 101 + b"\r\n")   # 431
                data = b""
                while chunk := s.recv(4096):
                    data += chunk
        self.assertTrue(data.startswith(b"HTTP/1.0 431 "), data[:40])
        self.assertEqual((data.count(b"HTTP/1."), hits), (1, []))   # http.server's error reply only: not answered twice
        self.assertEqual(err.getvalue(), "")   # and it never reached the gate

    def test_an_unenforced_gate_on_a_public_bind_fails_closed(self):
        off = gate.AccessConfig.from_env({})   # BIND_HOST unset: looks loopback, but the app binds 0.0.0.0 by its own flags
        self.assertFalse(off.enforced)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), serving(off, bind="0.0.0.0") as (port, hits):
            status, _ = fetch(port, "GET", host="127.0.0.1")
        self.assertEqual((status, hits), (403, []))
        self.assertEqual(json.loads(err.getvalue())["reason"], "unenforced public bind")
        with serving(off, bind="127.0.0.1") as (port, hits):   # the same config on a real loopback bind is transparent
            self.assertEqual(fetch(port, "GET", host="127.0.0.1")[0], 200)
        self.assertEqual(hits, ["GET"])


if __name__ == "__main__":
    unittest.main()
