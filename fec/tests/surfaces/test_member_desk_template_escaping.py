"""Member-desk template XSS guards: esc() must neutralise quotes, safeUrl() must refuse non-http(s).

The template's JS helpers are executed for real (bun, else node) on hostile input; the static
checks keep a raw ``href="${...}"`` interpolation of a data field from coming back.
"""
import html
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "fec" / "surfaces" / "templates" / "make_member_desk_00.tpl"
RUNTIME = shutil.which("bun") or shutil.which("node")

HOSTILE_TITLE = 'x" onmouseover="alert(1)'
LEGIT_URL = "https://example.com/a?b=1&c=2"

# Every non-empty url in the shipped desk data is http(s) (member_desk_bundle.json, cu-member-desk.html
# and the profiles bundle were scanned 2026-10-10), so these are the forms the link sites must keep.
LEGIT_URLS = [
    LEGIT_URL,
    "http://example.com/",
    "HTTPS://EXAMPLE.COM/Path",
    "  https://www.congress.gov/bill/119th-congress/house-bill/1234  ",  # surrounding blanks are trimmed
    "https://www.youtube.com/watch?v=abc123&t=42s",
    "https://api.census.gov/data/2024/acs/acs1/profile/groups/DP05.html",
    "https://www.britannica.com/topic/United-States-House-of-Representatives-Seats-by-State-1787120",
    "https://en.wikipedia.org/wiki/Ra%C3%BAl_Grijalva",
    "https://example.com/a#frag",
]
# None of these is an external web URL, so none may become a link at a url field.
REJECTED_URLS = [
    "javascript:alert(1)", "JAVASCRIPT:alert(1)", "  javascript:alert(1)", "\tjavascript:alert(1)",
    "java\tscript:alert(1)", "java\nscript:alert(1)", "vbscript:msgbox(1)",
    "data:text/html,<script>alert(1)</script>", "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "blob:https://example.com/uuid", "file:///C:/Windows/win.ini", "mailto:a@b.c", "tel:+15555550100",
    "//evil.example/x", "/relative/path", "relative/path", "www.example.com/x", "#", "#anchor", "", "   ",
]
REJECTED_JS = ["undefined", "null", "42", "({})", "[]"]


def helper_source(text, name):
    match = re.search(r"^function %s\(.*\}$" % name, text, re.M)
    assert match, "function %s() not found as a one-line declaration in the template" % name
    return match.group(0)


def run_helpers(cases, names=("esc", "attr", "safeUrl"), prelude=""):
    """Run the named template helpers exactly as shipped; cases maps label -> JS expression."""
    text = TEMPLATE.read_text(encoding="utf-8")
    source = prelude + "\n" + "\n".join(helper_source(text, n) for n in names)
    program = source + "\nconsole.log(JSON.stringify({" + ",".join(
        "%s:%s" % (json.dumps(k), v) for k, v in cases.items()) + "}));\n"
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "helpers.js"
        script.write_text(program, encoding="utf-8")
        done = subprocess.run([RUNTIME, str(script)], capture_output=True, encoding="utf-8", timeout=60, cwd=tmp)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


needs_js = pytest.mark.skipif(RUNTIME is None, reason="neither bun nor node on PATH")


@needs_js
def test_esc_and_attr_neutralise_quotes():
    out = run_helpers({
        "esc_title": "esc(%s)" % json.dumps(HOSTILE_TITLE),
        "attr_title": "attr(%s)" % json.dumps(HOSTILE_TITLE),
        "esc_mixed": "esc(%s)" % json.dumps("it's <b> & \"q\""),
        "attr_amp": "attr('a&b')",
        "esc_null": "esc(null)",
    }, names=("esc", "attr"))
    assert '"' not in out["esc_title"] and "&quot;" in out["esc_title"]
    assert out["attr_title"] == out["esc_title"]  # attr() adds nothing, and never double-encodes
    assert out["esc_mixed"] == "it&#39;s &lt;b&gt; &amp; &quot;q&quot;"
    assert out["attr_amp"] == "a&amp;b"
    assert out["esc_null"] == ""


@needs_js
def test_safe_url_rejects_everything_but_http_s_and_keeps_legit_urls():
    cases = {"bad%d" % i: "safeUrl(%s)" % json.dumps(v) for i, v in enumerate(REJECTED_URLS)}
    cases.update({"js%d" % i: "safeUrl(%s)" % v for i, v in enumerate(REJECTED_JS)})
    cases.update({"ok%d" % i: "safeUrl(%s)" % json.dumps(v) for i, v in enumerate(LEGIT_URLS)})
    hostile = {
        "dq": 'https://example.com/a" onmouseover="alert(1)',
        "sq": "https://example.com/a' onmouseover='alert(1)",
        "tag": "https://example.com/a><script>alert(1)</script>",
    }
    cases.update({k: "safeUrl(%s)" % json.dumps(v) for k, v in hostile.items()})
    out = run_helpers(cases)
    for i, value in enumerate(REJECTED_URLS):
        assert out["bad%d" % i] == "", repr(value)
    for i, value in enumerate(REJECTED_JS):
        assert out["js%d" % i] == "", value
    for i, value in enumerate(LEGIT_URLS):
        kept = out["ok%d" % i]
        assert html.unescape(kept) == value.strip(), value  # kept, attribute-escaped, decodes to the original
        assert not set('"\'<>') & set(kept), value
    assert '"' not in out["dq"] and "'" not in out["sq"] and "<" not in out["tag"] and ">" not in out["tag"]


def site_expression(text, start):
    match = re.search(r'href="\$\{(%s.*?)\}"' % re.escape(start), text)
    assert match, "no href interpolation starting with %s" % start
    return match.group(1)


@needs_js
def test_wikipedia_link_falls_back_to_the_constructed_https_url():
    expr = site_expression(TEMPLATE.read_text(encoding="utf-8"), "safeUrl((b.wikipedia||{}).url)")
    built = "https://en.wikipedia.org/wiki/Ra%C3%BAl%20Grijalva"
    given = "https://en.wikipedia.org/wiki/Raul_M._Grijalva"
    members = {
        "given": {"wikipedia": {"url": given}, "wikipedia_id": "Ra\u00fal Grijalva"},
        "hostile": {"wikipedia": {"url": "javascript:alert(1)"}, "wikipedia_id": "Ra\u00fal Grijalva"},
        "absent": {"wikipedia": None, "wikipedia_id": "Ra\u00fal Grijalva"},
    }
    out = run_helpers({k: "(function(b){return %s})(%s)" % (expr, json.dumps(v)) for k, v in members.items()})
    assert out == {"given": given, "hostile": built, "absent": built}


@needs_js
def test_congress_gov_bill_link_prefers_a_safe_url_and_otherwise_renders_none():
    expr = site_expression(TEMPLATE.read_text(encoding="utf-8"), "safeUrl(bill.url)||safeUrl(live.url)")
    good, live, bad = "https://www.congress.gov/bill/119th-congress/house-bill/1", "https://www.congress.gov/live", "javascript:alert(1)"
    pairs = {"both": (good, live), "bill_bad": (bad, live), "both_bad": (bad, bad), "none": (None, None)}
    out = run_helpers({k: "(function(bill,live){return %s})(%s,%s)" % (expr, json.dumps({"url": b}), json.dumps({"url": l}))
                       for k, (b, l) in pairs.items()})
    assert out == {"both": good, "bill_bad": live, "both_bad": "", "none": ""}


# Every url-valued link site and the field it reads (HEAD line numbers in the comments).
SAFE_URL_SITES = {
    "mm.u": 2,                     # staffer media chips 1149, 2784 (user-typed link)
    "u": 1,                        # staffer media add handler 2783
    "LF.url": 1,                   # observed long-form example 1211
    "x.url": 2,                    # long-form sources 1257, personal-detail source 1424
    "b.url": 2,                    # official site 1383, bill number 1813
    "(b.wikipedia||{}).url": 1,    # Wikipedia 1388
    "popBasis.source": 1,          # Britannica seat table 1515
    "url": 1,                      # Census profile links 1558
    "source.official_url": 1,      # demographic source 1658
    "cuSource": 1,                 # County Business Patterns source 1768
    "a.url": 3,                    # press evidence 1884, amendment 2096, press 2097
    "bill.url": 1,                 # Congress.gov bill link 2101 (also falls back to live.url)
}


@pytest.mark.parametrize("field,minimum", sorted(SAFE_URL_SITES.items()))
def test_every_url_link_site_goes_through_safe_url(field, minimum):
    text = TEMPLATE.read_text(encoding="utf-8")
    assert text.count('href="${safeUrl(%s)' % field) >= minimum


def test_no_unvetted_href_or_src_interpolation_remains():
    text = TEMPLATE.read_text(encoding="utf-8")
    # Allowed: safeUrl(); the two hrefs built purely from encodeURIComponent() (peopleHref, memberHref);
    # the attached-file download link, which is escaped and only rendered after a /^data:/i test on the
    # same line (a data: URL cannot be http(s) by design); and the Cook link, which only renders after
    # an https://www.cookpolitical.com/ allow-list test on the same line.
    allowed = ("safeUrl(", "peopleHref}", "memberHref(name)}")
    loose = []
    for m in re.finditer(r"""\b(?:href|src)=["']?\$\{""", text):
        rest = text[m.end():]
        line = text[text.rfind("\n", 0, m.start()) + 1:text.find("\n", m.start())]
        if (rest.startswith(allowed)
                or ("cookpolitical" in line and rest.startswith("attr(url)}"))
                or ("/^data:/i.test(f.data)" in line and rest.startswith("attr(f.data)}"))):
            continue
        loose.append(rest[:40])
    assert loose == []
    assert 'href="${a.url}"' not in text and 'href="${b.url}"' not in text


@needs_js
def test_imported_stance_override_must_be_a_known_label():
    # povOf() feeds innerHTML and the POVCOL lookup, and POV comes from an imported JSON file.
    pov = {
        "hostile": {"stance": "<img src=x onerror=1>"},
        "attr": {"stance": 'support" onmouseover="1'},
        "good_support": {"stance": "support"},
        "good_oppose": {"stance": "oppose"},
        "good_neutral": {"stance": "neutral"},
        "upper": {"stance": "Support"},
        "padded": {"stance": "support\n"},
        "array": {"stance": ["support"]},
        "object": {"stance": {"toString": 1}},
        "number": {"stance": 1},
        "empty": {"stance": ""},
        "missing": {"title": "no stance"},
    }
    cases = {key: "povOf(%s)" % json.dumps(key) for key in pov}
    cases.update({"unknown_url": 'povOf("https://never-imported.example/")',
                  "proto": 'povOf("__proto__")', "ctor": 'povOf("constructor")'})
    out = run_helpers(cases, names=("povOf",), prelude="let POV=%s;" % json.dumps(pov))
    expected = {key: None for key in cases}
    expected.update({"good_support": "support", "good_oppose": "oppose", "good_neutral": "neutral"})
    assert out == expected


def files_row_expression(text):
    start = text.index("files.map((f,k)=>")
    end = text.index(").join('')", start) + len(").join('')")
    return text[start:end]


@needs_js
def test_imported_note_file_links_only_for_data_urls():
    expr = files_row_expression(TEMPLATE.read_text(encoding="utf-8"))
    files = {
        "ok": {"name": "ok.txt", "data": "data:text/plain;base64,aGk="},
        "upper": {"name": "up.txt", "data": "DATA:text/plain,hi"},
        "js": {"name": "js.txt", "data": "javascript:alert(1)"},
        "https": {"name": "https.txt", "data": "https://example.com/f"},
        "blob": {"name": "blob.txt", "data": "blob:https://example.com/u"},
        "null": {"name": "null.txt", "data": None},
        "number": {"name": "num.txt", "data": 42},
        "hostile_name": {"name": 'x" onmouseover="alert(1)', "data": "javascript:alert(1)"},
        "tag_name": {"name": "<img src=x onerror=1>", "data": "data:text/plain,hi"},
        "quote_data": {"name": "q.txt", "data": 'data:text/plain,x" onclick="alert(1)'},
    }
    out = run_helpers({k: "(function(files){return %s})([%s])" % (expr, json.dumps(v)) for k, v in files.items()},
                      names=("esc", "attr"))
    assert 'href="data:text/plain;base64,aGk="' in out["ok"] and 'download="ok.txt"' in out["ok"]
    assert 'href="DATA:text/plain,hi"' in out["upper"]
    for key in ("js", "https", "blob", "null", "number", "hostile_name"):
        assert "href=" not in out[key], key
        assert "<span>\U0001F4CE " in out[key], key  # the name stays, as plain text
    assert '" onmouseover="' not in out["hostile_name"] and "&quot; onmouseover=&quot;" in out["hostile_name"]
    assert "<img" not in out["tag_name"] and "&lt;img" in out["tag_name"] and "href=" in out["tag_name"]
    assert '" onclick="' not in out["quote_data"] and 'href="data:text/plain,x&quot; onclick=&quot;alert(1)"' in out["quote_data"]


def fragment(text, start, end):
    a = text.index(start)
    return text[a:text.index(end, a) + len(end)]


@needs_js
def test_names_with_quotes_and_angle_brackets_are_escaped_in_the_member_header():
    text = TEMPLATE.read_text(encoding="utf-8")
    frag = fragment(text, "<h2>${esc(m.name)}", "${esc(m.district||'')}</div>")
    m = {"name": 'O"Brien <b>x</b>', "chamber": "House<", "party": "D\"<i>", "district": "1&2"}
    out = run_helpers({"h": "(function(m,potentialChip){return `%s`})(%s,()=>'')" % (frag, json.dumps(m))},
                      names=("esc",))["h"]
    assert "<b>" not in out and "<i>" not in out and 'O"Brien' not in out
    assert "O&quot;Brien &lt;b&gt;x&lt;/b&gt;" in out and "House&lt;" in out and "1&amp;2" in out


@needs_js
def test_committee_row_and_bioguide_link_escape_pipeline_fields():
    text = TEMPLATE.read_text(encoding="utf-8")
    row = fragment(text, "const role=c.role?", "</div>`;")
    link = fragment(text, "https://bioguide.congress.gov/search/bio/${", ")}")
    committee = {"name": 'Ways <b>&</b> "Means"', "role": "Chair<script>", "rel": 'Highly"<i>'}
    out = run_helpers({
        "row": "(function(c){const RELCOL={};const rc='#6b7280';%s})(%s)" % (row, json.dumps(committee)),
        "link": "(function(b){return `%s`})(%s)" % (link, json.dumps({"bioguide": 'X0001" onmouseover="1'})),
    }, names=("esc",))
    assert not any(tag in out["row"] for tag in ("<b>", "<script>", "<i>")) and '"Means"' not in out["row"]
    assert "Ways &lt;b&gt;&amp;&lt;/b&gt; &quot;Means&quot;" in out["row"] and "Chair&lt;script&gt;" in out["row"]
    assert out["link"] == "https://bioguide.congress.gov/search/bio/X0001%22%20onmouseover%3D%221"


def test_pipeline_fields_are_not_interpolated_raw():
    text = TEMPLATE.read_text(encoding="utf-8")
    raw = ["${m.name}", "${m.chamber}", "${m.party||'—'}", "${m.district||''}", "${x.chamber}", "${st||'?'}",
           "${a.cat}", "${a.date||''}", "${c.role}", "${c.name}", "${c.rel}", "${SRCL[r.src]||r.src}",
           "${P.approach}", "${b.bioguide}"]
    assert [fragment_ for fragment_ in raw if fragment_ in text] == []


def test_honor_facts_text_is_escaped_by_every_consumer_not_inside_it():
    # honorFacts() returns display text built from row.stance_reason; escaping it inside would
    # double-encode, so the contract is that each caller escapes it.
    text = TEMPLATE.read_text(encoding="utf-8")
    callers = re.findall(r"(\w+)\(honorFacts\(", text)
    assert sorted(callers) == ["esc", "esc", "escAttr"]
