# -*- coding: utf-8 -*-
"""Melange desk server: the invited build (Moneyball.html + lobby-*.html + downloads) over plain HTTP.

stdlib only, GET and HEAD only, an allowlist of five routes, nothing else.  It sits behind the
Cloudflare Access tunnel; fec/access_gate.py does the authentication (see its docstring).

  /                          DESK_ROOT/Moneyball.html
  /lobby-<slug>.html         slug [a-z0-9-]{1,64}
  /downloads/<id>/<name>     id YYYY.MM.DD.N or "latest"; name [A-Za-z0-9._-]{1,128}, no leading dot
  /healthz                   200 "ok"
  /version                   JSON from access_gate.version_payload("melange-desk")
  anything else              404;  any other method 405

The request path is matched as sent: it is never percent-decoded and never joined to a path
until a route regex has accepted every character, so "%2e%2e", "%2f", backslashes, "//x" and an
absolute-form target all miss the routes.  The file is then resolved and must still lie inside
DESK_ROOT (so a symlink out of the root is a 404); no directory is ever listed.  If "<file>.gz"
exists and the client accepts gzip it is sent instead with Content-Encoding: gzip (the build
writes it with make_invited_build.py).  Downloads are sent as attachments.

Environment: DESK_ROOT (required, the invited_build directory), DESK_PORT (default 8080),
BIND_HOST (default 127.0.0.1) and the ACCESS_* / ALLOWED_HOSTS variables of fec/access_gate.py.
A non-loopback BIND_HOST without the ACCESS_* settings exits with status 2.

Run:  python -m fec.surfaces.desk_server     or     python fec/surfaces/desk_server.py
Logs: one JSON line per request on stderr (access_gate.audit_line): no query string, no client IP.
"""
import email.utils
import json
import os
import re
import shutil
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # fec/
from access_gate import AccessMixin, audit_line, require_config_or_exit, version_payload  # noqa: E402

APP = "melange-desk"
_LOBBY = re.compile(r"/lobby-([a-z0-9-]{1,64})\.html")
_DOWNLOAD = re.compile(r"/downloads/(latest|[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]+)/([A-Za-z0-9._-]{1,128})")
_TYPES = {".html": "text/html; charset=utf-8", ".json": "application/json", ".txt": "text/plain; charset=utf-8",
          ".csv": "text/csv; charset=utf-8", ".pdf": "application/pdf", ".zip": "application/zip",
          ".gz": "application/gzip", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
_DRAIN_MAX = 1 << 20
TEXT = "text/plain; charset=utf-8"


def accepts_gzip(header):
    """RFC 9110 Accept-Encoding, reduced to the one question: may we send gzip?"""
    verdict = {}
    for part in (header or "").lower().split(","):
        name, _, params = part.partition(";")
        q = 1.0
        for p in params.split(";"):
            k, _, v = p.strip().partition("=")
            if k == "q":
                try:
                    q = float(v)
                except ValueError:
                    q = 0.0
        verdict[name.strip()] = q
    return verdict.get("gzip", verdict.get("x-gzip", verdict.get("*", 0.0))) > 0


class DeskHandler(AccessMixin, BaseHTTPRequestHandler):
    access_app = APP
    desk_root = None          # resolved Path; set by make_server
    server_version = APP
    sys_version = ""
    timeout = 30              # seconds a client may stall on any one read or write

    def version_string(self):
        return APP

    # --- logging: audit_line only; http.server's own lines carry the client IP and the query ---
    def log_request(self, code="-", size="-"):
        sys.stderr.write(audit_line(APP, self.access_user, getattr(self, "command", None) or "-",
                                    getattr(self, "path", "-"), int(code) if str(code).isdigit() else 0) + "\n")

    def log_message(self, fmt, *args):
        # http.server errors (bad request line, timeout): the format string only, never its arguments.
        sys.stderr.write(audit_line(APP, None, getattr(self, "command", None) or "-", "-", 0,
                                    "http.server: " + str(fmt)[:60]) + "\n")

    # --- responses ---
    def _reply(self, code, body, ctype=TEXT, extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for name, value in extra:
            self.send_header(name, value)
        self.send_security_headers()
        self.end_headers()
        if getattr(self, "command", None) != "HEAD":
            self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self._reply(code, b"error\n", extra=[("Connection", "close")])

    def _resolve(self, rel):
        """The regular file DESK_ROOT/rel, or None if it is missing or resolves outside DESK_ROOT."""
        try:
            path = (self.desk_root / rel).resolve()
            return path if path.is_relative_to(self.desk_root) and path.is_file() else None
        except (OSError, ValueError):
            return None

    def _serve(self, rel, attachment=None):
        """`attachment`: the route's own file name, sent as Content-Disposition (never the name a symlink resolves to)."""
        base = self._resolve(rel)
        if base is None:
            return self._reply(404, b"not found\n")
        side = self._resolve(rel + ".gz")
        try:   # a .gz older than its page is left over from before a rebuild (the build writes the page, then the .gz)
            if side is not None and os.stat(side).st_mtime_ns < os.stat(base).st_mtime_ns:
                side = None
        except OSError:
            side = None
        use_gz = side is not None and accepts_gzip(self.headers.get("Accept-Encoding"))
        try:
            fh = open(side if use_gz else base, "rb")
        except OSError:
            return self._reply(404, b"not found\n")
        with fh:
            st = os.fstat(fh.fileno())
            self.send_response(200)
            self.send_header("Content-Type", _TYPES.get(os.path.splitext(rel)[1].lower(), "application/octet-stream"))
            self.send_header("Content-Length", str(st.st_size))
            if use_gz:
                self.send_header("Content-Encoding", "gzip")
            if side is not None:
                self.send_header("Vary", "Accept-Encoding")
            self.send_header("ETag", '"%x-%x%s"' % (st.st_size, st.st_mtime_ns, "-gz" if use_gz else ""))
            self.send_header("Last-Modified", email.utils.formatdate(st.st_mtime, usegmt=True))
            if attachment:
                self.send_header("Content-Disposition", 'attachment; filename="%s"' % attachment)   # [A-Za-z0-9._-], from _DOWNLOAD
            self.send_security_headers()
            self.end_headers()
            if self.command != "HEAD":
                shutil.copyfileobj(fh, self.wfile, 1 << 20)

    # --- methods ---
    def do_GET(self):   # access_gate's parse_request has already run access_check, once, for every method
        try:
            self._route(self.path.split("?", 1)[0])
        except (ConnectionError, TimeoutError):
            self.close_connection = True   # the client went away mid-body; nothing to answer

    do_HEAD = do_GET

    def _route(self, route):
        if route == "/healthz":
            return self._reply(200, b"ok\n")
        if route == "/version":
            return self._reply(200, (json.dumps(version_payload(APP), sort_keys=True) + "\n").encode(), "application/json")
        if route == "/":
            return self._serve("Moneyball.html")
        m = _LOBBY.fullmatch(route)
        if m:
            return self._serve("lobby-%s.html" % m.group(1))
        m = _DOWNLOAD.fullmatch(route)
        if m and not m.group(2).startswith("."):
            return self._serve("downloads/%s/%s" % m.groups(), attachment=m.group(2))
        self._reply(404, b"not found\n")

    def _reject(self):
        try:   # read a small body so closing the socket cannot reset the connection under the 405
            n = int(self.headers.get("Content-Length") or 0)
            if 0 < n <= _DRAIN_MAX:
                self.rfile.read(n)
        except (ValueError, OSError):
            pass
        self.close_connection = True
        self._reply(405, b"method not allowed\n", extra=[("Allow", "GET, HEAD"), ("Connection", "close")])

    def __getattr__(self, name):   # every method but GET and HEAD, known to http.server or not
        if name.startswith("do_"):
            return self._reject
        raise AttributeError(name)


def _handle_error(self, request, client_address):
    """http.server's default prints the client address and a full traceback; the audit line names only the exception type."""
    sys.stderr.write(audit_line(APP, None, "-", "-", 0, "server error: " + getattr(sys.exc_info()[0], "__name__", "?")) + "\n")


def make_server(root, cfg, host, port):
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError("DESK_ROOT is not a directory")
    handler = type("Handler", (DeskHandler,), {"access_cfg": cfg, "desk_root": root})
    host = host.strip("[]")
    server = type("Server", (ThreadingHTTPServer,), {"address_family": socket.AF_INET6 if ":" in host else socket.AF_INET,
                                                    "handle_error": _handle_error})
    return server((host, port), handler)


def main():
    cfg = require_config_or_exit()
    root = os.environ.get("DESK_ROOT", "").strip()
    if not root or not os.path.isdir(root):
        sys.stderr.write("desk server: DESK_ROOT must name an existing directory\n")
        return 2
    try:
        port = int(os.environ.get("DESK_PORT") or 8080)
    except ValueError:
        sys.stderr.write("desk server: DESK_PORT must be a number\n")
        return 2
    server = make_server(root, cfg, cfg.bind_host, port)
    sys.stderr.write("desk server: %s:%d enforced=%s root=%s\n"
                     % (cfg.bind_host or "*", server.server_address[1], cfg.enforced, server.RequestHandlerClass.desk_root))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
