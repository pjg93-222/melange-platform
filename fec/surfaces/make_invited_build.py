# -*- coding: utf-8 -*-
"""Invited build: post-process the BUILT desk artifacts under the governance profile.

Reads Moneyball.html and lobby-*.html (default: the live MONEYBALL_ROOT, fec/paths.py), applies
ops/governance/invited_profile.json ("melange-invited-profile-v1", see ops/governance/README.md) and
writes <input>/outputs/invited_build/: the processed pages, a deterministic <page>.gz beside each, and
INVITED_BUILD.json (sha256 of every input, output, gz and the profile; per-path drop counts; and
"governance": "pending" while the profile says pending, so release can refuse it for invitees).
It never touches make_combined.py, the chain or the inputs; nothing is written until every page has
been built and checked, and any failure exits 1 having changed nothing.

How the data is embedded (learned from make_combined.py and the templates; every one is a single JSON
value, found by its binding and then re-serialised by the same json.dumps settings it was written with):
  island            binding                                               in
  network           const DATA={...}                                      Moneyball.html
  desk              const D={...}                                         Moneyball.html
  desk_profiles     window.__melangeProfileMerge({...})                   Moneyball.html
  industry          <script id=page-data type=application/json>{...}      Moneyball.html, lobby-network.html
  lobby_<slug>      <script id=page-data type=application/json>{...}      lobby-<slug>.html
  staffer*          const SDATA= / GDATA= / ODATA= / CDATA= {...}         Moneyball.html
The builder proves it can reproduce an island's bytes (one of four json.dumps settings, with the
json_for_script escapes) before it edits it, so everything outside the edited keys is byte-identical;
an island it cannot reproduce, find exactly once, or parse stops the build.

Profile rules (anything the builder cannot find, or does not know, is an error; nothing is skipped):
  drop_paths        {island: [path]}  delete the key at every match; each must match at least once
                    unless its drop_paths_rows entry says "optional": true
  redact_fields     {island: [rule]}  truncate (max_chars, the ellipsis counts) or drop a field of the
                    elements a path reaches, optionally only where sibling keys have given values;
                    "defensive": true allows zero matches
  drop_surfaces     only "staffer": cut its panel, its script (all four const ...DATA islands) and the
                    shell's #stflink tab out of Moneyball.html
  drop_pages        the page is not built or copied; its <a href> links are cut from every output
  attribution       a plain-text "Sources and licences" footer in every output
  path grammar      a.b   key b of a      a[]  every element of list a      a{}  every value of dict a
After editing, no dropped path may remain, no truncated field may exceed its limit, and no string in any
island, and no byte of the output, may look like a local path (X:\\ or X:/, \\Users\\, AppData\\, melange-data, OneDrive, file:///).  Every island the profile describes
must be in its page (only a dropped surface excuses it, and then none of its bindings may remain), and no JSON
value over RESIDUAL_MAX bytes may be bound outside the described islands.  drop_sources, min_aggregate_n
and controls are validated and recorded; they are enforced by the rules above and by the server.

Run:  python fec/surfaces/make_invited_build.py [--input DIR] [--output DIR] [--profile FILE]
"""
import argparse
import datetime
import gzip
import hashlib
import html
import json
import os
import re
import sys
import tempfile

_FEC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _FEC)
from paths import ROOT as MB_ROOT  # noqa: E402

SCHEMA = "melange-invited-profile-v1"
DESK = "Moneyball.html"
MANIFEST = "INVITED_BUILD.json"
DEFAULT_PROFILE = os.path.join(os.path.dirname(_FEC), "ops", "governance", "invited_profile.json")
REQUIRED = {"schema", "pending", "islands", "drop_paths", "drop_paths_rows", "redact_fields", "attribution"}
OPTIONAL = {"notes", "matrix", "decisions", "generated", "source_tree", "drop_sources", "drop_surfaces",
            "drop_pages", "min_aggregate_n", "min_aggregate_n_scope", "controls"}
DESK_ISLANDS = ("network", "desk", "desk_profiles", "industry",
                "staffer", "staffer_offices", "staffer_organisations", "staffer_clients")
_JS = {"network": b"const DATA=", "desk": b"const D=", "desk_profiles": b"window.__melangeProfileMerge(",
       "staffer": b"const SDATA=", "staffer_offices": b"const GDATA=",
       "staffer_organisations": b"const ODATA=", "staffer_clients": b"const CDATA="}
_PAGE_DATA = b"<script id=page-data type=application/json>"
_FORMATS = tuple(dict(ensure_ascii=a, sort_keys=s, separators=(",", ":")) for a, s in
                 ((False, False), (False, True), (True, False), (True, True)))
_STEP = re.compile(r"([A-Za-z0-9_]+)(\[\]|\{\})?")
# A drive letter (not the tail of a scheme: https://), either slash, a Users/AppData directory, the data tree, OneDrive, file:///.
_LOCAL_PATH_SRC = r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]|[\\/]Users[\\/]|AppData[\\/]|melange-data|OneDrive|file:///"
_LOCAL_PATH = re.compile(_LOCAL_PATH_SRC, re.I)
# The same over raw page bytes, where a JSON/JS escape is a backslash too: "C’s:\n" is not a drive, "C:\\Users" and "C:\Users" are.
_LOCAL_PATH_BYTES = re.compile(_LOCAL_PATH_SRC.replace(r"[A-Za-z]:[\\/]", r"[A-Za-z]:(?:/|\\\\|\\(?![ntrbfu\"'/\\]))").encode(), re.I)
_LOBBY = re.compile(r"lobby-([a-z0-9-]+)\.html")
_STFLINK = re.compile(rb"<a id=stflink\b.*?</a>", re.S)
# Where a JS data literal can start: `x = {`, `f([`, `f(0,{`, `k:[`, `c?{`, `x=>[`, `return {`; or JSON.parse("...") / ('...'), group 1.
_BINDING = re.compile(rb"(?:=>|[=(,:?]|\breturn)\s*[{\[]|(JSON\s*\.\s*parse\s*\(\s*(?=[\"']))")
_JSTR = re.compile(rb"\"[^\"\\]*(?:\\.[^\"\\]*)*\"|'[^'\\]*(?:\\.[^'\\]*)*'", re.S)   # a JS string literal
_ESC = re.compile(r"\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|[\s\S])")                        # a JS escape: \n \u00e9 \x41 \' ...
_ESCV = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0", "\n": ""}
_SCRIPT = re.compile(rb"<script\b[^>]*>(.*?)</script>", re.S | re.I)
_TOKEN = re.compile(rb'"[^"\\]*(?:\\.[^"\\]*)*"|[\[\]{}]')              # a string, or a bracket outside one
RESIDUAL_MAX = 4096    # bytes. Census 2026-10-12 of the live built Moneyball.html and five lobby pages: the largest JSON value bound
                       # outside the described islands is 38 bytes (356 bindings in the desk, ~28 per lobby page); 4096 is ~100x that.


class BuildError(Exception):
    pass


# --- profile ---------------------------------------------------------------------------------

def parse_path(text):
    """'a.b[].c' -> [('a', ''), ('b', '[]'), ('c', '')]"""
    steps = []
    for seg in str(text).split("."):
        m = _STEP.fullmatch(seg)
        if not m:
            raise BuildError("profile path %r: bad segment %r (grammar: a.b, a[], a{})" % (text, seg))
        steps.append((m.group(1), m.group(2) or ""))
    return steps


def _unruled(node, trail=""):
    """Paths of every object that is not ruled: one that says ruled=false, or has a matrix "row" without ruled=true
    (a rule whose "ruled" key is missing or not literally true is not a ruling)."""
    if isinstance(node, dict):
        if node.get("ruled") is False or ("row" in node and node.get("ruled") is not True):
            yield trail or "profile"
        for k, v in node.items():
            yield from _unruled(v, "%s/%s" % (trail, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _unruled(v, "%s[%d]" % (trail, i))


def load_profile(path):
    """(profile, sha256).  Validated down to every path and rule, so the build never meets a surprise."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        raise BuildError("profile %s unreadable: %s" % (path, exc.strerror or exc))
    try:
        prof = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise BuildError("profile %s is not JSON: %s" % (path, exc))
    if not isinstance(prof, dict) or prof.get("schema") != SCHEMA:
        raise BuildError("profile schema must be %r" % SCHEMA)
    missing, unknown = REQUIRED - set(prof), set(prof) - REQUIRED - OPTIONAL
    if missing or unknown:
        raise BuildError("profile keys: missing %s, not understood by this builder %s" % (sorted(missing), sorted(unknown)))
    if not isinstance(prof["pending"], bool):
        raise BuildError('profile "pending" must be true or false')
    if not prof["pending"] and list(_unruled(prof)):
        raise BuildError('profile says pending=false but these rules are not ruled: %s' % ", ".join(sorted(set(_unruled(prof)))[:5]))
    if "min_aggregate_n" in prof and not (isinstance(prof["min_aggregate_n"], int) and prof["min_aggregate_n"] >= 1):
        raise BuildError("min_aggregate_n must be a positive integer")
    islands = prof["islands"]
    for part in ("drop_paths", "drop_paths_rows", "redact_fields"):
        if not isinstance(prof[part], dict):
            raise BuildError("profile %s must be an object" % part)
    for part in ("drop_sources", "drop_surfaces", "drop_pages"):
        if not all(isinstance(x, dict) for x in prof.get(part, [])) or not isinstance(prof.get(part, []), list):
            raise BuildError("profile %s must be a list of objects" % part)
    if not isinstance(islands, dict) or not set(DESK_ISLANDS) <= set(islands):
        raise BuildError("profile islands must describe every desk island: %s" % ", ".join(DESK_ISLANDS))
    rows = prof["drop_paths_rows"]
    drops = {}
    owned = set()
    for island, paths in prof["drop_paths"].items():
        if island not in islands or not isinstance(paths, list):
            raise BuildError("drop_paths: %r is not a described island with a list of paths" % island)
        for p in paths:
            label = "%s:%s" % (island, p)
            steps = parse_path(p)
            if steps[-1][1]:
                raise BuildError("drop path %s must end in a plain key" % label)
            if label not in rows or label in owned or not isinstance(rows[label], dict):
                raise BuildError("drop path %s needs exactly one drop_paths_rows entry" % label)
            owned.add(label)
            drops.setdefault(island, []).append((label, steps, bool(rows[label].get("optional") or rows[label].get("defensive"))))
    if set(rows) - owned:
        raise BuildError("drop_paths_rows has entries with no drop path: %s" % sorted(set(rows) - owned))
    reds = {}
    for island, rules in prof["redact_fields"].items():
        if island not in islands or not isinstance(rules, list):
            raise BuildError("redact_fields: %r is not a described island with a list of rules" % island)
        for r in rules:
            if not (isinstance(r, dict) and r.get("action") in ("truncate", "drop")
                    and isinstance(r.get("field"), str) and isinstance(r.get("path"), str)):
                raise BuildError("redact_fields[%s]: a rule needs path, field and action truncate|drop" % island)
            where = r.get("where", {})
            if not (isinstance(where, dict) and all(isinstance(v, list) for v in where.values())):
                raise BuildError("redact_fields[%s] %s: where must map keys to lists of values" % (island, r["field"]))
            if r["action"] == "truncate" and not (isinstance(r.get("max_chars"), int) and r["max_chars"] >= 2):
                raise BuildError("redact_fields[%s] %s: truncate needs max_chars >= 2" % (island, r["field"]))
            reds.setdefault(island, []).append(
                ("%s:%s#%s" % (island, r.get("path"), r["field"]), parse_path(r.get("path")), where, r["field"],
                 r["action"], r.get("max_chars"), bool(r.get("defensive"))))
    surfaces = [s.get("surface") for s in prof.get("drop_surfaces", [])]
    if set(surfaces) - {"staffer"}:
        raise BuildError("drop_surfaces: only 'staffer' is implemented, got %s" % sorted(set(surfaces) - {"staffer"}))
    pages = [p.get("page") for p in prof.get("drop_pages", [])]
    if not all(isinstance(p, str) and _LOBBY.fullmatch(p) for p in pages):
        raise BuildError("drop_pages must name lobby-<slug>.html files")
    att = prof["attribution"]
    if not (isinstance(att, dict) and isinstance(att.get("footer_title"), str) and isinstance(att.get("invited_build_notice"), str)
            and isinstance(att.get("sources"), list) and att["sources"]
            and all(isinstance(s, dict) and isinstance(s.get("id"), str) and isinstance(s.get("text"), str) for s in att["sources"])):
        raise BuildError("attribution needs footer_title, invited_build_notice and sources[{id, text}]")
    prof["_drops"], prof["_reds"], prof["_surfaces"], prof["_pages"] = drops, reds, set(surfaces), set(pages)
    return prof, hashlib.sha256(raw).hexdigest()


# --- islands ---------------------------------------------------------------------------------

def _dump(obj, kw):
    text = json.dumps(obj, **kw)   # moneyball_core.json_for_script: no '&', '<', '>' and no line separators
    return (text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def islands_for(name, prof):
    if name == DESK:
        return list(DESK_ISLANDS)
    key = "industry" if name == "lobby-network.html" else "lobby_" + _LOBBY.fullmatch(name).group(1).replace("-", "_")
    if key not in prof["islands"]:
        raise BuildError("%s: the profile does not describe island %r (add it to islands, or drop the page)" % (name, key))
    return [key]


def locate(data, key, name):
    """(start, end, obj, json kwargs) of island `key` in `data`, or None when it is not there."""
    marker = _PAGE_DATA if key == "industry" or key.startswith("lobby_") else _JS[key]
    hits = [m.end() for m in re.finditer(re.escape(marker), data) if data[m.end():m.end() + 1] == b"{"]
    if not hits:
        return None
    if len(hits) > 1:
        raise BuildError("%s: island %s is bound %d times; refusing to guess which one is the data" % (name, key, len(hits)))
    start = hits[0]
    stop = data.find(b"</script>", start)
    if stop < 0:
        raise BuildError("%s: island %s has no closing </script>" % (name, key))
    seg = data[start:stop].decode("utf-8")
    try:
        obj, n = json.JSONDecoder().raw_decode(seg)
    except ValueError as exc:
        raise BuildError("%s: island %s is not JSON (%s)" % (name, key, exc))
    text = seg[:n]
    del seg
    kw = next((k for k in _FORMATS if _dump(obj, k) == text), None)
    if kw is None:
        raise BuildError("%s: island %s is not in a serialisation this builder reproduces byte for byte" % (name, key))
    return start, start + len(text.encode("utf-8")), obj, kw


def resolve(node, steps):
    nodes = [node]
    for key, mode in steps:
        nxt = []
        for n in nodes:
            if isinstance(n, dict) and key in n:
                v = n[key]
                if mode == "[]" and isinstance(v, list):
                    nxt.extend(v)
                elif mode == "{}" and isinstance(v, dict):
                    nxt.extend(v.values())
                elif not mode:
                    nxt.append(v)
        nodes = nxt
    return nodes


def _holders(root, steps):
    return [n for n in resolve(root, steps[:-1]) if isinstance(n, dict) and steps[-1][0] in n]


def _matching(root, steps, where):
    return [e for e in resolve(root, steps) if isinstance(e, dict) and all(e.get(k) in v for k, v in where.items())]


def apply_rules(obj, drops, reds, where_label, counts):
    for label, steps, optional in drops:
        holders = _holders(obj, steps)
        if not holders and not optional:
            raise BuildError("%s: drop path %s matched nothing (a renamed key would ship silently; mark the "
                             'drop_paths_rows entry "optional": true if it is genuinely absent here)' % (where_label, label))
        for h in holders:
            del h[steps[-1][0]]
        counts["drop"][label] = counts["drop"].get(label, 0) + len(holders)
    for label, steps, where, field, action, limit, defensive in reds:
        hit = [e for e in _matching(obj, steps, where) if field in e]
        if not hit and not defensive:
            raise BuildError("%s: redaction %s matched nothing (mark the rule defensive if that is expected)" % (where_label, label))
        done = 0
        for e in hit:
            if action == "drop":
                del e[field]
                done += 1
            elif isinstance(e[field], str) and len(e[field]) > limit:
                e[field] = e[field][:limit - 1].rstrip() + "\u2026"
                done += 1
        counts["redact"][label] = counts["redact"].get(label, 0) + done
    # the gate: re-walk the result, never trust the edit loop
    for label, steps, optional in drops:
        if _holders(obj, steps):
            raise BuildError("%s: drop path %s still matches after the edit" % (where_label, label))
    for label, steps, where, field, action, limit, defensive in reds:
        for e in _matching(obj, steps, where):
            if (action == "drop" and field in e) or (action == "truncate" and isinstance(e.get(field), str) and len(e[field]) > limit):
                raise BuildError("%s: redaction %s left an element unredacted" % (where_label, label))


def local_path_leaks(obj):
    found, stack = [], [obj]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            stack.extend(n.keys())
            stack.extend(n.values())
        elif isinstance(n, list):
            stack.extend(n)
        elif isinstance(n, str) and _LOCAL_PATH.search(n):
            found.append(n[:60])
    return found


def _value_end(data, i, stop):
    """End offset of the bracketed value starting at data[i], or None when it never closes before `stop`."""
    depth = 0
    for m in _TOKEN.finditer(data, i, stop):
        t = m.group()
        if t in (b"{", b"["):
            depth += 1
        elif t in (b"}", b"]"):
            depth -= 1
            if not depth:
                return m.end()
    return None


def _js_string(literal):
    """The value of a JS string literal (quotes included)."""
    return _ESC.sub(lambda m: chr(int(m[1][1:], 16)) if len(m[1]) > 1 else _ESCV.get(m[1], m[1]), literal[1:-1])


def residual_json(data, known, limit=RESIDUAL_MAX):
    """[(offset, size)] of every JSON value over `limit` bytes bound in a <script> body (`= {`, `f(0,{`, `x=>[`, `return {`,
    JSON.parse("..."), ...) that is not inside one of the `known` island spans.  The described islands are the only data
    the profile has ruled on; any other large JSON literal would ship unredacted."""
    found = []
    for script in _SCRIPT.finditer(data):
        pos, end = script.start(1), script.end(1)
        while True:
            m = _BINDING.search(data, pos, end)
            if not m:
                break
            string = m[1] is not None
            i = m.end() if string else m.end() - 1
            span = next((e for s, e in known if s <= i < e), None)
            if span is not None:
                pos = span
                continue
            if string:
                lit = _JSTR.match(data, i, end)
                stop = lit and lit.end()
            else:
                stop = _value_end(data, i, end)
            pos = i + 1                                  # small or not JSON: look inside it too
            if stop and stop - i > limit:
                try:
                    text = data[i:stop].decode("utf-8")
                    json.loads(_js_string(text) if string else text)
                except ValueError:
                    continue
                found.append((i, stop - i))
                pos = stop
    return found


# --- the page around the islands -------------------------------------------------------------

def cut_links(data, page):
    """Remove every <a ... href="page" ...>...</a>; returns (data, how many)."""
    needle = b'href="%s"' % page.encode()
    out, pos, n = [], 0, 0
    while True:
        i = data.find(needle, pos)
        if i < 0:
            break
        s = data.rfind(b"<a ", pos, i)
        e = data.find(b"</a>", i)
        if s < 0 or e < 0 or b">" in data[s:i] or b"<a" in data[i:e]:
            raise BuildError("a link to %s is not a plain <a> element; cannot cut it safely" % page)
        out.append(data[pos:s])
        pos, n = e + 4, n + 1
    out.append(data[pos:])
    return b"".join(out), n


def drop_staffer(data):
    """Cut the staffer panel, script and shell tab (what make_combined.assemble(..., False) leaves out).
    Returns (data, 'dropped' | 'already absent')."""
    anchors = (b'<div id=app-staffer hidden>', b"const SDATA=", b"<a id=stflink")
    counts = [data.count(a) for a in anchors]
    if counts == [0, 0, 0]:
        return data, "already absent"
    if counts != [1, 1, 1]:
        raise BuildError("staffer surface: expected its panel, SDATA script and #stflink tab exactly once each, found %s" % counts)
    p0 = data.index(anchors[0])
    p1 = data.find(b"<script>", p0)
    if p1 < 0 or data[p1 - 6:p1] != b"</div>":
        raise BuildError("staffer surface: its panel no longer ends at the next <script>")
    k = data.index(anchors[1])
    s0 = data.rfind(b"<script>", 0, k)
    s1 = data.find(b"</script>", k) + len(b"</script>")
    span = data[s0:s1]
    if s0 < p1 or span.count(b"<script") != 1 or not span.startswith(b"<script>\n(function(){\n") \
            or not span.endswith(b"})();\n</script>") or b"__stafferShow" not in span:
        raise BuildError("staffer surface: its script is not the one make_combined emits")
    data = data[:p0] + data[p1:s0] + data[s1:]
    data, n = _STFLINK.subn(b"", data)
    if n != 1 or any(a in data for a in anchors[:2]) or b"const GDATA=" in data:
        raise BuildError("staffer surface: it survived the cut")
    return data, "dropped"


def footer(att, fixed):
    e = html.escape
    items = "".join("<li>%s</li>" % e(s["text"]) for s in att["sources"])
    note = '<p style="margin:0 0 8px">%s</p>' % e(att["invited_build_notice"])
    ul = '<ul style="margin:0;padding-left:18px">%s</ul>' % items
    if fixed:   # the desk is a full-viewport app: a collapsed pill that stays on screen
        return ('<footer id=melattrib style="position:fixed;right:10px;bottom:10px;z-index:130;max-width:min(560px,92vw);'
                'font:11px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;color:#D2CDBC"><details>'
                '<summary style="cursor:pointer;background:#282113;border:1px solid #564019;border-radius:6px;padding:5px 10px;'
                'color:#A49D8A">%s</summary><div style="background:#282113;border:1px solid #564019;border-radius:6px;'
                'margin-top:6px;padding:8px 12px;max-height:60vh;overflow:auto">%s%s</div></details></footer>'
                % (e(att["footer_title"]), note, ul))
    return ('<footer id=melattrib style="margin:24px auto;max-width:960px;padding:0 16px;'
            'font:12px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#A49D8A"><h2 style="font-size:13px;margin:0 0 6px">%s</h2>%s%s</footer>'
            % (e(att["footer_title"]), note, ul))


def transform(name, data, prof):
    """(output bytes, report) for one page; raises BuildError."""
    rep = {"drop": {}, "redact": {}, "page_links_cut": {}, "staffer_surface": None}
    for page in sorted(prof["_pages"]):
        data, n = cut_links(data, page)
        if not n:
            raise BuildError("%s: no link to the dropped page %s found (the page chrome has changed)" % (name, page))
        rep["page_links_cut"][page] = n
    if name == DESK and "staffer" in prof["_surfaces"]:
        data, rep["staffer_surface"] = drop_staffer(data)
        left = [k for k in DESK_ISLANDS if k.startswith("staffer") and _JS[k] in data]
        if left:   # "already absent" is only believed when every staffer binding is gone, not just the three anchors
            raise BuildError("%s: staffer island(s) %s survive the surface cut (%s)" % (name, ", ".join(left), rep["staffer_surface"]))
    if data.count(b"type=application/json") != 1:
        raise BuildError("%s: expected exactly one application/json data island, found %d (an undescribed island?)"
                         % (name, data.count(b"type=application/json")))
    edits = []
    for key in islands_for(name, prof):
        drops, reds = prof["_drops"].get(key, []), prof["_reds"].get(key, [])
        if rep["staffer_surface"] and key.startswith("staffer"):
            continue   # the island is gone with its surface (checked above); its rules are moot
        found = locate(data, key, name)
        if found is None:   # the profile describes it, so a page without it has changed shape: nothing is skipped
            raise BuildError("%s: island %s is not in the page (described in the profile; a renamed binding would ship unredacted)"
                             % (name, key))
        start, end, obj, kw = found
        apply_rules(obj, drops, reds, "%s/%s" % (name, key), rep)
        leaks = local_path_leaks(obj)
        if leaks:
            raise BuildError("%s/%s: %d string(s) look like local paths, e.g. %r (add a drop path or fix the producer)"
                             % (name, key, len(leaks), leaks[0]))
        edits.append((start, end, _dump(obj, kw).encode("utf-8")))
        del obj
    stray = residual_json(data, [(s, e) for s, e, _ in edits])
    if stray:
        raise BuildError("%s: %d JSON value(s) over %d bytes are bound outside the described islands, e.g. %d bytes after %r "
                         "(describe the island in the profile, or fix the producer)"
                         % (name, len(stray), RESIDUAL_MAX, stray[0][1], data[max(stray[0][0] - 40, 0):stray[0][0]]))
    out, pos = [], 0
    for start, end, new in sorted(edits):
        out += [data[pos:start], new]
        pos = end
    out.append(data[pos:])
    data = b"".join(out)
    tail = data.rstrip()
    if not tail.endswith(b"</body></html>"):
        raise BuildError("%s does not end with </body></html>; cannot place the attribution footer" % name)
    cut = len(tail) - len(b"</body></html>")
    data = data[:cut] + footer(prof["attribution"], name == DESK).encode("utf-8") + data[cut:]
    if data.count(b"id=melattrib") != 1:
        raise BuildError("%s: the attribution footer is not present exactly once" % name)
    leak = _LOCAL_PATH_BYTES.search(data)   # the whole page, not just the islands: a script, an attribute, a comment
    if leak:
        raise BuildError("%s: the output looks like it holds a local path, e.g. %r" % (name, data[max(leak.start() - 20, 0):leak.end() + 20]))
    return data, rep


# --- build -----------------------------------------------------------------------------------

def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _write(path, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def build(src, dst, profile_path):
    src, dst = os.path.abspath(src), os.path.abspath(dst)
    if os.path.normcase(os.path.realpath(src)) == os.path.normcase(os.path.realpath(dst)):
        raise BuildError("the output directory must not be the input directory")
    prof, prof_sha = load_profile(profile_path)
    if not os.path.isfile(os.path.join(src, DESK)):
        raise BuildError("%s is missing from %s" % (DESK, src))
    names = [DESK] + sorted(n for n in os.listdir(src) if _LOBBY.fullmatch(n) and n not in prof["_pages"])
    inputs, outputs, report = {}, {}, {}
    for name in names:
        with open(os.path.join(src, name), "rb") as f:
            data = f.read()
        inputs[name] = {"sha256": _sha(data), "bytes": len(data)}
        outputs[name], report[name] = transform(name, data, prof)
        del data
    os.makedirs(dst, exist_ok=True)
    try:
        os.remove(os.path.join(dst, MANIFEST))   # a failed rebuild must not leave an old manifest beside new files
    except FileNotFoundError:
        pass
    built = {}
    for name, data in outputs.items():
        gz = gzip.compress(data, compresslevel=9, mtime=0)
        _write(os.path.join(dst, name), data)
        _write(os.path.join(dst, name + ".gz"), gz)   # the page first, its .gz after: desk_server serves a .gz only if it is not older than the page
        built[name] = {"sha256": _sha(data), "bytes": len(data), "gz_sha256": _sha(gz), "gz_bytes": len(gz)}
    keep = set(built) | {n + ".gz" for n in built}
    for n in os.listdir(dst):   # a page dropped since the last build must stop being servable
        if (_LOBBY.fullmatch(n) or _LOBBY.fullmatch(n[:-3]) or n in (DESK, DESK + ".gz")) and n not in keep:
            os.remove(os.path.join(dst, n))
    manifest = {
        "schema": "melange-invited-build-v1",
        "governance": "pending" if prof["pending"] else "ruled",
        "built_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "profile": {"path": os.path.abspath(profile_path), "sha256": prof_sha},
        "inputs": inputs,
        "outputs": built,
        "dropped_surfaces": sorted(prof["_surfaces"]),
        "dropped_pages": sorted(prof["_pages"]),
        "dropped_sources": [s.get("id") for s in prof.get("drop_sources", [])],
        "min_aggregate_n": prof.get("min_aggregate_n"),
        "report": report,
    }
    _write(os.path.join(dst, MANIFEST), (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the invited (governance-profiled) desk from the built artifacts.")
    ap.add_argument("--input", default=MB_ROOT, help="directory holding Moneyball.html and lobby-*.html (default: MONEYBALL_ROOT)")
    ap.add_argument("--output", help="default: <input>/outputs/invited_build")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    a = ap.parse_args(argv)
    try:
        m = build(a.input, a.output or os.path.join(a.input, "outputs", "invited_build"), a.profile)
    except (BuildError, OSError) as exc:
        sys.stderr.write("ERROR: invited build refused: %s\n" % exc)
        return 1
    print("invited build: governance=%s, %d pages -> %s" % (m["governance"], len(m["outputs"]), a.output or "outputs/invited_build"))
    if m["governance"] == "pending":
        print("WARNING: the profile is pending; release must refuse this build for invitees")
    return 0


if __name__ == "__main__":
    sys.exit(main())
