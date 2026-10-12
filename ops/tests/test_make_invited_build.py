# -*- coding: utf-8 -*-
"""fec/surfaces/make_invited_build.py: the governance profile applied to the BUILT desk pages.

The fixtures are synthetic (no real member, lobbyist or path) but embed their data exactly the way the
real pages do: `const DATA=` / `const D=` / `window.__melangeProfileMerge(` / `<script id=page-data
type=application/json>` islands, each serialised with the json.dumps settings its builder uses, a shell
with the #stflink and Filings tabs, a staffer panel and script, and the closing </body></html>.
Everything happens in temp directories; the live tree is never read or written.

Runs as:  python -m pytest ops/tests/test_make_invited_build.py -q   or   python ops/tests/test_make_invited_build.py
"""
import contextlib
import copy
import gzip
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "fec"))
from surfaces import make_invited_build as MIB  # noqa: E402

BS = chr(92)
WIN = "C:" + BS + "Users" + BS + "someone" + BS + "audio" + BS + "t1.wav"   # a local path that must never ship
ISLAND_JSON = b"type=application/json>"


def esc(text):
    return (text.replace("&", BS + "u0026").replace("<", BS + "u003c").replace(">", BS + "u003e")
            .replace(" ", BS + "u2028").replace(" ", BS + "u2029"))


def dumps(obj, ascii_only=False, sort=False):
    return esc(json.dumps(obj, ensure_ascii=ascii_only, sort_keys=sort, separators=(",", ":")))


def article(source, n, title="t"):
    return {"source": source, "title": title * n, "url": "https://example.test/" + source}


NETWORK = {"name": ["Member Alpha", "Member Beta"], "x": [0.5, 1.25], "note": "R&D <b>café</b> \U0001F600",
           "speech_meta": {"corpus": {"collected_interview_transcripts": {"path": WIN, "n": 3},
                                      "source_media_recovery": {"inventory_summary": {"files": 9}, "kept": 2}}}}
DESK = {"cats": ["a", "b"], "members": [{"name": "Member Alpha",
        "speech_profile": {"longform_interview_evidence": {"full_turns": [{"turn_audio_wav": WIN, "text": "keep me"}]}}}],
        "speech_descriptive": {"corpus": {"collected_interview_transcripts": {"path": WIN, "n": 1},
                                          "source_media_recovery": {"inventory_summary": {"k": 1}, "other": True}}}}
PROFILES = {"members_heavy": [
    {"name": "Member Alpha", "staffdir": [{"nm": "Staff One", "st": "private note"}],
     "articles": [article("social", 500, "a"), article("news", 500, "b"), article("bluesky", 3, "c")],
     "personal": {"items": [{"source": "INTERVIEW", "evidence": "e" * 400}, {"source": "CREC", "evidence": "r" * 400}]}},
    {"name": "Member Beta", "staffdir": [], "articles": [], "personal": {"items": []}}],
    "bill_lobbying": {"bills": {"HR1": {"top_lobbyists": [{"name": "Lobbyist One"}], "orgs": [1, 2]},
                                "HR2": {"top_lobbyists": [], "orgs": [3]}}}}
INDUSTRY = {"receipts": {"bills": {"titles": {"cache_path": BS + "AppData" + BS + "x", "n": 2}}},
            "sectors": ["credit unions & affiliates", "<tag>", "café"]}
STAFFER = ({"global": {}, "members": [], "meta": {"closeness": {"member_context": {"path": WIN}}}, "who": "Staffer Marker"},
           {"offices": ["Staffer Marker"]}, {"orgs": ["Staffer Marker"]}, {"clients": ["Staffer Marker"]})
CLEAN_NET = {"name": ["Member Alpha"], "x": [1.5]}
CLEAN_DESK = {"cats": ["a"], "members": [{"name": "Member Alpha"}]}
CLEAN_IND = {"sectors": ["credit unions & affiliates"]}
STFLINK_TAB = ('<a id=stflink class=meltab data-surface=staffer href="staffer-network.html"><span class=meltabicon>S</span>'
               'Staffer network</a>')
FILINGS_TAB = '<a class=meltab href="lobby-interactions.html"><span class=meltabicon aria-hidden=true>X</span>Filings</a>'


def desk_page(staffer=True, network=NETWORK, desk=DESK, profiles=PROFILES, industry=INDUSTRY, extra_island=""):
    shell = ('<div id=melchrome><nav id=meltabs><a class="meltab netlink" href="Moneyball Dashboard.html">Main</a>'
             + STFLINK_TAB + FILINGS_TAB + '</nav></div>')
    stf = ""
    if staffer:
        s, g, o, c = (dumps(x) for x in STAFFER)
        stf = ('<script>\n(function(){\n"use strict";\nconst SDATA=%s;\nconst GDATA=%s;\nconst ODATA=%s;\nconst CDATA=%s;\n'
               'window.__stafferOpen=window.__stafferOpen||function(name){};'
               'window.__stafferShow=window.__stafferShow||function(){};\n})();\n</script>' % (s, g, o, c))
    return ("<!DOCTYPE html><html lang=en><head><meta charset=utf-8><title>Fixture</title><style>\n@layer desk,industry,staffer;\n"
            "@layer staffer{#app-staffer .x{color:red}}\n</style></head><body class=netmode>" + shell +
            "<div id=app-network><p>network body</p></div><div id=app-member hidden>desk body</div>"
            "<div id=app-industry hidden><main>industry body</main>"
            '<script id=page-data type=application/json>' + dumps(industry, sort=True) + "</script></div>"
            + ("<div id=app-staffer hidden><h2>Staffer Marker</h2><div>nested</div></div>" if staffer else "") +
            "<script>\n(function(){\nconst DATA=" + dumps(network, ascii_only=True) + ";\nconst N=DATA.name.length;\n})();\n</script>"
            "<script>\n(function(){\nconst D=" + dumps(desk) + ";\nconst DSPLIT=1;\n})();\n</script>"
            "<script>\nwindow.__melangeProfileMerge(" + dumps(profiles) + ");\n</script>"
            '<script>\n(function(){\n"use strict";\nconst INTERACTIONS_FILE="lobby-interactions.html";\n'
            'const DATA=JSON.parse(document.getElementById("page-data").textContent);\n})();\n</script>'
            + stf + extra_island + "<script>/*controller*/</script></body></html>")


def lobby_page(data, shortcut=False):
    return ("<!doctype html><html lang=en><head><meta charset=utf-8></head><body><header><nav>"
            '<a class=tab href="lobby-bills.html">Bills</a>'
            '<a class=tab href="lobby-interactions.html"><span class=tabicon>X</span>Filings</a>'
            + ('<a class=desklink href="lobby-interactions.html">Open filing ledger</a>' if shortcut else "") +
            "</nav></header><main id=main><p>page</p>"
            "<script id=page-data type=application/json>" + dumps(data, sort=True) + "</script>"
            "<script>const DATA=JSON.parse(document.getElementById(\"page-data\").textContent);</script></main></body></html>")


def profile(**over):
    rows = {"desk_profiles:members_heavy[].staffdir": {"row": "M-10", "ruled": False},
            "desk_profiles:bill_lobbying.bills{}.top_lobbyists": {"row": "M-05", "ruled": False},
            "desk:members[].speech_profile.longform_interview_evidence.full_turns[].turn_audio_wav": {"row": "X-09", "ruled": False},
            "desk:speech_descriptive.corpus.collected_interview_transcripts.path": {"row": "X-09", "ruled": False},
            "desk:speech_descriptive.corpus.source_media_recovery.inventory_summary": {"row": "X-09", "ruled": False},
            "network:speech_meta.corpus.collected_interview_transcripts.path": {"row": "X-09", "ruled": False},
            "network:speech_meta.corpus.source_media_recovery.inventory_summary": {"row": "X-09", "ruled": False},
            "staffer:meta.closeness.member_context.path": {"row": "X-09", "ruled": False},
            "industry:receipts.bills.titles.cache_path": {"row": "X-09", "ruled": False}}
    paths = {}
    for label in rows:
        island, path = label.split(":", 1)
        paths.setdefault(island, []).append(path)
    prof = {
        "schema": "melange-invited-profile-v1", "pending": True, "notes": "fixture",
        "islands": {k: {} for k in MIB.DESK_ISLANDS + ("lobby_champions", "lobby_interactions")},
        "drop_sources": [{"id": "legistorm", "row": "M-26", "ruled": False, "why": "none"}],
        "drop_surfaces": [{"surface": "staffer", "rows": ["M-07"], "ruled": False}],
        "drop_pages": [{"page": "lobby-interactions.html", "row": "M-06", "ruled": False}],
        "drop_paths": paths, "drop_paths_rows": rows,
        "redact_fields": {"desk_profiles": [
            {"row": "M-12", "ruled": False, "path": "members_heavy[].articles[]", "where": {"source": ["social", "bluesky"]},
             "field": "title", "action": "truncate", "max_chars": 200},
            {"row": "M-13", "ruled": False, "path": "members_heavy[].personal.items[]", "where": {"source": ["INTERVIEW"]},
             "field": "evidence", "action": "truncate", "max_chars": 200},
            {"row": "M-24", "ruled": False, "defensive": True, "path": "members_heavy[].staffdir[]", "field": "st", "action": "drop"}]},
        "min_aggregate_n": 10,
        "attribution": {"footer_title": "Sources and licences", "invited_build_notice": "Invited build <notice> & more.",
                        "sources": [{"id": "fec", "text": "FEC filings & reports."}, {"id": "lda", "text": "LDA filings."}]},
        "controls": {"row": "X-01", "ruled": False},
    }
    prof.update(over)
    return prof


def lean(paths=None, **over):
    """Only the drop paths given, no surface, page or redaction rules: for byte-fidelity tests."""
    paths = paths or {}
    rows = {"%s:%s" % (i, p): {"row": "X-09", "ruled": False} for i, ps in paths.items() for p in ps}
    base = dict(drop_surfaces=[], drop_pages=[], redact_fields={}, drop_paths=paths, drop_paths_rows=rows)
    base.update(over)
    return profile(**base)


def ruled(prof):
    prof = copy.deepcopy(prof)
    prof["pending"] = False

    def walk(n):
        if isinstance(n, dict):
            for k in list(n):
                if k == "ruled":
                    n[k] = True
                else:
                    walk(n[k])
        elif isinstance(n, list):
            for x in n:
                walk(x)
    walk(prof)
    return prof


def island(data, marker, sorted_=False):
    """Independent extraction of one JSON island: the value right after `marker`."""
    i = data.index(marker) + len(marker)
    obj, n = json.JSONDecoder().raw_decode(data[i:].decode("utf-8"))
    return obj


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.src = os.path.join(self.tmp, "src")
        self.dst = os.path.join(self.tmp, "out", "invited_build")
        os.makedirs(self.src)
        self.pfile = os.path.join(self.tmp, "profile.json")

    def put(self, name, text):
        with open(os.path.join(self.src, name), "wb") as f:
            f.write(text.encode("utf-8"))

    def fixtures(self, **kw):
        self.put("Moneyball.html", desk_page(**kw))
        self.put("lobby-network.html", lobby_page(INDUSTRY))
        self.put("lobby-champions.html", lobby_page({"rows": [1, 2]}, shortcut=True))
        self.put("lobby-interactions.html", lobby_page({"lobs": ["Lobbyist One"]}))

    def profile(self, prof=None):
        with open(self.pfile, "w", encoding="utf-8") as f:
            json.dump(profile() if prof is None else prof, f)

    def build(self, prof=None, dst=None):
        self.profile(prof)
        return MIB.build(self.src, dst or self.dst, self.pfile)

    def read(self, name, dst=None):
        with open(os.path.join(dst or self.dst, name), "rb") as f:
            return f.read()

    def refuses(self, message, prof=None):
        with self.assertRaises(MIB.BuildError) as cm:
            self.build(prof)
        self.assertIn(message, str(cm.exception))
        self.assertFalse(os.path.exists(self.dst), "a refused build must write nothing")


class FullBuild(Case):
    def setUp(self):
        super().setUp()
        self.fixtures()
        self.manifest = self.build()
        self.desk = self.read("Moneyball.html")

    def test_files_written_and_dropped_page_absent(self):
        names = sorted(os.listdir(self.dst))
        self.assertEqual(names, ["INVITED_BUILD.json", "Moneyball.html", "Moneyball.html.gz", "lobby-champions.html",
                                 "lobby-champions.html.gz", "lobby-network.html", "lobby-network.html.gz"])

    def test_drop_paths_are_gone_and_siblings_stay(self):
        net = island(self.desk, b"const DATA=")
        self.assertEqual(net["speech_meta"]["corpus"], {"collected_interview_transcripts": {"n": 3}, "source_media_recovery": {"kept": 2}})
        desk = island(self.desk, b"const D=")
        self.assertEqual(desk["members"][0]["speech_profile"]["longform_interview_evidence"]["full_turns"], [{"text": "keep me"}])
        self.assertEqual(desk["speech_descriptive"]["corpus"], {"collected_interview_transcripts": {"n": 1}, "source_media_recovery": {"other": True}})
        prof = island(self.desk, b"window.__melangeProfileMerge(")
        self.assertTrue(all("staffdir" not in m for m in prof["members_heavy"]))
        self.assertEqual(prof["bill_lobbying"]["bills"], {"HR1": {"orgs": [1, 2]}, "HR2": {"orgs": [3]}})
        ind = island(self.desk, b"<script id=page-data type=application/json>")
        self.assertEqual(ind["receipts"]["bills"]["titles"], {"n": 2})
        self.assertEqual(ind["sectors"], INDUSTRY["sectors"])
        self.assertEqual(island(self.read("lobby-network.html"), b"<script id=page-data type=application/json>"), ind)

    def test_redaction_truncates_only_the_selected_elements(self):
        arts = island(self.desk, b"window.__melangeProfileMerge(")["members_heavy"][0]["articles"]
        self.assertEqual([a["source"] for a in arts], ["social", "news", "bluesky"])
        self.assertEqual(len(arts[0]["title"]), 200)
        self.assertTrue(arts[0]["title"].endswith("…") and arts[0]["title"].startswith("a" * 199))
        self.assertEqual(arts[1]["title"], "b" * 500)          # source "news" is not in where
        self.assertEqual(arts[2]["title"], "ccc")              # short enough, untouched
        items = island(self.desk, b"window.__melangeProfileMerge(")["members_heavy"][0]["personal"]["items"]
        self.assertEqual((len(items[0]["evidence"]), len(items[1]["evidence"])), (200, 400))   # CREC stays whole

    def test_staffer_surface_is_cut_whole(self):
        for needle in (b"SDATA", b"GDATA", b"ODATA", b"CDATA", b"id=app-staffer", b"stflink", b"Staffer Marker", b"__stafferShow"):
            self.assertNotIn(needle, self.desk, needle)
        self.assertEqual(self.manifest["report"]["Moneyball.html"]["staffer_surface"], "dropped")
        self.assertIn(b'<a class="meltab netlink"', self.desk)   # neighbouring tabs untouched

    def test_dropped_page_links_are_cut_everywhere(self):
        for name in ("Moneyball.html", "lobby-network.html", "lobby-champions.html"):
            self.assertNotIn(b'href="lobby-interactions.html"', self.read(name), name)
        self.assertIn(b'href="lobby-bills.html"', self.read("lobby-network.html"))
        self.assertEqual(self.manifest["report"]["lobby-champions.html"]["page_links_cut"], {"lobby-interactions.html": 2})
        self.assertIn(b'INTERACTIONS_FILE="lobby-interactions.html"', self.desk)   # JS constant: not a link, no data

    def test_attribution_footer_once_per_page_escaped_plain_text(self):
        for name in ("Moneyball.html", "lobby-network.html", "lobby-champions.html"):
            data = self.read(name)
            self.assertEqual(data.count(b"id=melattrib"), 1, name)
            self.assertTrue(data.rstrip().endswith(b"</footer></body></html>") or data.rstrip().endswith(b"</main></footer></body></html>")
                            or b"</footer></body></html>" in data[-40:], name)
            self.assertIn(b"Invited build &lt;notice&gt; &amp; more.", data)
            self.assertIn(b"FEC filings &amp; reports.", data)
            self.assertNotIn(b"http://", data[data.index(b"id=melattrib"):])

    def test_manifest_records_hashes_counts_and_pending(self):
        m = json.loads(self.read("INVITED_BUILD.json"))
        self.assertEqual(m["governance"], "pending")
        self.assertEqual(m["profile"]["sha256"], MIB._sha(open(self.pfile, "rb").read()))
        self.assertEqual(sorted(m["inputs"]), ["Moneyball.html", "lobby-champions.html", "lobby-network.html"])
        with open(os.path.join(self.src, "Moneyball.html"), "rb") as f:
            self.assertEqual(m["inputs"]["Moneyball.html"]["sha256"], MIB._sha(f.read()))
        self.assertEqual(m["outputs"]["Moneyball.html"]["sha256"], MIB._sha(self.desk))
        self.assertEqual(m["outputs"]["Moneyball.html"]["gz_sha256"], MIB._sha(self.read("Moneyball.html.gz")))
        drop = m["report"]["Moneyball.html"]["drop"]
        self.assertEqual(drop["desk_profiles:members_heavy[].staffdir"], 2)
        self.assertEqual(drop["desk_profiles:bill_lobbying.bills{}.top_lobbyists"], 2)
        self.assertEqual(drop["desk:members[].speech_profile.longform_interview_evidence.full_turns[].turn_audio_wav"], 1)
        self.assertNotIn("staffer:meta.closeness.member_context.path", drop)   # moot with the surface gone
        self.assertEqual(m["report"]["Moneyball.html"]["redact"]["desk_profiles:members_heavy[].articles[]#title"], 1)
        self.assertEqual((m["dropped_surfaces"], m["dropped_pages"], m["dropped_sources"], m["min_aggregate_n"]),
                         (["staffer"], ["lobby-interactions.html"], ["legistorm"], 10))

    def test_each_gz_is_written_after_its_page_so_the_server_will_serve_it(self):
        for name in ("Moneyball.html", "lobby-network.html", "lobby-champions.html"):
            self.assertGreaterEqual(os.stat(os.path.join(self.dst, name + ".gz")).st_mtime_ns,
                                    os.stat(os.path.join(self.dst, name)).st_mtime_ns, name)    # desk_server skips an older .gz

    def test_gz_is_deterministic_and_round_trips(self):
        other = os.path.join(self.tmp, "again")
        self.build(dst=other)
        for name in ("Moneyball.html", "lobby-network.html", "lobby-champions.html"):
            gz = self.read(name + ".gz")
            self.assertEqual(gz, self.read(name + ".gz", other), name)
            self.assertEqual(gz[4:8], b"\0\0\0\0", "gzip mtime must be zero")
            self.assertEqual(gzip.decompress(gz), self.read(name))
            self.assertEqual(self.read(name), self.read(name, other))

    def test_second_build_drops_a_page_the_profile_no_longer_ships(self):
        stale = os.path.join(self.dst, "lobby-old.html")
        open(stale, "w").write("x")
        open(stale + ".gz", "w").write("x")
        os.makedirs(os.path.join(self.dst, "downloads"))
        self.build()
        self.assertFalse(os.path.exists(stale) or os.path.exists(stale + ".gz"))
        self.assertTrue(os.path.isdir(os.path.join(self.dst, "downloads")))


class Fidelity(Case):
    def test_untouched_content_is_byte_identical(self):
        """One drop path in one island and nothing else: the output is the input plus that edit plus the footer."""
        self.put("Moneyball.html", desk_page(staffer=False, network=CLEAN_NET, desk=CLEAN_DESK).replace(STFLINK_TAB, ""))
        self.put("lobby-champions.html", lobby_page({"rows": [1, 2]}))
        self.build(lean({"industry": ["receipts.bills.titles.cache_path"]}, drop_surfaces=[{"surface": "staffer", "ruled": False}]))
        want = copy.deepcopy(INDUSTRY)
        del want["receipts"]["bills"]["titles"]["cache_path"]
        src = self.read("Moneyball.html", self.src)
        expect = src.replace(dumps(INDUSTRY, sort=True).encode("utf-8"), dumps(want, sort=True).encode("utf-8"))
        self.assertNotEqual(expect, src)
        for name, want_bytes in (("Moneyball.html", expect), ("lobby-champions.html", self.read("lobby-champions.html", self.src))):
            out = self.read(name)
            i = out.index(b"<footer id=melattrib")
            j = out.index(b"</footer>", i) + len(b"</footer>")
            self.assertEqual(out[:i] + out[j:], want_bytes, name)

    def test_staffer_cut_matches_a_build_that_never_had_the_surface(self):
        """Cutting the surface from a four-surface page leaves what a staffer-less assembly emits."""
        kw = dict(network=CLEAN_NET, desk=CLEAN_DESK, industry=CLEAN_IND)
        self.put("Moneyball.html", desk_page(staffer=True, **kw))
        self.build(lean(drop_surfaces=[{"surface": "staffer", "ruled": False}]))
        cut = self.read("Moneyball.html")
        tab = (b'<a id=stflink class=meltab data-surface=staffer href="staffer-network.html">'
               b'<span class=meltabicon>S</span>Staffer network</a>')
        bare = desk_page(staffer=False, **kw).encode("utf-8").replace(tab, b"")
        i = cut.index(b"<footer id=melattrib")
        self.assertEqual(cut[:i] + cut[cut.index(b"</footer>", i) + len(b"</footer>"):], bare)

    def test_staffer_already_absent_is_fine(self):
        self.put("Moneyball.html", desk_page(staffer=False, network=CLEAN_NET, desk=CLEAN_DESK, industry=CLEAN_IND).replace(
            '<a id=stflink class=meltab data-surface=staffer href="staffer-network.html"><span class=meltabicon>S</span>Staffer network</a>', ""))
        m = self.build(lean(drop_surfaces=[{"surface": "staffer", "ruled": False}]))
        self.assertEqual(m["report"]["Moneyball.html"]["staffer_surface"], "already absent")


class ClaimRule(Case):
    """M1: the claim text beside the evidence quotes the same third-party sources, so it is cut where evidence is."""

    def test_shipped_profile_truncates_claim_wherever_it_truncates_evidence(self):
        prof, _sha = MIB.load_profile(MIB.DEFAULT_PROFILE)
        rules = prof["redact_fields"]["desk_profiles"]
        evidence = next(r for r in rules if r["field"] == "evidence")
        claim = next(r for r in rules if r["field"] == "claim")
        self.assertEqual((claim["path"], claim["where"], claim["action"], claim["max_chars"]),
                         (evidence["path"], evidence["where"], "truncate", evidence["max_chars"]))
        self.assertEqual(claim["row"], "M-12/M-13")
        self.assertIs(claim["ruled"], False)                                       # a recommendation until Paul rules it

    def test_claim_is_cut_for_third_party_sources_and_whole_for_the_congressional_record(self):
        profiles = copy.deepcopy(PROFILES)
        profiles["members_heavy"][0]["personal"]["items"] = [{"source": "INTERVIEW", "evidence": "e" * 400, "claim": "c" * 400},
                                                             {"source": "CREC", "evidence": "r" * 400, "claim": "k" * 400}]
        rule = {"row": "M-12/M-13", "ruled": False, "path": "members_heavy[].personal.items[]", "where": {"source": ["INTERVIEW"]},
                "field": "claim", "action": "truncate", "max_chars": 200}
        prof = profile()
        prof["redact_fields"]["desk_profiles"].append(rule)
        self.put("Moneyball.html", desk_page(profiles=profiles))
        self.put("lobby-network.html", lobby_page(INDUSTRY))
        self.put("lobby-champions.html", lobby_page({"rows": [1, 2]}))
        self.build(prof)
        out = island(self.read("Moneyball.html"), b"window.__melangeProfileMerge(")["members_heavy"][0]["personal"]["items"]
        self.assertEqual(len(out[0]["claim"]), 200)
        self.assertTrue(out[0]["claim"].endswith("…"))
        self.assertEqual(out[1]["claim"], "k" * 400)


class FailOpenGaps(Case):
    """Reviewer's gaps: a staffer island that outlives an 'already absent' verdict, an island nobody checks for, and
    large JSON bound outside every described island."""

    def test_a_staffer_island_that_survives_an_already_absent_verdict_fails(self):
        page = desk_page(staffer=False).replace("</body></html>", "<script>const GDATA=" + dumps(STAFFER[1]) + ";</script></body></html>")
        self.put("Moneyball.html", page.replace(STFLINK_TAB, ""))
        self.refuses("staffer island(s) staffer_offices survive the surface cut (already absent)", lean(drop_surfaces=[{"surface": "staffer"}]))

    def test_a_cut_surface_that_leaves_one_of_its_islands_behind_fails(self):
        page = desk_page().replace("</body></html>", "<script>const ODATA=" + dumps(STAFFER[2]) + ";</script></body></html>")
        self.put("Moneyball.html", page)
        self.refuses("staffer island(s) staffer_organisations survive the surface cut (dropped)", lean(drop_surfaces=[{"surface": "staffer"}]))

    def test_every_described_island_must_be_in_its_page_even_without_rules(self):
        self.fixtures()
        # the lobby page's one JSON island is bound differently: it is not the island the profile describes
        self.put("lobby-champions.html", lobby_page({"rows": [1]}).replace("<script id=page-data type=application/json>",
                                                                               "<script id=other-data type=application/json>"))
        self.refuses("island lobby_champions is not in the page", profile())

    def test_large_json_outside_the_described_islands_fails_and_small_does_not(self):
        big = '<script>window.__extra=' + json.dumps({"rows": ["x" * 20] * (MIB.RESIDUAL_MAX // 20 + 10)}) + ';</script>'
        small = '<script>window.__extra=' + json.dumps({"rows": ["x"] * 50}) + ';</script>'
        self.put("Moneyball.html", desk_page(extra_island=small))
        self.put("lobby-champions.html", lobby_page({"rows": [1]}))
        self.put("lobby-network.html", lobby_page(INDUSTRY))
        self.build(profile())                                                  # small data outside the islands is page chrome
        self.dst = os.path.join(self.tmp, "out", "second")
        self.put("Moneyball.html", desk_page(extra_island=big))
        self.refuses("JSON value(s) over %d bytes are bound outside the described islands" % MIB.RESIDUAL_MAX, profile())

    def test_residual_scan_sees_through_json_in_a_call_and_ignores_js_that_is_not_json(self):
        data = b"<script>f([" + b'"' + b"x" * 100 + b'"' + b"]);const C={a:1,b:[" + b"1," * 100 + b"x]};</script>"
        self.assertEqual([size for _o, size in MIB.residual_json(data, [], limit=50)], [104])    # the JSON array; the JS literal is not JSON
        self.assertEqual(MIB.residual_json(data, [(10, 120)], limit=50), [])                     # inside a known island span

    def test_residual_scan_finds_a_json_literal_in_every_shape_it_can_be_bound_in(self):
        obj = b'{"rows":["' + b"x" * 100 + b'"]}'
        arr = b'["' + b"x" * 100 + b'"]'
        lit = lambda b, q: q + b.replace(b"\\", b"\\\\").replace(q, b"\\" + q) + q       # a JS string literal holding b
        shapes = {
            "second call argument": (b"f(0," + obj + b");", len(obj)),
            "second call argument, spaced": (b"f(0,  " + arr + b");", len(arr)),
            "arrow body object": (b"var g=x=>" + obj + b";", len(obj)),
            "arrow body array": (b"var g=x=> " + arr + b";", len(arr)),
            "return object": (b"function g(){return " + obj + b"}", len(obj)),
            "return array": (b"function g(){return" + arr + b"}", len(arr)),
            "ternary": (b"var g=c?" + arr + b":0;", len(arr)),
            "property value": (b"var g={k:" + arr + b"};", len(arr)),
            "JSON.parse double quoted": (b"var g=JSON.parse(" + lit(obj, b'"') + b");", len(lit(obj, b'"'))),
            "JSON.parse single quoted": (b"var g=JSON . parse( " + lit(obj, b"'") + b");", len(lit(obj, b"'"))),
            "JSON.parse unicode escapes": (b'var g=JSON.parse("\\u007b\\u0022a\\u0022:\\u0022' + b"x" * 100 + b'\\u0022\\u007d");', None),
        }
        for name, (js, size) in shapes.items():
            with self.subTest(name):
                got = MIB.residual_json(b"<script>" + js + b"</script>", [], limit=50)
                self.assertEqual(len(got), 1, got)
                if size is not None:
                    self.assertEqual(got[0][1], size)

    def test_residual_scan_ignores_json_parse_that_is_small_not_json_a_variable_or_inside_a_known_island(self):
        def scan(js, known=()):
            return MIB.residual_json(b"<script>" + js + b"</script>", list(known), limit=50)
        self.assertEqual(scan(b'JSON.parse("{\\"a\\":1}")'), [])                                      # small
        self.assertEqual(scan(b'JSON.parse("' + b"nope" * 30 + b'")'), [])                           # long, not JSON
        self.assertEqual(scan(b"JSON.parse(text);JSON.parse(" + b"x" * 100 + b")"), [])             # not a string literal
        js = b'JSON.parse("[' + b"1," * 60 + b'2]")'
        self.assertEqual(len(scan(js)), 1)
        self.assertEqual(scan(js, [(8, 8 + len(js))]), [])                                           # a described island


class Governance(Case):
    def test_pending_true_is_recorded(self):
        self.fixtures()
        self.assertEqual(self.build()["governance"], "pending")

    def test_pending_false_is_ruled(self):
        self.fixtures()
        self.assertEqual(self.build(ruled(profile()))["governance"], "ruled")
        self.assertEqual(json.loads(self.read("INVITED_BUILD.json"))["governance"], "ruled")

    def test_pending_false_needs_ruled_true_on_every_object_with_a_row(self):
        for label, mutate in (("no ruled key", lambda p: p["controls"].pop("ruled")),
                              ("ruled null", lambda p: p["drop_sources"][0].update(ruled=None)),
                              ("ruled a string", lambda p: p["redact_fields"]["desk_profiles"][0].update(ruled="yes")),
                              ("a row row without ruled", lambda p: p["drop_paths_rows"].update(
                                  {"industry:receipts.bills.titles.cache_path": {"row": "X-09"}}))):
            prof = ruled(profile())
            mutate(prof)
            with self.subTest(label):
                self.refuses("these rules are not ruled", prof)
        self.fixtures()
        self.assertEqual(self.build(ruled(profile()))["governance"], "ruled")         # every row ruled: fine

    def test_pending_false_with_an_unruled_rule_is_refused(self):
        self.fixtures()
        bad = profile(pending=False)
        self.refuses("not ruled", bad)

    def test_pending_must_be_a_boolean(self):
        self.fixtures()
        self.refuses('"pending" must be true or false', profile(pending="no"))

    def test_missing_profile_fails(self):
        self.fixtures()
        with self.assertRaises(MIB.BuildError) as cm:
            MIB.build(self.src, self.dst, os.path.join(self.tmp, "absent.json"))
        self.assertIn("unreadable", str(cm.exception))
        self.assertFalse(os.path.exists(self.dst))

    def test_wrong_schema_and_unknown_keys_fail(self):
        self.fixtures()
        self.refuses("schema must be", profile(schema="v2"))
        self.refuses("not understood by this builder ['export_everything']", profile(export_everything=True))
        bad = profile()
        del bad["attribution"]
        self.refuses("missing ['attribution']", bad)

    def test_only_the_staffer_surface_is_implemented(self):
        self.fixtures()
        self.refuses("only 'staffer' is implemented", profile(drop_surfaces=[{"surface": "desk"}]))

    def test_profile_must_describe_every_desk_island(self):
        self.fixtures()
        bad = profile()
        del bad["islands"]["desk_profiles"]
        self.refuses("must describe every desk island", bad)

    def test_every_drop_path_needs_exactly_one_row(self):
        self.fixtures()
        bad = profile()
        bad["drop_paths_rows"].pop("industry:receipts.bills.titles.cache_path")
        self.refuses("needs exactly one drop_paths_rows entry", bad)
        bad = profile()
        bad["drop_paths_rows"]["industry:no.such.row"] = {"row": "X"}
        self.refuses("has entries with no drop path", bad)

    def test_bad_path_grammar(self):
        self.fixtures()
        bad = profile()
        bad["drop_paths"]["industry"] = ["receipts..cache_path"]
        self.refuses("bad segment", bad)
        bad["drop_paths"]["industry"] = ["receipts.bills[]"]
        self.refuses("must end in a plain key", bad)

    def test_output_may_not_overwrite_the_input(self):
        self.fixtures()
        self.profile()
        with self.assertRaises(MIB.BuildError):
            MIB.build(self.src, self.src, self.pfile)


class FailClosed(Case):
    def setUp(self):
        super().setUp()
        self.fixtures()

    def test_unknown_drop_path_fails_with_the_path_named(self):
        bad = profile()
        bad["drop_paths"]["industry"].append("receipts.bills.titles.cache_pth")
        bad["drop_paths_rows"]["industry:receipts.bills.titles.cache_pth"] = {"row": "X-09", "ruled": False}
        self.refuses("industry:receipts.bills.titles.cache_pth matched nothing", bad)

    def test_optional_path_may_match_nothing(self):
        bad = profile()
        bad["drop_paths"]["desk"].append("members[].staffdir")
        bad["drop_paths_rows"]["desk:members[].staffdir"] = {"row": "M-10", "ruled": False, "optional": True}
        self.build(bad)
        self.assertTrue(os.path.exists(os.path.join(self.dst, "INVITED_BUILD.json")))

    def test_redaction_that_matches_nothing_fails_unless_defensive(self):
        bad = profile()
        bad["redact_fields"]["desk_profiles"][0]["where"] = {"source": ["nothing-has-this-source"]}
        self.refuses("redaction desk_profiles:members_heavy[].articles[]#title matched nothing", bad)
        ok = profile()
        ok["redact_fields"]["desk_profiles"][0]["where"] = {"source": ["nothing-has-this-source"]}
        ok["redact_fields"]["desk_profiles"][0]["defensive"] = True
        self.build(ok)

    def test_rules_for_an_island_that_is_not_in_the_page_fail(self):
        bad = profile()
        bad["drop_paths"]["lobby_champions"] = ["rows"]
        bad["drop_paths_rows"]["lobby_champions:rows"] = {"row": "X", "ruled": False}
        self.put("lobby-champions.html", lobby_page([1]))   # a list is not an object island: the marker is not followed by '{'
        self.refuses("island lobby_champions is not in the page", bad)

    def test_a_local_path_that_no_rule_drops_fails_the_build(self):
        leaky = copy.deepcopy(INDUSTRY)
        leaky["extra"] = {"file": WIN}
        self.put("lobby-network.html", lobby_page(leaky))
        self.refuses("look like local paths", profile())

    def test_local_paths_with_forward_slashes_onedrive_and_file_urls_fail_too(self):
        for leak in ("C:/Users/someone/audio/t1.wav", "d:/melange-data/x.json", "/home/x/OneDrive/Desktop/y", "file:///etc/passwd",
                     BS * 2 + "server" + BS + "share" + BS + "AppData" + BS + "Local"):
            leaky = copy.deepcopy(INDUSTRY)
            leaky["extra"] = {"file": "see " + leak}
            self.put("lobby-network.html", lobby_page(leaky))
            self.refuses("look like local paths", profile())

    def test_a_local_path_outside_every_island_fails_and_urls_do_not(self):
        self.fixtures()
        self.put("Moneyball.html", desk_page().replace("<script>/*controller*/</script>",
                                                       '<script>/*controller*/const SRC="E:/Users/x/Desktop/audio.wav";</script>'))
        self.refuses("the output looks like it holds a local path", profile())
        self.put("Moneyball.html", desk_page().replace("<script>/*controller*/</script>",
                                                       '<script>/*controller*/const SRC="https://example.test/path",re=/^data:/i;</script>'))
        self.build(profile(), dst=os.path.join(self.tmp, "ok1"))              # schemes and regex literals are not drive letters
        self.put("Moneyball.html", desk_page().replace("<script>/*controller*/</script>",
                                                       '<script>/*controller*/const T="Q: the THREE C' + "\u2019" + 's:\\n\\n Lower Costs";</script>'))
        self.build(profile(), dst=os.path.join(self.tmp, "ok2"))              # a JSON/JS escape after "s:" is not a path either
        self.put("Moneyball.html", desk_page().replace("<script>/*controller*/</script>",
                                                       '<script>/*controller*/const P="C:' + BS * 2 + "Users" + BS * 2 + 'x";</script>'))
        self.refuses("the output looks like it holds a local path", profile())

    def test_dropped_field_removes_the_local_path_that_would_otherwise_fail(self):
        self.build()   # WIN lives only under dropped paths in the fixtures

    def test_unreproducible_serialisation_fails(self):
        pretty = json.dumps(DESK, indent=2)
        self.put("Moneyball.html", desk_page().replace(dumps(DESK), pretty))
        self.refuses("not in a serialisation this builder reproduces", profile())

    def test_duplicate_binding_fails(self):
        page = desk_page().replace("<script>/*controller*/</script>", "<script>const D=" + dumps(DESK) + ";</script><script>/*controller*/</script>")
        self.put("Moneyball.html", page)
        self.refuses("is bound 2 times", profile())

    def test_undescribed_json_island_fails(self):
        self.put("Moneyball.html", desk_page(extra_island='<script type=application/json id=other>{"a":1}</script>'))
        self.refuses("expected exactly one application/json data island", profile())

    def test_lobby_page_the_profile_does_not_describe_fails(self):
        self.put("lobby-surprise.html", lobby_page({"a": 1}))
        self.refuses("does not describe island 'lobby_surprise'", profile())

    def test_dropped_page_with_no_link_to_cut_fails(self):
        self.put("lobby-network.html", lobby_page(INDUSTRY).replace('href="lobby-interactions.html"', 'href="elsewhere.html"'))
        self.refuses("no link to the dropped page lobby-interactions.html", profile())

    def test_half_a_staffer_surface_fails(self):
        self.put("Moneyball.html", desk_page().replace("<div id=app-staffer hidden>", "<div id=gone hidden>"))
        self.refuses("staffer surface: expected its panel", profile())

    def test_missing_desk_fails(self):
        os.remove(os.path.join(self.src, "Moneyball.html"))
        self.refuses("Moneyball.html is missing", profile())

    def test_failed_rebuild_leaves_no_old_manifest_next_to_new_files(self):
        self.build()
        bad = profile()
        bad["drop_paths"]["industry"].append("nope")
        bad["drop_paths_rows"]["industry:nope"] = {"row": "X"}
        with self.assertRaises(MIB.BuildError):
            self.build(bad)
        # transform runs before any write, so the previous good build is intact
        self.assertTrue(os.path.exists(os.path.join(self.dst, "INVITED_BUILD.json")))


class Grammar(unittest.TestCase):
    DOC = {"a": {"b": [{"c": 1}, {"c": 2, "d": 3}, 5]}, "m": {"x": {"k": 1}, "y": {"k": 2}}}

    def test_steps(self):
        self.assertEqual(MIB.parse_path("a.b[].c"), [("a", ""), ("b", "[]"), ("c", "")])
        self.assertEqual(MIB.parse_path("m{}.k"), [("m", "{}"), ("k", "")])

    def test_resolve_and_holders(self):
        self.assertEqual(MIB.resolve(self.DOC, MIB.parse_path("a.b[]")), [{"c": 1}, {"c": 2, "d": 3}, 5])
        self.assertEqual(len(MIB._holders(self.DOC, MIB.parse_path("a.b[].c"))), 2)      # the int element is skipped
        self.assertEqual(len(MIB._holders(self.DOC, MIB.parse_path("m{}.k"))), 2)
        self.assertEqual(MIB._holders(self.DOC, MIB.parse_path("a.b{}.c")), [])           # list is not a dict
        self.assertEqual(MIB._holders(self.DOC, MIB.parse_path("a.zzz.c")), [])


class Cli(Case):
    def test_main_exit_codes_and_message(self):
        self.fixtures()
        self.profile()
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            code = MIB.main(["--input", self.src, "--output", self.dst, "--profile", self.pfile])
        self.assertEqual(code, 0)
        self.assertIn("governance=pending", out.getvalue())
        self.assertIn("WARNING", out.getvalue())
        bad = profile()
        bad["drop_paths"]["industry"].append("nope")
        bad["drop_paths_rows"]["industry:nope"] = {"row": "X"}
        self.profile(bad)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = MIB.main(["--input", self.src, "--output", os.path.join(self.tmp, "o2"), "--profile", self.pfile])
        self.assertEqual(code, 1)
        self.assertIn("ERROR: invited build refused", err.getvalue())
        self.assertIn("industry:nope matched nothing", err.getvalue())

    def test_main_missing_profile_exits_1(self):
        self.fixtures()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = MIB.main(["--input", self.src, "--output", self.dst, "--profile", os.path.join(self.tmp, "none.json")])
        self.assertEqual(code, 1)
        self.assertIn("profile", err.getvalue())


if __name__ == "__main__":
    unittest.main()
