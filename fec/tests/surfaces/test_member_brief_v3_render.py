"""Member desk: the v3 brief block replaces "Suggested approach" (template order, escaping, fallback, wiring)."""
import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from fec.members import validate_member_briefs_v3 as V
from fec.tests.members import test_member_briefs_v3 as fx

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "fec/surfaces/templates/make_member_desk_00.tpl"
BUILDER = ROOT / "fec/surfaces/make_member_desk.py"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node is required to exercise the browser renderer")

BRIEF = {
    "name": "Hakeem Jeffries", "pronoun": "he", "animal": "Bull", "descriptor": "skeptical and disciplined",
    "bottom_line": "Bring a tight case & facts.", "do": ["Do one", "Do two", "Do three", "Do four"],
    "dont": ["Dont one", "Dont two", "Dont three"], "warning_sign": "WARN", "follow_up": "FOLLOW",
    "personality": "PERSONALITY TEXT", "speaks": "SPEAKS TEXT", "decides": "DECIDES TEXT",
    "basis": {"line": "Basis: 412 floor speeches, 37 hearing transcripts, Mar 2015 to Oct 2026. Confidence: High."},
}


def render(psy):
    text = TEMPLATE.read_text(encoding="utf-8")
    helper = text.split("function briefV3HTML(B){", 1)[1].split("/* Approach brief.", 1)[0]
    narrative = text.split("function narrativeHTML(m){", 1)[1].split("function speechHTML(m){", 1)[0]
    script = ("const esc=s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');\n"
              "function briefV3HTML(B){" + helper + "\nfunction narrativeHTML(m){" + narrative
              + "process.stdout.write(narrativeHTML(JSON.parse(require('fs').readFileSync(0,'utf8'))));")
    done = subprocess.run([NODE, "-e", script], input=json.dumps({"psy": psy}), capture_output=True, text=True,
                          check=True, encoding="utf-8")
    return done.stdout


@needs_node
def test_v3_block_renders_in_template_order():
    html = render({"brief_v3": BRIEF})
    order = ["Hakeem Jeffries — The Bull", "Archetype: Bull, skeptical and disciplined", "Bottom line:", ">Do</div>",
             "<li>Do one</li>", "<li>Do four</li>", ">Don't</div>", "<li>Dont one</li>", "<li>Dont three</li>",
             "Warning sign:", "Follow-up:", ">Profile</div>", "Personality.", "How he speaks.", "How he decides.", "Basis: 412 floor"]
    at = [html.index(token) for token in order]
    assert at == sorted(at), dict(zip(order, at))
    assert html.count("<li>") == 7 and html.count("<ul") == 2
    assert html.rstrip().endswith("Confidence: High.</div>\n      </div>")        # the Basis line closes the block


@needs_node
def test_v3_block_replaces_the_old_approach_block_and_never_renders_a_playbook():
    html = render({"brief_v3": BRIEF, "narrative": {"p1": "OLD1", "p2": "OLD2", "p3": "OLD3", "meeting_playbook": {"open": "STALE"}},
                   "narrative_status": "withheld"})
    for stale in ("Suggested approach", "OLD1", "STALE", "In the room", "Approach brief pending"):
        assert stale not in html
    assert "Meeting brief" in html


@needs_node
def test_plural_pronoun_labels_and_text_escaping():
    html = render({"brief_v3": {**BRIEF, "pronoun": "they", "personality": "<b>x</b> & y"}})
    assert "How they speak." in html and "How they decide." in html and "How they speaks" not in html
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in html and "<b>x</b>" not in html
    assert "How she speaks." in render({"brief_v3": {**BRIEF, "pronoun": "she"}})


@needs_node
def test_v2_block_still_renders_when_there_is_no_v3_brief():
    html = render({"narrative": {"p1": "one", "p2": "two", "p3": "three",
                                 "meeting_playbook": {"open": "Legacy open", "bring": "b", "run_the_room": "r", "close": "c"}}})
    assert "Suggested approach" in html and "In the room" in html and "Legacy open" in html and "Meeting brief" not in html
    assert "Approach brief pending" in render({"narrative_status": "withheld"})
    assert render({}) == ""


@needs_node
def test_a_flagged_brief_still_renders_with_a_visible_evidence_updated_line():
    html = render({"brief_v3": {**BRIEF, "stale": {"date": "2026-10-20"}}})
    line = "Evidence updated after this brief was written (2026-10-20)"
    assert line in html and html.index("Archetype: Bull") < html.index(line) < html.index("Bottom line:")
    assert "Personality." in html and "Basis: 412 floor" in html                         # the brief itself is still there
    assert "Evidence updated" not in render({"brief_v3": BRIEF})
    assert "&lt;i&gt;" in render({"brief_v3": {**BRIEF, "stale": {"date": "<i>"}}})


def test_block_is_plain_nodes_inside_profile_so_print_includes_it():
    text = TEMPLATE.read_text(encoding="utf-8")
    block = text.split("function briefV3HTML(B){", 1)[1].split("/* Approach brief.", 1)[0]
    assert "<details" not in block and "display:none" not in block
    assert "narrativeHTML(m)\n     +sub('Biography'" in text                              # first thing inside Profile
    assert re.search(r"printbtn'\)\.onclick=\(\)=>\{document\.querySelectorAll\('#app-member details\.sec,#app-member details\.sub'\)", text)


def test_methodology_row_describes_the_v3_brief():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert re.search(r"if\(P\.brief_v3\)add\('Meeting brief'.*computed from source counts", text)


def test_display_payload_carries_the_computed_basis_and_drops_the_evidence_refs():
    packet = {"member": {"pronoun": "she"}, "basis": {"line": "Basis: 1 floor speech. Confidence: Low."}}
    brief = {"archetype": {"animal": "Owl", "descriptor": "d", "justification": "j"}, "bottom_line": "b", "do": [], "dont": [],
             "warning_sign": "w", "follow_up": "f", "personality": "p", "speaks": "s", "decides": "d", "evidence_refs": {"x": ["y"]}}
    payload = V.display_payload("Name", brief, packet)
    assert payload["basis"]["line"].endswith("Confidence: Low.") and payload["pronoun"] == "she" and "evidence_refs" not in payload
    assert "archetype" not in payload and "justification" not in json.dumps(payload) and "basis_withheld" not in payload
    withheld = V.display_payload("Name", brief, packet, basis_withheld="packet receipt is missing")
    assert withheld["basis"] is None and withheld["basis_withheld"] == "packet receipt is missing"
    none = V.display_payload("Name", brief, {"member": {"pronoun": "she"}, "basis": None})
    assert none["basis"] is None and none["basis_withheld"] == "no basis was computed for this member"


def desk_gate(root, monkeypatch):
    """Execute the desk's own v3 gate statements (import, load_publication, report) against a throwaway root."""
    monkeypatch.syspath_prepend(str(ROOT / "fec"))                              # the desk runs with fec/ on sys.path
    tree = ast.parse(BUILDER.read_text(encoding="utf-8"))
    start = next(i for i, n in enumerate(tree.body) if ast.unparse(n) == "import members.validate_member_briefs_v3 as BV3")
    gate = ast.Module(body=tree.body[start:start + 3], type_ignores=[])
    assert "load_publication" in ast.unparse(gate) and "BRIEFS_V3" in ast.unparse(gate.body[1].targets[0])
    ns = {"os": __import__("os"), "Path": Path, "B": str(root)}
    exec(compile(gate, str(BUILDER), "exec"), ns)
    return ns


def test_desk_shows_no_v3_brief_and_stays_quiet_when_nothing_is_published(tmp_path, capsys, monkeypatch):
    ns = desk_gate(tmp_path, monkeypatch)
    assert ns["BRIEFS_V3"] == {} and capsys.readouterr().out == ""


def test_desk_withholds_every_v3_brief_loudly_when_the_publication_is_rejected(tmp_path, capsys, monkeypatch):
    d = tmp_path / "data/members"
    d.mkdir(parents=True)
    (d / "member_briefs_v3.json").write_text("{}", encoding="utf-8")
    ns = desk_gate(tmp_path, monkeypatch)
    assert ns["BRIEFS_V3"] == {} and "WITHHELD" in capsys.readouterr().out


def test_desk_build_shows_clean_briefs_and_reports_the_counts(tmp_path, capsys, monkeypatch):
    root = fx.publish(tmp_path)
    ns = desk_gate(root, monkeypatch)
    assert set(ns["BRIEFS_V3"]) == {"Alex Rich", "Beth Thin", "Cam Floor"} and not any("stale" in p for p in ns["BRIEFS_V3"].values())
    assert "v3 briefs: 3 shown, 0 flagged for rewrite, 0 departed" in capsys.readouterr().out


def test_desk_build_flags_only_the_member_whose_evidence_moved_and_queues_it(tmp_path, capsys, monkeypatch):
    root = fx.publish(tmp_path)
    fx.edit_json(fx.P.input_paths(root)["ledger"], lambda d: d["members"]["Cam Floor"]["traits"]["Openness"].update(band=fx.B("well above most")))
    ns = desk_gate(root, monkeypatch)
    shown = ns["BRIEFS_V3"]
    today = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).date().isoformat()
    assert set(shown) == {"Alex Rich", "Beth Thin", "Cam Floor"}                         # nobody hidden
    assert shown["Cam Floor"]["stale"] == {"date": today} and "stale" not in shown["Alex Rich"] and "stale" not in shown["Beth Thin"]
    out = capsys.readouterr().out
    assert "v3 briefs: 3 shown, 1 flagged for rewrite, 0 departed" in out
    assert "Cam Floor: evidence changed since the brief was written" in out                # the reason is printed (Glassbox)
    assert list(fx.queue_of(root)["members"]) == ["Cam Floor"]


def test_desk_build_prints_why_a_member_could_not_be_rebuilt_and_why_a_basis_is_withheld(tmp_path, capsys, monkeypatch):
    root = fx.publish(tmp_path)
    fx.edit_json(fx.P.input_paths(root)["ledger"], lambda d: fx.corrupt_label(d, "Beth Thin"))
    (root / "data/members/member_brief_packets_v3.receipt.json").unlink()
    ns = desk_gate(root, monkeypatch)
    out = capsys.readouterr().out
    assert "Beth Thin: cannot rebuild the packet: unknown band label 'bogus'" in out
    assert "Basis lines withheld: packet receipt is missing" in out
    assert ns["BRIEFS_V3"]["Alex Rich"]["basis"] is None and ns["BRIEFS_V3"]["Alex Rich"]["basis_withheld"] == "packet receipt is missing"


def test_psy_for_shows_the_departed_notice_instead_of_any_brief_then_prefers_v3_then_v2():
    tree = ast.parse(BUILDER.read_text(encoding="utf-8"))
    psy_for = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "psy_for")
    notice_if = next(n for n in psy_for.body if isinstance(n, ast.If) and ast.unparse(n.test) == "notice")
    assert "out['departed_notice'] = notice" in ast.unparse(notice_if.body[0]) and len(notice_if.body) == 1      # nothing else for them
    assert any("BV3.departed_notice(name)" in ast.unparse(n) for n in psy_for.body if isinstance(n, ast.Assign))
    v3 = notice_if.orelse[0]
    assert ast.unparse(v3.test) == "name in BRIEFS_V3" and "out['brief_v3'] = BRIEFS_V3[name]" in ast.unparse(v3.body[0])
    v2 = v3.orelse[0]
    assert ast.unparse(v2.test) == "nar and nar.get('ok')"                                                   # v2 stays the fallback
    assert "narrative_status" in ast.unparse(v2.orelse[0])


def test_existing_descriptive_gate_slice_is_still_extractable():
    tree = ast.parse(BUILDER.read_text(encoding="utf-8"))
    names = [n.targets[0].id for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)]
    assert names.index("_NARR_LABELLED") < names.index("NARR")                          # the v2 gate slice is intact and contiguous
    texts = [ast.unparse(n) for n in tree.body]
    v3 = next(i for i, t in enumerate(texts) if t == "import members.validate_member_briefs_v3 as BV3")
    assert texts.index(next(t for t in texts if t.startswith("NARR = "))) < v3           # the v3 gate sits after the v2 gate, not inside it


@needs_node
def test_a_departed_member_shows_only_the_notice_and_neither_kind_of_brief():
    notice = "No longer serving (departed 2026-07-11)"
    html = render({"departed_notice": notice, "brief_v3": BRIEF, "narrative": {"p1": "OLD1", "p2": "OLD2", "p3": "OLD3"}, "narrative_status": "withheld"})
    assert notice in html
    for gone in ("Bottom line", "Personality.", "OLD1", "Suggested approach", "Approach brief pending", "Basis:"):
        assert gone not in html
    assert "No longer serving" not in render({"brief_v3": BRIEF})


@needs_node
def test_a_withheld_basis_is_shown_as_such_and_never_as_a_basis_line():
    html = render({"brief_v3": {**BRIEF, "basis": None, "basis_withheld": "packet receipt is not READY (status NOT_READY_PARTIAL_LONGFORM)"}})
    assert "Basis withheld: packet receipt is not READY (status NOT_READY_PARTIAL_LONGFORM)" in html and "Basis: " not in html.replace("Basis withheld", "")
    assert "Personality." in html                                                                             # the brief itself still shows


@needs_node
def test_the_pronoun_goes_through_esc_in_the_template():
    html = render({"brief_v3": {**BRIEF, "pronoun": "<img src=x onerror=alert(1)>"}})
    assert "<img" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html


def test_pronoun_is_escaped_at_every_use_in_the_source():
    block = TEMPLATE.read_text(encoding="utf-8").split("function briefV3HTML(B){", 1)[1].split("/* Approach brief.", 1)[0]
    assert "B.pronoun" in block and not re.search(r"'How '\+B\.pronoun", block) and "esc(B.pronoun)" in block


def resp_html(psy):
    text = TEMPLATE.read_text(encoding="utf-8")
    body = text.split("function respHTML(m){", 1)[1].split("\nfunction ", 1)[0]
    script = ("const esc=s=>String(s??'');\nfunction respHTML(m){" + body + "\n"
              "process.stdout.write(respHTML(JSON.parse(require('fs').readFileSync(0,'utf8'))));")
    return subprocess.run([NODE, "-e", script], input=json.dumps({"psy": psy}), capture_output=True, text=True, check=True, encoding="utf-8").stdout


@needs_node
def test_the_v2_quick_read_is_hidden_whenever_the_member_has_a_v3_brief_or_a_departed_notice():
    approach = "Lead with outcomes: a short, direct, results-focused brief."
    assert "Quick read" in resp_html({"approach": approach}) and approach in resp_html({"approach": approach})
    assert "Quick read" not in resp_html({"approach": approach, "brief_v3": BRIEF})
    assert "Quick read" not in resp_html({"approach": approach, "departed_notice": "No longer serving (departed 2026-07-11)"})
