"""Collect public 2026 campaign-site pages with reproducible raw storage."""

import argparse
import codecs
import csv
import datetime
import gzip
import hashlib
from html.parser import HTMLParser
import http.client
import ipaddress
import json
import os
import random
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import ROOT


USER_AGENT = (
    "MelangeCampaignSiteCollector/1.0 "
    "(research; contact goff0070@umn.edu)"
)
GLASSBOX_EMITTER = r"C:\Users\pgoff\.claude\glassbox\emit.ps1"
GLASSBOX_RUN_ID = "c1-2026-collection"
GLASSBOX_SOURCE = "campaign-sites-2026"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
CRAWL_DELAY_CAP_S = 30.0
EXPECTED_ROSTER_COLUMNS = [
    "state",
    "district",
    "candidate_name",
    "party",
    "incumbent_bioguide",
    "url",
    "official_url",
    "notes",
]

MANIFEST_KEYS = (
    "slug",
    "state",
    "district",
    "candidate_name",
    "party",
    "incumbent_bioguide",
    "run_utc",
    "status",
    "error",
    "pages",
    "platform_points",
    "bio_text",
    "page_errors",
    "run_log_error",
)

BIO_LINK_RE = re.compile(r"about|meet|bio", re.IGNORECASE)
ISSUES_LINK_RE = re.compile(
    r"issue|priorit|position|where\s+i\s+stand|platform", re.IGNORECASE
)
LOGIN_PATH_RE = re.compile(r"login|signin|auth", re.IGNORECASE)


class RobotsDisallowed(Exception):
    """Raised when robots.txt disallows a requested page."""


class UnverifiedTarget(Exception):
    """Raised before a fetch when a target is not structurally public."""


class _AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a" or self._href is not None:
            return
        attributes = dict(attrs)
        self._href = attributes.get("href")
        self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, " ".join(self._text)))
            self._href = None
            self._text = []


class _TextPlatformParser(HTMLParser):
    HEADING_TAGS = {"h1", "h2", "h3", "b", "strong"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text_chunks = []
        self.platform_points = []
        self._skip_depth = 0
        self._heading_tag = None
        self._heading_chunks = []
        self._current_heading = None
        self._section_chunks = []
        self._in_list_item = False
        self._list_chunks = []

    @staticmethod
    def _clean(parts):
        return re.sub(r"\s+", " ", " ".join(parts)).strip()

    def _flush_section(self):
        text = self._clean(self._section_chunks)
        if self._current_heading is not None and text:
            self.platform_points.append(
                {"heading": self._current_heading, "text": text}
            )
        self._section_chunks = []

    def handle_starttag(self, tag, attrs):
        del attrs
        tag = tag.lower()
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "li" and not self._in_list_item:
            self._flush_section()
            self._in_list_item = True
            self._list_chunks = []
        elif (
            tag in self.HEADING_TAGS
            and self._heading_tag is None
            and not self._in_list_item
        ):
            self._flush_section()
            self._heading_tag = tag
            self._heading_chunks = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style"}:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "li" and self._in_list_item:
            text = self._clean(self._list_chunks)
            if text:
                self.platform_points.append(
                    {"heading": self._current_heading or "", "text": text}
                )
            self._in_list_item = False
            self._list_chunks = []
        elif tag == self._heading_tag:
            heading = self._clean(self._heading_chunks)
            if heading:
                self._current_heading = heading
            self._heading_tag = None
            self._heading_chunks = []

    def handle_data(self, data):
        if self._skip_depth:
            return
        cleaned = re.sub(r"\s+", " ", data).strip()
        if not cleaned:
            return
        self.text_chunks.append(cleaned)
        if self._in_list_item:
            self._list_chunks.append(cleaned)
        elif self._heading_tag is not None:
            self._heading_chunks.append(cleaned)
        else:
            self._section_chunks.append(cleaned)

    def finish(self):
        self._flush_section()
        return self._clean(self.text_chunks), self.platform_points


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def candidate_slug(state, district, candidate_name):
    """Return the readable candidate slug with its collision-resistant suffix.

    The suffix hashes state|district|candidate_name only, so fixing a roster URL never orphans a
    candidate's manifest or raw directory.
    """
    name = (candidate_name or "").strip()
    if "," in name:
        last_part, first_part = name.split(",", 1)
        last_tokens = re.findall(r"[A-Za-z0-9]+", last_part)
        first_tokens = re.findall(r"[A-Za-z0-9]+", first_part)
        last_name = "-".join(last_tokens)
        first_name = first_tokens[0] if first_tokens else ""
    else:
        tokens = re.findall(r"[A-Za-z0-9]+", name)
        first_name = tokens[0] if tokens else ""
        last_name = tokens[-1] if len(tokens) > 1 else ""
    value = "{}-{}-{}-{}".format(
        state or "", district or "", last_name, first_name
    ).lower()
    current_slug = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    identity = "{}|{}|{}".format(state or "", district or "", candidate_name or "")
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:6]
    return current_slug + "-" + suffix


def classify_link_role(link_text):
    text = link_text or ""
    if BIO_LINK_RE.search(text):
        return "bio"
    if ISSUES_LINK_RE.search(text):
        return "issues"
    return "other"


def extract_links(raw_html, base_url, charset="utf-8"):
    parser = _AnchorParser()
    parser.feed(raw_html.decode(charset, errors="replace"))
    parser.close()
    links = []
    seen = set()
    for href, text in parser.anchors:
        absolute = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        normalized = urllib.parse.urlunparse(parsed._replace(fragment=""))
        if normalized in seen:
            continue
        seen.add(normalized)
        links.append((normalized, classify_link_role(text)))
    return links


def extract_text_and_platform_points(
    raw_html, include_platform_points=False, charset="utf-8"
):
    parser = _TextPlatformParser()
    parser.feed(raw_html.decode(charset, errors="replace"))
    parser.close()
    text, points = parser.finish()
    return text, points if include_platform_points else []


def _charset_from_content_type(content_type):
    match = re.search(
        r"charset\s*=\s*[\"']?([^\s;\"']+)", content_type or "", re.IGNORECASE
    )
    return match.group(1) if match else "utf-8"


def _safe_charset(name):
    """Return a canonical codec name that can decode text, else utf-8."""
    try:
        # A non-empty decode also rejects bytes-to-bytes codecs such as "hex" that lookup accepts.
        b"x".decode(name, "replace")
        return codecs.lookup(name).name
    except (LookupError, TypeError, UnicodeError):
        return "utf-8"


def _effective_address(address):
    """Judge an IPv4 carried inside an IPv4-mapped or 6to4 IPv6 address as that IPv4."""
    return (
        getattr(address, "ipv4_mapped", None)
        or getattr(address, "sixtofour", None)
        or address
    )


def _assert_public_resolution(url):
    """Refuse a URL whose host is, or resolves to, a non-public address."""
    reason = private_target_reason(url)
    if reason:
        raise UnverifiedTarget(reason)
    parsed = urllib.parse.urlparse(url)
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        answers = socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise urllib.error.URLError(exc)
    for answer in answers:
        address = ipaddress.ip_address(answer[4][0].split("%")[0])
        if not _effective_address(address).is_global:
            raise UnverifiedTarget(
                "target {} resolves to non-public address {}".format(
                    parsed.hostname, address
                )
            )


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_public_resolution(newurl)  # a public page can still redirect inward
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def default_fetch(url, timeout):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    # ponytail: DNS can answer differently when urllib connects after this check (rebinding TOCTOU);
    # the upgrade is a pinned-IP connection.
    _assert_public_resolution(url)
    opener = urllib.request.build_opener(_PublicRedirectHandler)
    with opener.open(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "")
        content_length = response.headers.get("Content-Length")
        raw = response.read(MAX_RESPONSE_BYTES)
        try:
            declared_length = int(content_length) if content_length is not None else None
        except (TypeError, ValueError):
            declared_length = None
        truncated = (
            declared_length > MAX_RESPONSE_BYTES
            if declared_length is not None
            else len(raw) == MAX_RESPONSE_BYTES
        )
        return (
            response.getcode(),
            raw,
            response.geturl(),
            _charset_from_content_type(content_type),
            truncated,
        )


def _normalize_fetch_result(result, requested_url):
    if len(result) != 5:
        raise ValueError("fetch_fn must return 5 values")
    status, raw, final_url, charset, truncated = result
    return {
        "requested_url": requested_url,
        "http_status": status,
        "raw": raw,
        "final_url": final_url or requested_url,
        "charset": _safe_charset(charset or "utf-8"),
        "truncated": bool(truncated),
    }


def private_target_reason(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return "target scheme must be http or https"
    host = (parsed.hostname or "").lower()
    if not host:
        return "target host cannot be determined"
    if host == "localhost" or host.endswith(".localhost"):
        return "target host localhost is not public"
    # Local parsing only (no DNS): ipaddress for canonical literals, inet_aton for the
    # integer / hex / short spellings (2130706433, 0x7f000001, 127.1) that resolvers accept.
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            address = ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return None  # a hostname; default_fetch resolves and re-checks it
    effective = _effective_address(address)
    if effective.is_global:
        return None
    kind = (
        "loopback" if effective.is_loopback
        else "link-local" if effective.is_link_local
        else "private" if effective.is_private
        else "reserved"
    )
    return "target non-public address {} is {}".format(address, kind)


def assert_output_path(out_dir, path):
    real_out_dir = os.path.realpath(os.fspath(out_dir))
    real_path = os.path.realpath(os.fspath(path))
    # A bare startswith() let a sibling such as <out_dir>2 pass; compare whole path components instead.
    if os.path.commonpath([real_out_dir, real_path]) != real_out_dir:
        raise ValueError("output path escapes out-dir: {}".format(path))


def _summary_text(counts):
    return " ".join(
        "{}={}".format(key, counts[key])
        for key in (
            "processed",
            "collected",
            "skipped_no_url",
            "skipped_robots",
            "skipped_unverified",
            "errors",
        )
    )


def emit_glassbox(kind, status, counts):
    """Emit a Glassbox event; emitter failures never affect collection."""
    try:
        subprocess.run(
            [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                GLASSBOX_EMITTER,
                "-Lane",
                "claude",
                "-Kind",
                kind,
                "-RunId",
                GLASSBOX_RUN_ID,
                "-Source",
                GLASSBOX_SOURCE,
                "-Status",
                status,
                "-Summary",
                _summary_text(counts),
                "-Quiet",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except Exception:
        pass


class CampaignSiteCollector:
    def __init__(
        self,
        out_dir,
        fetch_fn=None,
        robot_parser_factory=None,
        delay_min=2.0,
        delay_max=4.0,
        timeout=20,
        max_pages=6,
    ):
        self.out_dir = os.fspath(out_dir)
        self._uses_default_fetch = fetch_fn is None or fetch_fn is default_fetch
        self.fetch_fn = fetch_fn or default_fetch
        self.robot_parser_factory = robot_parser_factory
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.timeout = timeout
        self.max_pages = max_pages
        self._robot_cache = {}

    def effective_delay_bounds(self, crawl_delay=None):
        if not self._uses_default_fetch:
            return self.delay_min, self.delay_max
        floor = max(2.0, float(crawl_delay or 0), self.delay_min)
        return floor, max(floor, self.delay_max)

    def _transport(self, url, crawl_delay=None):
        for attempt in range(2):
            delay_bounds = self.effective_delay_bounds(crawl_delay)
            time.sleep(random.uniform(*delay_bounds))
            try:
                return _normalize_fetch_result(
                    self.fetch_fn(url, self.timeout), requested_url=url
                )
            except urllib.error.HTTPError:
                raise  # an HTTP answer is final; HTTPError subclasses URLError
            except (urllib.error.URLError, TimeoutError, http.client.IncompleteRead):
                if attempt == 1:
                    raise
                time.sleep(5)
        raise AssertionError("unreachable")

    def _default_robot_parser(self, robots_url):
        parser = urllib.robotparser.RobotFileParser(robots_url)
        try:
            response = self._transport(robots_url)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                parser.disallow_all = True
                return parser
            if 400 <= exc.code < 500:
                parser.allow_all = True
                return parser
            raise
        status = response["http_status"]
        raw = response["raw"]
        if status in {401, 403}:
            parser.disallow_all = True
        elif 400 <= status < 500:
            parser.allow_all = True
        else:
            parser.parse(raw.decode("utf-8", errors="replace").splitlines())
        return parser

    def _robot_parser_for(self, url):
        parsed = urllib.parse.urlparse(url)
        reason = private_target_reason(url)
        if reason:
            raise UnverifiedTarget(reason)
        cache_key = (parsed.scheme.lower(), parsed.hostname.lower())
        parser = self._robot_cache.get(cache_key)
        if parser is None:
            robots_url = urllib.parse.urlunparse(
                (parsed.scheme, parsed.netloc, "/robots.txt", "", "", "")
            )
            if self.robot_parser_factory is None:
                parser = self._default_robot_parser(robots_url)
            else:
                parser = self.robot_parser_factory(robots_url)
            self._robot_cache[cache_key] = parser
        return parser

    def _robots_allowed(self, url):
        parser = self._robot_parser_for(url)
        return parser.can_fetch(USER_AGENT, url)

    @staticmethod
    def _crawl_delay(parser):
        crawl_delay_fn = getattr(parser, "crawl_delay", None)
        if crawl_delay_fn is None:
            return None
        return crawl_delay_fn(USER_AGENT)

    def _fetch_checked(self, url):
        reason = private_target_reason(url)
        if reason:
            raise UnverifiedTarget(reason)
        if not self._robots_allowed(url):
            raise RobotsDisallowed("robots.txt disallows " + url)
        parser = self._robot_parser_for(url)
        delay = self._crawl_delay(parser)
        if isinstance(delay, (int, float)) and delay > CRAWL_DELAY_CAP_S:
            raise RobotsDisallowed(
                "crawl-delay {}s exceeds {}s cap: {}".format(
                    delay, CRAWL_DELAY_CAP_S, url
                )
            )
        response = self._transport(url, crawl_delay=delay)
        final_url = response["final_url"]
        disallowed_redirect = False
        # A SAME-host redirect can still land on a Disallowed PATH (robots allows / but disallows
        # /members/; /issues 301s to /members/issues).  Comparing hosts only let that page be
        # stored and text-extracted without any robots check on the path actually fetched, so
        # re-check whenever the final URL differs from the one robots was consulted about.
        if final_url != url:
            try:
                disallowed_redirect = not self._robots_allowed(final_url)
            except (UnverifiedTarget, RobotsDisallowed, urllib.error.URLError, OSError):
                # A robots lookup failure for the redirect target must not escape as a URLError
                # that _fetch_with_https_upgrade would answer with an http re-fetch.
                disallowed_redirect = True
        response["disallowed_redirect"] = disallowed_redirect
        return response

    def _fetch_with_https_upgrade(self, url):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme.lower() == "http":
            upgraded = urllib.parse.urlunparse(parsed._replace(scheme="https"))
            try:
                return self._fetch_checked(upgraded)
            except (RobotsDisallowed, UnverifiedTarget, urllib.error.HTTPError):
                raise
            except (urllib.error.URLError, OSError) as exc:
                # Downgrade only after a plain connection failure, never after an HTTP answer or a
                # certificate-verification failure.
                reason = getattr(exc, "reason", exc)
                if isinstance(reason, ssl.SSLCertVerificationError) or isinstance(
                    exc, ssl.SSLCertVerificationError
                ):
                    raise
                return self._fetch_checked(url)
        return self._fetch_checked(url)

    def _store_raw(self, slug, raw_html):
        digest = hashlib.sha256(raw_html).hexdigest()
        raw_dir = os.path.join(self.out_dir, "raw", slug)
        assert_output_path(self.out_dir, raw_dir)
        raw_path = os.path.join(raw_dir, digest[:16] + ".html.gz")
        assert_output_path(self.out_dir, raw_path)
        temp_path = raw_path + ".tmp"
        assert_output_path(self.out_dir, temp_path)
        os.makedirs(raw_dir, exist_ok=True)
        if os.path.exists(raw_path):
            return digest, False
        with gzip.open(temp_path, "wb") as handle:
            handle.write(raw_html)
        os.replace(temp_path, raw_path)
        return digest, True

    @staticmethod
    def _target_for_row(row):
        direct_url = (row.get("url") or "").strip()
        official_url = (row.get("official_url") or "").strip()
        bioguide = (row.get("incumbent_bioguide") or "").strip()
        if direct_url:
            return direct_url, False
        if bioguide and official_url:
            official_host = (
                urllib.parse.urlparse(official_url).hostname or ""
            ).lower()
            housegov = official_host.endswith(("house.gov", "senate.gov"))
            return official_url, housegov
        return None, False

    @staticmethod
    def row_has_fetchable_url(row):
        target, unused_housegov = CampaignSiteCollector._target_for_row(row)
        del unused_housegov
        return target is not None

    @staticmethod
    def _base_manifest(row, run_utc):
        return {
            "slug": candidate_slug(
                row.get("state", ""),
                row.get("district", ""),
                row.get("candidate_name", ""),
            ),
            "state": row.get("state", ""),
            "district": row.get("district", ""),
            "candidate_name": row.get("candidate_name", ""),
            "party": row.get("party", ""),
            "incumbent_bioguide": row.get("incumbent_bioguide", ""),
            "run_utc": run_utc,
            "status": "error",
            "error": None,
            "pages": [],
            "platform_points": [],
            "bio_text": None,
            "page_errors": [],
            "run_log_error": None,
        }

    def _write_manifest(self, manifest):
        manifest_dir = os.path.join(self.out_dir, "manifests")
        assert_output_path(self.out_dir, manifest_dir)
        path = os.path.join(manifest_dir, manifest["slug"] + ".json")
        assert_output_path(self.out_dir, path)
        temp_path = path + ".tmp"
        assert_output_path(self.out_dir, temp_path)
        os.makedirs(manifest_dir, exist_ok=True)
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_path, path)

    def _fetch_store_extract(self, manifest, requested_url, role, housegov):
        response = self._fetch_with_https_upgrade(requested_url)
        fetched_utc = utc_now().isoformat()
        raw_html = response["raw"]
        disallowed_redirect = response["disallowed_redirect"]

        # Raw preservation intentionally precedes every extraction operation.
        if disallowed_redirect:
            digest = hashlib.sha256(raw_html).hexdigest()
            stored = False
        else:
            digest, stored = self._store_raw(manifest["slug"], raw_html)
        final_url = response["final_url"]
        entry = {
            "url": response["requested_url"],
            "final_url": final_url,
            "fetched_utc": fetched_utc,
            "http_status": response["http_status"],
            "sha256_raw": digest,
            "role": role,
            "stored": stored,
            "housegov": housegov,
            "disallowed_redirect": disallowed_redirect,
            "suspected_login": bool(
                LOGIN_PATH_RE.search(urllib.parse.urlparse(final_url).path)
            ),
            "truncated": response["truncated"],
        }
        manifest["pages"].append(entry)

        if disallowed_redirect:
            return final_url, b"", response["charset"], True
        if role in ("bio", "issues"):
            text, points = extract_text_and_platform_points(
                raw_html,
                include_platform_points=(role == "issues"),
                charset=response["charset"],
            )
            if role == "bio":
                if manifest["bio_text"]:
                    manifest["bio_text"] += "\n\n" + text
                else:
                    manifest["bio_text"] = text
            else:
                manifest["platform_points"].extend(points)
        return final_url, raw_html, response["charset"], False

    def _collect(self, manifest, target_url, housegov):
        home_url, home_html, home_charset, disallowed_redirect = self._fetch_store_extract(
            manifest, target_url, "home", housegov
        )
        if disallowed_redirect:
            manifest["status"] = "skipped_robots"
            manifest["error"] = "home page redirects to a disallowed URL: {}".format(
                home_url
            )
            return
        if self.max_pages <= 1:
            manifest["status"] = "collected"
            return

        home_host = urllib.parse.urlparse(home_url).hostname.lower()
        discovered = extract_links(home_html, home_url, charset=home_charset)
        prioritized = sorted(
            enumerate(discovered),
            key=lambda item: (
                {"bio": 0, "issues": 1, "other": 2}[item[1][1]],
                item[0],
            ),
        )
        seen = {urllib.parse.urlunparse(urllib.parse.urlparse(home_url)._replace(fragment=""))}
        for unused_index, (page_url, role) in prioritized:
            del unused_index
            if len(manifest["pages"]) + len(manifest["page_errors"]) >= self.max_pages:
                break
            parsed = urllib.parse.urlparse(page_url)
            if parsed.hostname.lower() != home_host or page_url in seen:
                continue
            if parsed.username is not None or parsed.password is not None:
                continue
            seen.add(page_url)
            try:
                self._fetch_store_extract(manifest, page_url, role, housegov)
            except Exception as exc:  # one bad subpage must not sink the candidate
                manifest["page_errors"].append(
                    {
                        "url": page_url,
                        "role": role,
                        "error": str(exc) or type(exc).__name__,
                    }
                )
        manifest["status"] = "collected"

    def process_row(self, row, run_utc):
        manifest = self._base_manifest(row, run_utc)
        try:
            target_url, housegov = self._target_for_row(row)
            if not (row.get("candidate_name") or "").strip():
                manifest["status"] = "skipped_unverified"
            elif target_url is None:
                manifest["status"] = "skipped_no_url"
            else:
                reason = private_target_reason(target_url)
                if reason:
                    manifest["status"] = "skipped_unverified"
                    manifest["error"] = reason
                else:
                    self._collect(manifest, target_url, housegov)
        except RobotsDisallowed as exc:
            manifest["status"] = "skipped_robots"
            manifest["error"] = str(exc)
        except UnverifiedTarget as exc:
            manifest["status"] = "skipped_unverified"
            manifest["error"] = str(exc)
        except Exception as exc:
            manifest["status"] = "error"
            manifest["error"] = str(exc)

        try:
            self._write_manifest(manifest)
        except Exception as exc:
            manifest["status"] = "error"
            manifest["error"] = "manifest write failed: {}".format(exc)
        return manifest


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--roster",
        default=os.path.join(os.fspath(ROOT), "source_data", "campaign_sites_2026", "roster.csv"),
    )
    parser.add_argument(
        "--out-dir",
        default=os.path.join(os.fspath(ROOT), "source_data", "campaign_sites_2026"),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke", type=int, metavar="N")
    mode.add_argument("--sweep", action="store_true")
    parser.add_argument("--delay-min", type=float, default=2.0)
    parser.add_argument("--delay-max", type=float, default=4.0)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--max-pages", type=int, default=6)
    return parser


def _empty_summary():
    return {
        "processed": 0,
        "collected": 0,
        "skipped_no_url": 0,
        "skipped_robots": 0,
        "skipped_unverified": 0,
        "errors": 0,
    }


def _atomic_append_json_line(out_dir, path, value):
    assert_output_path(out_dir, path)
    # Read-all + rewrite-all per row is O(n^2) bytes: one sweep row carries its full bio_text and
    # platform_points, so a ~900-candidate roster rewrote a tens-of-MB log 900 times on a
    # OneDrive-synced tree.  This switches to a single buffered append of one complete line,
    # flushed and fsynced before the handle closes, for linear cost instead of quadratic -- NOT
    # for the same crash property as before: os.replace() was already atomic, so a crash mid-write
    # left the file at its previous complete content with no torn line ever possible; an append
    # CAN leave a torn tail line if a crash lands mid-write.  Harmless here because run_log is a
    # fresh timestamped file per run (runs_dir/<compact_utc>.jsonl) with no reader in this repo,
    # but a caller of this helper for any file a reader depends on must tolerate a torn final line.
    # No explicit newline= is passed, so text-mode translation keeps writing CRLF on Windows here,
    # exactly as the prior "w"-mode open did.
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _set_candidate_write_error(manifest, label, exc):
    message = "{}: {}".format(label, exc)
    if manifest.get("error"):
        message = manifest["error"] + "; " + message
    manifest["status"] = "error"
    manifest["error"] = message


def main(argv=None, fetch_fn=None, robot_parser_factory=None):
    args = build_parser().parse_args(argv)
    if args.sweep:
        approval_marker = os.path.join(args.out_dir, "SWEEP_APPROVED")
        if not os.path.isfile(approval_marker):
            print(
                "--sweep requires coordinator approval marker: {}".format(
                    approval_marker
                ),
                file=sys.stderr,
            )
            return 2
    try:
        with open(args.roster, newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            found_columns = reader.fieldnames or []
            if found_columns != EXPECTED_ROSTER_COLUMNS:
                print(
                    "Roster header mismatch: expected={} found={}".format(
                        json.dumps(EXPECTED_ROSTER_COLUMNS),
                        json.dumps(found_columns),
                    ),
                    file=sys.stderr,
                )
                return 2
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        print("Unable to read roster: {}".format(exc), file=sys.stderr)
        return 1

    summary = _empty_summary()
    emit_glassbox("task.started", "running", summary)

    run_time = utc_now()
    run_utc = run_time.isoformat()
    compact_utc = run_time.strftime("%Y%m%dT%H%M%SZ")
    runs_dir = os.path.join(args.out_dir, "runs")
    run_log_setup_error = None
    try:
        assert_output_path(args.out_dir, runs_dir)
        os.makedirs(runs_dir, exist_ok=True)
        run_log = os.path.join(runs_dir, compact_utc + ".jsonl")
        assert_output_path(args.out_dir, run_log)
    except Exception as exc:
        run_log = None
        run_log_setup_error = "run log setup failed: {}".format(exc)
        print(run_log_setup_error, file=sys.stderr)

    collector = CampaignSiteCollector(
        out_dir=args.out_dir,
        fetch_fn=fetch_fn,
        robot_parser_factory=robot_parser_factory,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        timeout=args.timeout,
        max_pages=args.max_pages,
    )

    run_log_failed = False
    smoke_used = 0
    for row in rows:
        has_fetchable_url = collector.row_has_fetchable_url(row)
        if args.smoke is not None and has_fetchable_url and smoke_used >= args.smoke:
            break
        if has_fetchable_url:
            smoke_used += 1

        manifest = collector.process_row(row, run_utc)
        run_log_error = run_log_setup_error
        if run_log_error is None and run_log is not None:
            try:
                _atomic_append_json_line(args.out_dir, run_log, manifest)
            except Exception as exc:
                run_log_error = "run log write failed: {}".format(exc)
        if run_log_error is not None:
            # The collection itself succeeded: keep its status and record the log fault apart.
            manifest["run_log_error"] = run_log_error
            run_log_failed = True
            try:
                collector._write_manifest(manifest)
            except Exception as exc:
                _set_candidate_write_error(manifest, "manifest write failed", exc)
                print(manifest["error"], file=sys.stderr)

        summary["processed"] += 1
        if manifest["status"] == "error":
            summary["errors"] += 1
        else:
            summary[manifest["status"]] += 1

        if args.smoke is not None and has_fetchable_url and smoke_used >= args.smoke:
            break

    if run_log is not None:
        try:
            _atomic_append_json_line(args.out_dir, run_log, summary)
        except Exception as exc:
            run_log_failed = True
            print("run log summary write failed: {}".format(exc), file=sys.stderr)
    emit_glassbox("task.completed", "completed", summary)
    print(json.dumps(summary, sort_keys=True))
    return 1 if summary["errors"] or run_log_failed else 0


if __name__ == "__main__":
    sys.exit(main())
