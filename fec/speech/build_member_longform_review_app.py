#!/usr/bin/env python3
"""Build a standalone browser review surface for roster-wide long-form turns.

The page embeds the non-authoritative queue and automated drafts, but it never
pre-accepts a decision.  A reviewer must listen to the context-padded audio,
make identity/boundary/semantic decisions, provide notes and a reviewer name,
and explicitly attest to the review before the page will export materializer-
compatible JSONL.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
from pathlib import Path

from fec.speech import source_media_recovery as SMR


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUEUE = ROOT / "data/speech/member_longform_review_queue_v1.jsonl"
DEFAULT_DRAFTS = ROOT / "data/speech/member_longform_review_drafts_v1.jsonl"
DEFAULT_OUT = ROOT / "member-longform-review.html"
DEFAULT_RECEIPT = ROOT / "data/speech/member_longform_review_app_v1.receipt.json"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def file_uri(value: str | None) -> str | None:
    if not value:
        return None
    path = Path(value).resolve()
    return path.as_uri() if path.is_file() else None


def review_rows(queue_path: Path, drafts_path: Path) -> list[dict]:
    drafts = {
        str(row["turn_id"]): row for row in SMR.read_jsonl(drafts_path, required=True)
    }
    rows = []
    for item in SMR.read_jsonl(queue_path, required=True):
        if item.get("status") != "IDENTITY_BOUNDARY_SEMANTIC_REVIEW_REQUIRED":
            continue
        draft = drafts.get(str(item["turn_id"])) or {}
        rows.append({
            "turn_id": item["turn_id"],
            "member": item["member"],
            "source_id": item["source_id"],
            "wav_sha256": item.get("wav_sha256"),
            "transcript_sha256": item.get("transcript_sha256"),
            "candidate_turn_binding_sha256": (item.get("candidate_turn_binding_sha256")
                                               or (item.get("turn_coverage") or {}).get("turn_binding_sha256")),
            "source_era": item.get("source_era"),
            "current_role": item.get("current_role"),
            "title": item.get("title"),
            "channel": item.get("channel"),
            "url": item.get("url"),
            "start_sec": item["start_sec"],
            "end_sec": item["end_sec"],
            "duration_seconds": item["duration_seconds"],
            "review_audio_uri": file_uri(item.get("review_audio_wav")),
            "review_audio_sha256": item.get("review_audio_sha256"),
            "turn_start_in_review_audio_sec": item.get("turn_start_in_review_audio_sec"),
            "turn_end_in_review_audio_sec": item.get("turn_end_in_review_audio_sec"),
            "literal_name_cues": item.get("literal_name_cues") or [],
            "selected_identity_cue": item.get("selected_identity_cue") or {},
            "identity_cue_start_in_review_audio_sec": item.get("identity_cue_start_in_review_audio_sec"),
            "identity_cue_to_turn_gap_seconds": item.get("identity_cue_to_turn_gap_seconds"),
            "preceding_context": item.get("preceding_context") or "",
            "turn_transcript": item.get("turn_transcript") or "",
            "following_context": item.get("following_context") or "",
            "speaker_continuity_method": item.get("speaker_continuity_method"),
            "consecutive_acoustic_windows": item.get("consecutive_acoustic_windows"),
            "draft_identity_note": draft.get("identity_review_note") or "",
            "draft_boundary_note": draft.get("boundary_review_note") or "",
            "draft_semantic_findings": draft.get("semantic_findings") or [],
        })
    return sorted(rows, key=lambda row: str(row["member"]))


def render(rows: list[dict], queue_sha256: str, draft_sha256: str) -> str:
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    title = "Congressional long-form turn review"
    # One stable store keyed by turn_id; each saved decision carries its own turn binding,
    # so queue rebuilds and turns entering/leaving the queue never hide unchanged decisions.
    content_key = "v2"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root{{--bg:#111827;--panel:#1f2937;--line:#374151;--ink:#f3f4f6;--muted:#9ca3af;--gold:#facc15;--blue:#7dd3fc;--red:#fca5a5;--green:#86efac}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,sans-serif}}
header{{position:sticky;top:0;z-index:2;background:#0b1220;border-bottom:1px solid var(--line);padding:12px 18px}}
h1{{font-size:18px;margin:0 0 5px}} .muted{{color:var(--muted)}} .warning{{color:var(--gold);font-weight:650}}
.controls{{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px}} input,select,textarea,button{{font:inherit}}
input,select,textarea{{background:#111827;color:var(--ink);border:1px solid #4b5563;border-radius:5px;padding:7px}}
button{{background:#334155;color:white;border:1px solid #64748b;border-radius:5px;padding:7px 10px;cursor:pointer}}
button.primary{{background:#075985}} main{{max-width:1180px;margin:auto;padding:16px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}} .full{{grid-column:1/-1}}
label{{display:block;color:var(--blue);font-size:12px;font-weight:700;margin-bottom:4px}}
textarea{{width:100%;min-height:82px;resize:vertical}} #semantic{{min-height:220px;font-family:ui-monospace,monospace;font-size:12px}}
.transcript{{white-space:pre-wrap;background:#111827;border:1px solid var(--line);border-radius:6px;padding:10px;max-height:260px;overflow:auto}}
.turn{{border-color:#0369a1}} audio{{width:100%}} a{{color:#93c5fd}} .meta{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:10px}}
.pill{{background:#111827;border:1px solid var(--line);border-radius:999px;padding:2px 8px}}
.attest{{padding:10px;border:1px solid #854d0e;background:#422006;border-radius:6px}}
.status-ok{{color:var(--green)}} .status-bad{{color:var(--red)}}
@media(max-width:760px){{.grid{{grid-template-columns:1fr}}.full{{grid-column:1}}}}
</style></head><body>
<header><h1>{html.escape(title)}</h1>
<div><span id="position"></span> · <span id="progress"></span></div>
<div class="warning">Automated drafts are suggestions only. Export requires a person to listen and explicitly review identity, both speaker boundaries, and every semantic finding.</div>
<div class="muted">Save completed decisions as a review JSONL for source-bound validation. Exporting decisions does not publish profiles or approve unreviewed turns.</div>
<div class="controls"><button id="prev">← Previous</button><button id="next">Next →</button><button id="nextPending">Next pending</button><input id="search" placeholder="Find member"><button id="find">Find</button><button id="save">Save this review</button><button id="exportProgress">Export progress backup</button><button class="primary" id="export">Export final JSONL</button></div>
</header><main><div id="empty" class="card" hidden>No reviewable turns were produced.</div><div id="card" class="card">
<h2 id="member"></h2><div class="meta" id="meta"></div><div id="source"></div>
<div class="grid" style="margin-top:12px">
<div class="full"><label>Review audio — from before the selected name cue through the complete answer and outgoing handoff</label><audio id="audio" controls preload="metadata"></audio><div class="muted" id="audioHint"></div></div>
<div class="full"><label>Selected in-band name cue</label><div class="transcript" id="cue"></div></div>
<div><label>Preceding context</label><div class="transcript" id="before"></div></div>
<div><label>Following context</label><div class="transcript" id="after"></div></div>
<div class="full"><label>Proposed complete member turn</label><div class="transcript turn" id="turn"></div></div>
<div><label>Overall decision</label><select id="reviewStatus"><option>PENDING</option><option>ACCEPT</option><option>REJECT</option></select></div>
<div><label>Reviewer name</label><input id="reviewerName" style="width:100%" placeholder="Required for export"></div>
<div><label>Identity decision</label><select id="identity"><option>PENDING</option><option>ACCEPT</option><option>REJECT</option></select><textarea id="identityNote" placeholder="What in the audio confirms or rejects identity?"></textarea></div>
<div><label>Boundary decision</label><select id="boundary"><option>PENDING</option><option>ACCEPT</option><option>REJECT</option></select><textarea id="boundaryNote" placeholder="Confirm the incoming and outgoing speaker handoffs."></textarea></div>
<div class="full"><label>Semantic decision</label><select id="semanticStatus"><option>PENDING</option><option>ACCEPT</option><option>REJECT</option></select></div>
<div class="full"><label>Semantic findings JSON — edit or delete every unsupported draft</label><textarea id="semantic"></textarea><div class="muted">Each accepted descriptive finding needs kind, finding, and scope. Meeting advice is not required. If a legacy meeting_phase is retained, it must be open, bring, run_the_room, or close.</div></div>
<div class="full attest"><label style="color:var(--gold)"><input type="checkbox" id="attest"> I personally listened to the review audio, checked both speaker handoffs, and reviewed every retained semantic claim in context.</label></div>
<div class="full" id="validation"></div>
</div></div></main>
<script>
const ROWS={payload};
const QUEUE_SHA={json.dumps(queue_sha256)},DRAFT_SHA={json.dumps(draft_sha256)};
const STORE='member-longform-review:'+{json.dumps(content_key)};
const POSITION_STORE=STORE+':position',REVIEWER_STORE=STORE+':reviewer';
let saved=JSON.parse(localStorage.getItem(STORE)||'{{}}'),index=Math.max(0,Math.min(ROWS.length-1,Number(localStorage.getItem(POSITION_STORE)||0)));
const $=id=>document.getElementById(id), fields=['reviewStatus','reviewerName','identity','identityNote','boundary','boundaryNote','semanticStatus','semantic','attest'];
function esc(s){{return String(s??'').replace(/[&<>\"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]))}}
function rec(r){{const s=saved[r.turn_id];return s&&s.binding===r.candidate_turn_binding_sha256?s:null}}
function seed(r){{return {{reviewStatus:'PENDING',reviewerName:localStorage.getItem(REVIEWER_STORE)||'',identity:'PENDING',identityNote:r.draft_identity_note,boundary:'PENDING',boundaryNote:r.draft_boundary_note,semanticStatus:'PENDING',semantic:JSON.stringify(r.draft_semantic_findings,null,2),attest:false}}}}
function currentDecision(){{let d={{}};for(const f of fields)d[f]=$(f).type==='checkbox'?$(f).checked:$(f).value;return d}}
function updateProgress(){{const done=ROWS.filter(r=>['ACCEPT','REJECT'].includes((rec(r)||{{}}).reviewStatus)).length;$('progress').textContent=`${{done}} decided · ${{ROWS.length-done}} pending`}}
function show(i){{if(!ROWS.length){{$('card').hidden=true;$('empty').hidden=false;return}};index=(i+ROWS.length)%ROWS.length;localStorage.setItem(POSITION_STORE,String(index));const r=ROWS[index],d=rec(r)||seed(r);if(!d.reviewerName)d.reviewerName=localStorage.getItem(REVIEWER_STORE)||'';
 $('member').textContent=r.member;$('position').textContent=`${{index+1}} / ${{ROWS.length}}`;
 updateProgress();
 $('meta').innerHTML=`<span class=pill>${{esc(r.source_era)}}</span><span class=pill>${{Number(r.duration_seconds).toFixed(1)}} sec</span><span class=pill>${{esc(r.consecutive_acoustic_windows)}} continuity windows</span>`;
 $('source').innerHTML=`${{/^https?:\\/\\//i.test(r.url||'')?`<a href="${{esc(r.url)}}" target=_blank rel=noopener>${{esc(r.title||r.url)}}</a>`:esc(r.title||'')}} · ${{esc(r.channel||'')}}`;
 $('audio').src=r.review_audio_uri||'';$('audioHint').textContent=`Name cue begins at ${{r.identity_cue_start_in_review_audio_sec??'?'}}s; turn begins at ${{r.turn_start_in_review_audio_sec??'?'}}s and ends at ${{r.turn_end_in_review_audio_sec??'?'}}s in this review file. SHA-256: ${{r.review_audio_sha256||'missing'}}`;
 const c=r.selected_identity_cue||{{}};$('cue').textContent=`[${{c.start_sec??'?'}}–${{c.end_sec??'?'}}s] ${{c.phrase||'missing'}} · first: ${{c.first_match||'exact'}} · surname similarity: ${{c.surname_similarity??'n/a'}} · cue-to-turn gap: ${{r.identity_cue_to_turn_gap_seconds??'?'}}s`;
 $('before').textContent=r.preceding_context;$('turn').textContent=r.turn_transcript;$('after').textContent=r.following_context;
 for(const f of fields){{if($(f).type==='checkbox')$(f).checked=!!d[f];else $(f).value=d[f]??''}}validate(false)}}
function semanticProblems(fs){{const problems=[];if(!Array.isArray(fs)||!fs.length)return ['at least one semantic finding'];for(const f of fs){{if(!f||typeof f!=='object'||Array.isArray(f)){{problems.push('finding object');continue}}for(const k of ['kind','finding','scope'])if(!String(f[k]||'').trim())problems.push('finding '+k);if(f.meeting_phase!=null&&!['open','bring','run_the_room','close'].includes(f.meeting_phase))problems.push('valid meeting_phase')}}return problems}}
function validate(showMessage=true){{const r=ROWS[index],d=currentDecision(),problems=[];if(!d.reviewerName.trim())problems.push('reviewer name');if(!d.attest)problems.push('attestation');for(const k of ['wav_sha256','transcript_sha256','candidate_turn_binding_sha256'])if(!/^[0-9a-f]{{64}}$/i.test(r[k]||''))problems.push('source binding '+k);
 if(!['ACCEPT','REJECT'].includes(d.reviewStatus))problems.push('overall decision');
 if(d.reviewStatus==='ACCEPT'){{for(const [k,label] of [['identity','identity'],['boundary','boundary'],['semanticStatus','semantic']])if(d[k]!=='ACCEPT')problems.push(label+' ACCEPT');if(!d.identityNote.trim())problems.push('identity note');if(!d.boundaryNote.trim())problems.push('boundary note');try{{problems.push(...semanticProblems(JSON.parse(d.semantic)))}}catch(e){{problems.push('valid semantic JSON')}}}}
 $('validation').className=problems.length?'status-bad':'status-ok';$('validation').textContent=showMessage?(problems.length?'Missing: '+[...new Set(problems)].join(', '):'Review is export-ready.') : '';return problems}}
function stash(){{if(!ROWS.length)return;const r=ROWS[index],d=currentDecision();saved[r.turn_id]={{...d,binding:r.candidate_turn_binding_sha256}};localStorage.setItem(STORE,JSON.stringify(saved));localStorage.setItem(POSITION_STORE,String(index));if(d.reviewerName.trim())localStorage.setItem(REVIEWER_STORE,d.reviewerName.trim());updateProgress()}}
function save(showMessage=true){{stash();validate(showMessage)}}
function nextPending(){{save(false);for(let step=1;step<=ROWS.length;step++){{const i=(index+step)%ROWS.length;if(!['ACCEPT','REJECT'].includes((rec(ROWS[i])||{{}}).reviewStatus)){{show(i);return}}}}alert('Every review has a decision.')}}
function exportRows(requireComplete){{
 save(false);
 const undecided=ROWS.filter(r=>!['ACCEPT','REJECT'].includes((rec(r)||{{}}).reviewStatus));
 if(requireComplete&&undecided.length){{
  alert(`${{undecided.length}} reviews are still pending. Use 'Next pending' to finish them, or export a progress backup.`);
  show(ROWS.indexOf(undecided[0]));return
 }}
 const output=[],bad=[];
 for(const r of ROWS){{
  const d=rec(r);if(!d||!['ACCEPT','REJECT'].includes(d.reviewStatus))continue;
  const old=index;index=ROWS.indexOf(r);
  for(const f of fields){{if($(f).type==='checkbox')$(f).checked=!!d[f];else $(f).value=d[f]??''}}
  const problems=validate(false);index=old;
  if(problems.length){{bad.push(r.member+': '+[...new Set(problems)].join(', '));continue}}
  const row={{schema:'member-longform-human-review-v1',turn_id:r.turn_id,member:r.member,source_id:r.source_id,wav_sha256:r.wav_sha256,transcript_sha256:r.transcript_sha256,candidate_turn_binding_sha256:r.candidate_turn_binding_sha256,source_era:r.source_era,start_sec:r.start_sec,end_sec:r.end_sec,review_status:d.reviewStatus,reviewer_type:'HUMAN',reviewer_name:d.reviewerName.trim(),reviewed_at:new Date().toISOString(),review_attestation:'personally_listened_and_reviewed_identity_boundaries_and_semantics'}};
  if(d.reviewStatus==='ACCEPT')Object.assign(row,{{identity_review:d.identity,identity_review_note:d.identityNote.trim(),boundary_review:d.boundary,boundary_review_note:d.boundaryNote.trim(),semantic_review:d.semanticStatus,semantic_findings:JSON.parse(d.semantic)}});
  output.push(row)
 }}
 show(index);
 if(bad.length){{alert('Fix before export:\\n'+bad.slice(0,20).join('\\n'));show(index);return}}
 if(!output.length){{alert('No completed decisions to export.');return}}
 const text=output.map(x=>JSON.stringify(x)).join('\\n')+'\\n',blob=new Blob([text],{{type:'application/x-ndjson'}}),a=document.createElement('a');
 a.href=URL.createObjectURL(blob);a.download=requireComplete?'member_longform_human_reviews_v1.jsonl':'member_longform_human_reviews_progress_v1.jsonl';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)
}}
$('prev').onclick=()=>{{save(false);show(index-1)}};$('next').onclick=()=>{{save(false);show(index+1)}};$('nextPending').onclick=nextPending;$('save').onclick=()=>save(true);$('exportProgress').onclick=()=>exportRows(false);$('export').onclick=()=>exportRows(true);$('find').onclick=()=>{{const q=$('search').value.trim().toLowerCase(),i=ROWS.findIndex(r=>r.member.toLowerCase().includes(q));if(i>=0){{save(false);show(i)}}}};$('search').onkeydown=e=>{{if(e.key==='Enter')$('find').click()}};for(const f of fields)$(f).addEventListener('input',()=>{{stash();validate(false)}});addEventListener('beforeunload',stash);show(index);
</script></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--drafts", type=Path, default=DEFAULT_DRAFTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    args = parser.parse_args()
    rows = review_rows(args.queue, args.drafts)
    queue_sha = SMR.sha256_file(args.queue)
    draft_sha = SMR.sha256_file(args.drafts)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_name(args.out.name + ".tmp")
    temporary.write_text(render(rows, queue_sha, draft_sha), encoding="utf-8", newline="\n")
    temporary.replace(args.out)
    receipt = {
        "schema": "member-longform-review-app-receipt-v1",
        "status": "PASS_REVIEW_SURFACE_ONLY",
        "generated_at": utc_now(),
        "counts": {"reviewable_turns": len(rows), "members": len({row["member"] for row in rows})},
        "contract": {
            "automated_decisions": False,
            "preaccepted_decisions": False,
            "explicit_listen_attestation_required_for_export": True,
            "export_is_compatible_with_materializer": True,
            "page_itself_grants_profile_authority": False,
        },
        "inputs": {"queue_sha256": queue_sha, "drafts_sha256": draft_sha},
        "output": {"path": str(args.out.resolve()), "sha256": SMR.sha256_file(args.out)},
    }
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(receipt["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
