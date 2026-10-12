import json
import re
import shutil
import subprocess
from pathlib import Path

from fec.speech import build_member_longform_review_app as mod


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_review_app_requires_personal_decisions_and_emits_source_bound_surface(tmp_path, monkeypatch):
    audio = tmp_path / "turn.wav"
    audio.write_bytes(b"review-audio-placeholder")
    queue = tmp_path / "queue.jsonl"
    drafts = tmp_path / "drafts.jsonl"
    out = tmp_path / "review.html"
    receipt = tmp_path / "receipt.json"
    write_jsonl(queue, [{
        "status": "IDENTITY_BOUNDARY_SEMANTIC_REVIEW_REQUIRED",
        "turn_id": "example-01", "member": "Example Member", "source_id": "source",
        "wav_sha256": "a" * 64, "transcript_sha256": "b" * 64,
        "turn_coverage": {"turn_binding_sha256": "c" * 64},
        "source_era": "NOT_CURRENT_FORMAL_LEADERSHIP", "start_sec": 10, "end_sec": 80,
        "duration_seconds": 70, "review_audio_wav": str(audio), "review_audio_sha256": "hash",
        "turn_start_in_review_audio_sec": 5, "turn_end_in_review_audio_sec": 75,
        "identity_cue_start_in_review_audio_sec": 1,
        "identity_cue_to_turn_gap_seconds": 4,
        "selected_identity_cue": {"phrase": "Example Member", "start_sec": 1, "end_sec": 2},
        "preceding_context": "Question", "turn_transcript": "Answer", "following_context": "Next",
    }])
    write_jsonl(drafts, [{
        "turn_id": "example-01", "identity_review_note": "Confirm the name cue.",
        "boundary_review_note": "Confirm both handoffs.", "semantic_findings": [{
            "kind": "observed_extended_answer", "finding": "The answer ran 70 seconds.",
            "scope": "One candidate turn; human review required.",
        }],
    }])
    monkeypatch.setattr("sys.argv", [
        "prog", "--queue", str(queue), "--drafts", str(drafts),
        "--out", str(out), "--receipt", str(receipt),
    ])
    assert mod.main() == 0
    rendered = out.read_text(encoding="utf-8")
    assert "Example Member" in rendered
    assert audio.resolve().as_uri() in rendered
    assert "I personally listened" in rendered
    assert "Selected in-band name cue" in rendered
    assert "Next pending" in rendered
    assert "Export progress backup" in rendered
    assert "reviews are still pending" in rendered
    assert "REVIEWER_STORE" in rendered
    assert "beforeunload" in rendered
    assert "Fix before export:\\n" in rendered
    assert "finalize_member_longform_profiles" not in rendered
    assert "Meeting advice is not required" in rendered
    rows = mod.review_rows(queue, drafts)
    assert rows[0]["wav_sha256"] == "a" * 64
    assert rows[0]["transcript_sha256"] == "b" * 64
    assert rows[0]["candidate_turn_binding_sha256"] == "c" * 64
    assert "wav_sha256:r.wav_sha256" in rendered
    assert "transcript_sha256:r.transcript_sha256" in rendered
    assert "candidate_turn_binding_sha256:r.candidate_turn_binding_sha256" in rendered
    node = shutil.which("node")
    if node:
        script = re.search(r"<script>(.*?)</script>", rendered, re.DOTALL)
        assert script
        subprocess.run([node, "--check"], input=script.group(1), text=True, check=True)
    assert '<option selected>ACCEPT</option>' not in rendered
    doc = json.loads(receipt.read_text(encoding="utf-8"))
    assert doc["counts"] == {"reviewable_turns": 1, "members": 1}
    assert doc["contract"]["preaccepted_decisions"] is False


def test_source_link_is_only_emitted_for_http_and_https_urls():
    rendered = mod.render([], "queue", "draft")
    line = next(line for line in rendered.splitlines() if line.lstrip().startswith("$('source').innerHTML="))
    assert "/^https?:\\/\\//i.test(r.url||'')" in line
    node = shutil.which("node")
    if node:
        code = """
const esc=s=>String(s??'').replace(/[&<>\\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const out={};const $=id=>out[id]=out[id]||{};
const show=r=>{""" + line + """;return out.source.innerHTML};
for(const bad of ['javascript:alert(1)','JaVaScRiPt:alert(1)',' javascript:alert(1)','data:text/html,x','file:///C:/x','//evil.example/x','vbscript:x']){
  const html=show({url:bad,title:'T',channel:'C'});
  if(html.includes('href=')||html.includes('<a'))throw new Error('link emitted for '+bad+': '+html);
  if(!html.includes('T'))throw new Error('title dropped for '+bad);
}
for(const good of ['https://example.gov/a?b=1&c=2','HTTP://Example.gov/a']){
  const html=show({url:good,title:'T',channel:'C'});
  if(!html.includes('<a href="'+esc(good)+'"'))throw new Error('link missing for '+good+': '+html);
}
if(show({url:null,title:'T',channel:'C'}).includes('href='))throw new Error('link emitted without a url');
"""
        subprocess.run([node, "-e", code], text=True, check=True)


def test_descriptive_finding_validation_and_optional_legacy_phase():
    rendered = mod.render([], "queue", "draft")
    match = re.search(r"function semanticProblems\(fs\).*?\n", rendered)
    assert match
    node = shutil.which("node")
    if node:
        code = match.group(0) + """
const finding={kind:'response_length',finding:'One complete response lasted 75 seconds.',scope:'One public interview turn.'};
if(semanticProblems([finding]).length)throw new Error('descriptive finding refused');
if(semanticProblems([{...finding,meeting_phase:'run_the_room'}]).length)throw new Error('legacy phase refused');
if(!semanticProblems([{...finding,meeting_phase:'invalid'}]).includes('valid meeting_phase'))throw new Error('invalid phase accepted');
if(!semanticProblems([{kind:'incomplete'}]).length)throw new Error('missing finding accepted');
if(!semanticProblems([null]).length)throw new Error('malformed finding accepted');
"""
        subprocess.run([node, "-e", code], text=True, check=True)
