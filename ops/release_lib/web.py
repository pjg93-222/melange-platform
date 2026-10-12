"""HTTP: the Netlify draft -> verify -> publish flow (stdlib urllib) and the small GET helpers used by status.

The token travels only in an Authorization header. A 4xx answer to a file upload is final ("REJECTED"):
the release never splits or strips a file to get past a size limit.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .core import ReleaseError, Secret, atomic_write, sha256_file

API = "https://api.netlify.com/api/v1"
RETRY_STATUS = (429, 500, 502, 503, 504)


def _sha1_file(path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class Rejected(ReleaseError):
    def __init__(self, message: str):
        super().__init__("REJECTED", message)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None                                  # a 3xx surfaces as an HTTPError: nothing is ever re-sent to another URL


# urllib copies request headers (Authorization, CF-Access-Client-Secret) onto a redirected request, to whatever host the
# Location names. Every call that carries a credential goes through this opener, which has no redirect handler.
_NO_REDIRECT = urllib.request.build_opener(_NoRedirect)


def served_release_id(body: bytes):
    """release_id of a /version.json body; None for anything that is not a JSON object with one (an HTML error page)."""
    try:
        value = json.loads(body).get("release_id")
    except (ValueError, AttributeError):
        return None
    return value


def _safe_url(url: str) -> str:
    host = urllib.parse.urlsplit(url)
    if host.scheme != "https" and not (host.scheme == "http" and host.hostname in ("127.0.0.1", "localhost")):
        raise ReleaseError("INSECURE_URL", f"{host.scheme}://{host.hostname} is not allowed; https only")
    return url


def http_get(url: str, headers=None, timeout=60, sleep=time.sleep, retries=3) -> tuple[int, bytes]:
    """(status, body). Retries 429/5xx and transport errors a bounded number of times."""
    request = urllib.request.Request(_safe_url(url), headers=headers or {})
    opener = _NO_REDIRECT if headers else urllib.request.build_opener()          # headers mean a credential: never follow a redirect
    for attempt in range(retries + 1):
        try:
            with opener.open(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            if error.code in RETRY_STATUS and attempt < retries:
                sleep(min(2 ** attempt, 30))
                continue
            return error.code, error.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            if attempt >= retries:
                raise ReleaseError("NETWORK", f"cannot reach {urllib.parse.urlsplit(url).hostname}")
            sleep(min(2 ** attempt, 30))
    raise AssertionError("unreachable")


def sha256_url(url: str, timeout=900) -> str:
    digest = hashlib.sha256()
    with urllib.request.urlopen(urllib.request.Request(_safe_url(url)), timeout=timeout) as response:
        for block in iter(lambda: response.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class Netlify:
    def __init__(self, token: Secret, site_id: str, api: str = API, sleep=time.sleep, retries=5, timeout=900, verify_attempts=5):
        self.token, self.site, self.api, self.sleep, self.retries, self.timeout = token, site_id, api.rstrip("/"), sleep, retries, timeout
        self.verify_attempts = verify_attempts

    def _call(self, method, path, body=None, *, file: Path | None = None, upload=False):
        """JSON in/out, or a streamed file body for uploads. Backoff on 429/5xx; 4xx on an upload is REJECTED."""
        url, last = _safe_url(self.api + path), None
        for attempt in range(self.retries + 1):
            headers = {"Authorization": "Bearer " + self.token.reveal(), "User-Agent": "melange-release"}
            data = None
            if file is not None:
                headers.update({"Content-Type": "application/octet-stream", "Content-Length": str(file.stat().st_size)})
                data = open(file, "rb")
            elif body is not None:
                data, headers["Content-Type"] = json.dumps(body).encode("utf-8"), "application/json"
            try:
                with _NO_REDIRECT.open(urllib.request.Request(url, data=data, headers=headers, method=method),
                                       timeout=self.timeout) as response:
                    raw = response.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as error:
                last = error.code
                if error.code in RETRY_STATUS and attempt < self.retries:
                    wait = error.headers.get("Retry-After")
                    self.sleep(min(float(wait) if wait and wait.isdigit() else 2 ** attempt, 60))
                    continue
                if upload and error.code != 429 and error.code < 500:
                    raise Rejected(f"Netlify refused the upload: HTTP {error.code}")
                raise ReleaseError("NETLIFY_HTTP", f"{method} {path.split('?')[0]} -> HTTP {error.code}")
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
                if attempt >= self.retries:
                    raise ReleaseError("NETWORK", "cannot reach the Netlify API")
                self.sleep(min(2 ** attempt, 60))
            finally:
                if hasattr(data, "close"):
                    data.close()
        raise ReleaseError("NETLIFY_HTTP", f"{method} {path.split('?')[0]} kept failing (last HTTP {last})")

    def wait(self, deploy_id: str, states, interval=2.0, limit=1800.0) -> dict:
        deadline = time.monotonic() + limit
        while True:
            deploy = self._call("GET", f"/deploys/{deploy_id}")
            if deploy.get("state") == "error":
                raise ReleaseError("NETLIFY_DEPLOY", f"deploy {deploy_id} failed: {str(deploy.get('error_message'))[:160]}")
            if deploy.get("state") in states:
                return deploy
            if time.monotonic() > deadline:
                raise ReleaseError("NETLIFY_TIMEOUT", f"deploy {deploy_id} stayed {deploy.get('state')}")
            self.sleep(interval)

    def site_url(self) -> str:
        site = self._call("GET", f"/sites/{self.site}")
        return (site.get("ssl_url") or site.get("url") or "").rstrip("/")

    def draft(self, files: dict[str, Path], interval=2.0) -> dict:
        """Create a draft deploy, upload only the required files, wait until ready. Returns the deploy JSON."""
        digests = {path: _sha1_file(src) for path, src in files.items()}
        created = self._call("POST", f"/sites/{self.site}/deploys", {"files": digests, "async": True, "draft": True})
        deploy = self.wait(created["id"], ("prepared", "uploading", "uploaded", "ready"), interval)
        needed = set(deploy.get("required") or created.get("required") or [])
        for path, src in sorted(files.items()):
            if digests[path] in needed:
                quoted = urllib.parse.quote(path.lstrip("/"), safe="/")
                self._call("PUT", f"/deploys/{created['id']}/files/{quoted}", file=Path(src), upload=True)
        return self.wait(created["id"], ("ready",), interval)

    def check(self, base_url: str, files: dict[str, Path]) -> list[str]:
        """Download every file from base_url and compare sha256 with the local copy. Returns mismatched paths."""
        wrong = []
        for path, src in sorted(files.items()):
            try:
                if sha256_url(base_url.rstrip("/") + urllib.parse.quote(path, safe="/"), self.timeout) != sha256_file(src):
                    wrong.append(path)
            except (urllib.error.URLError, OSError):
                wrong.append(path)
        return wrong

    def release(self, files: dict[str, Path], *, publish: bool, release_id: str, interval=2.0) -> dict:
        """draft -> upload -> ready -> download+compare -> (publish only if asked) -> re-verify production + /version.json."""
        deploy = self.draft(files, interval)
        draft_url = deploy.get("deploy_ssl_url") or deploy.get("deploy_url")
        wrong = self.check(draft_url, files)
        if wrong:
            raise ReleaseError("NETLIFY_VERIFY", f"draft {deploy['id']} serves different bytes for {wrong}; not published")
        result = {"deploy_id": deploy["id"], "draft_url": draft_url, "published": False, "verified": True}
        if not publish:
            return result
        self._call("POST", f"/sites/{self.site}/deploys/{deploy['id']}/restore")
        production = self.site_url()
        for attempt in range(self.verify_attempts):       # the CDN can lag the publish by seconds: back off, then give up
            wrong = self.check(production, files)
            code, body = http_get(production + "/version.json")
            if not wrong and code == 200 and served_release_id(body) == release_id:
                break
            if attempt + 1 < self.verify_attempts:
                self.sleep(min(2 ** (attempt + 1), 30))
        else:                                              # published already; nothing further is published, nothing is rolled back
            raise ReleaseError("NETLIFY_VERIFY", f"production does not serve release {release_id} after publish: {wrong or '/version.json'} "
                                                 f"(deploy {deploy['id']} is live and unverified; restore the previous deploy in Netlify "
                                                 "if it is wrong; the release stops here)")
        return {**result, "published": True, "production_url": production,
                "published_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def confirm_receipt(receipt_path, index_html, deploy_id: str, published_utc: str) -> None:
    """Extend dashboard_deployment_receipt.json with the remote deploy, but only if its recorded hash is the
    hash of the index.html that was uploaded (publish_dashboard.py's contract). Atomic replace."""
    receipt_path = Path(receipt_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("local_deploy_artifact", {}).get("sha256") != sha256_file(index_html):
        raise ReleaseError("RECEIPT_MISMATCH", "receipt hash is not the uploaded index.html; receipt left untouched")
    contract = receipt.setdefault("contract", {})
    contract.update(remote_deploy_confirmed=True, remote_deploy_id=deploy_id, remote_published_utc=published_utc)
    receipt["status"] = "REMOTE_DEPLOY_CONFIRMED"
    atomic_write(receipt_path, (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8"))
