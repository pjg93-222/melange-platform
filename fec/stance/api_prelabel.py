# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""S1 — API pre-label pass over gold_label_queue.csv (user-directed 2026-07-01: "you review once with
an API, and then I will go over it"). Calls Claude (claude-opus-4-8, adaptive thinking, structured
JSON output) once per queue row and fills the api_* columns; the gold_* columns are pre-seeded with
the API verdicts so the USER can correct in place. stance_eval.py can then score BOTH the legacy
DEBATE column (model_stance) and this pass (api_cu_alignment) against the corrected gold.
Resume-safe: rows that already have api_cu_alignment are skipped. Requires ANTHROPIC_API_KEY."""
import csv, io, json, sys, threading, collections
from concurrent.futures import ThreadPoolExecutor, as_completed
import anthropic
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
QUEUE = B + r'\data\stance\gold_label_queue.csv'
MODEL = "claude-opus-4-8"
WORKERS = 8

SYSTEM = """You label press items for a credit-union (CU) legislative-stance model. Each item is a press
release or news article associated with a member of Congress. You see only metadata and the headline —
judge from those; when the headline alone is ambiguous, say so via low confidence rather than guessing hard.

Work through the factorization, in order:
1. TARGET — the specific thing the text takes a position on (a bill, agency, rule, practice, or event).
   If the item does not express any stance connected to the member (ceremonial, generic constituent news,
   or the member is only incidentally mentioned), the item is UNRELATED.
2. STANCE TOWARD TARGET — does the ACTOR support, oppose, or stay neutral on that target?
3. TARGET'S CU VALENCE — is the target good (+1), bad (-1), or neutral (0) FOR CREDIT UNIONS?
   Fixed priors for this model (institutional positions of the credit-union movement):
   * Expanding CFPB power/funding/enforcement reach = -1; restraining or reforming CFPB (bipartisan
     commission, appropriations oversight) = +1.
   * Interchange caps, the Credit Card Competition Act, credit-card rate/fee mandates = -1;
     blocking or repealing them = +1.
   * Measures favoring large banks over CUs, or attacks on the CU tax status = -1;
     reinforcing the CU tax status, CU charter/field-of-membership expansion = +1.
   * Fraud prevention, data-privacy standards CUs seek, CDFI funding, defense-CU access = +1.
4. CU ALIGNMENT — the member's alignment as evidenced by THIS item:
   stance(actor toward target) x valence(target for CUs). Supporting an anti-CU target = oppose.
   Opposing an anti-CU target = support. Neutral stance or neutral target = neutral.
5. ATTRIBUTION — whose position does this item evidence?
   * member-own: the member's own press release / their quoted words.
   * reported-action: a third party (e.g. America's Credit Unions) reporting an ACTION the member took
     (sponsored, voted, withdrew an amendment). The alignment is about the member's ACTION, not the
     article's tone. E.g. "Advocacy win: Senator X will not offer the amendment" = the member withdrew
     an anti-CU amendment -> weak support, NOT the article's celebratory tone.
   * third-party-voice: the text's stance belongs to someone other than the member (an advocacy group's
     opinion, an opponent's attack). If the member's own position cannot be inferred, alignment=unrelated.
Confidence: high = headline clearly establishes actor, target, and direction; medium = probable but one
element inferred; low = headline insufficient (prefer neutral/unrelated + low over a guessed direction).
Keep rationale to one sentence."""

SCHEMA = {
    "type": "object",
    "properties": {
        "target": {"type": "string", "description": "the specific bill/agency/practice the stance is about, or 'none'"},
        "stance_toward_target": {"type": "string", "enum": ["support", "oppose", "neutral", "unrelated"]},
        "target_cu_valence": {"type": "integer", "enum": [1, 0, -1]},
        "cu_alignment": {"type": "string", "enum": ["support", "oppose", "neutral", "unrelated"]},
        "attribution": {"type": "string", "enum": ["member-own", "reported-action", "third-party-voice"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "rationale": {"type": "string"}
    },
    "required": ["target", "stance_toward_target", "target_cu_valence", "cu_alignment",
                 "attribution", "confidence", "rationale"],
    "additionalProperties": False,
}

client = anthropic.Anthropic()
lock = threading.Lock()

def label(row):
    dom = ('americascreditunions.org (the CU trade association itself)' if row['source_channel'] == 'acu'
           else ("the member's own congressional website" if row['source_channel'] == 'own-domain'
                 else 'a third-party outlet'))
    user = (f"Member: {row['member']} ({row['party']}, source: {dom})\n"
            f"Date: {row['date']}\nPress categories: {row['categories']}\n"
            f"Headline: {row['title']}")
    resp = client.messages.create(
        model=MODEL, max_tokens=3000,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    txt = next(b.text for b in resp.content if b.type == "text")
    return json.loads(txt), resp.usage

rows = list(csv.DictReader(open(QUEUE, encoding='utf-8-sig')))
todo = [r for r in rows if not (r.get('api_cu_alignment') or '').strip()]
print('queue rows: %d | to label: %d | model: %s' % (len(rows), len(todo), MODEL))
done = 0; errs = 0
usage_in = usage_out = 0

def work(r):
    try:
        out, u = label(r)
        return r['id'], out, u, None
    except Exception as e:
        return r['id'], None, None, str(e)[:200]

byid = {r['id']: r for r in rows}
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futs = [ex.submit(work, r) for r in todo]
    for f in as_completed(futs):
        rid, out, u, err = f.result()
        with lock:
            done += 1
            r = byid[rid]
            if err:
                errs += 1; r['api_rationale'] = 'ERROR: ' + err
            else:
                r['api_target'] = out['target']
                r['api_stance_toward_target'] = out['stance_toward_target']
                r['api_target_cu_valence'] = str(out['target_cu_valence'])
                r['api_cu_alignment'] = out['cu_alignment']
                r['api_attribution'] = out['attribution']
                r['api_confidence'] = out['confidence']
                r['api_rationale'] = out['rationale']
                r['debate_vs_api'] = '' if (r.get('model_stance') or '').strip() == out['cu_alignment'] else 'DISAGREE'
                # pre-seed the gold columns for correction-in-place (only if still empty)
                for g, a in (('gold_target', 'api_target'), ('gold_stance_toward_target', 'api_stance_toward_target'),
                             ('gold_target_cu_valence', 'api_target_cu_valence'),
                             ('gold_cu_alignment', 'api_cu_alignment'), ('gold_attribution', 'api_attribution')):
                    if not (r.get(g) or '').strip(): r[g] = r[a]
                usage_in += u.input_tokens
                usage_out += u.output_tokens
            if done % 25 == 0 or done == len(todo):
                print('  %d/%d labeled (%d errors)' % (done, len(todo), errs), flush=True)

cols = list(rows[0].keys())
with io.open(QUEUE, 'w', encoding='utf-8-sig', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)
al = collections.Counter(r.get('api_cu_alignment') for r in rows if r.get('api_cu_alignment'))
cf = collections.Counter(r.get('api_confidence') for r in rows if r.get('api_confidence'))
dis = sum(1 for r in rows if r.get('debate_vs_api') == 'DISAGREE')
att = collections.Counter(r.get('api_attribution') for r in rows if r.get('api_attribution'))
print('\nDONE. errors: %d | tokens in/out: %d/%d (~$%.2f)' % (errs, usage_in, usage_out,
      usage_in / 1e6 * 5 + usage_out / 1e6 * 25))
print('api alignment dist:', dict(al))
print('api confidence:', dict(cf), '| attribution:', dict(att))
print('DEBATE vs API disagreements: %d / %d (%.0f%%)' % (dis, len(rows), 100 * dis / max(1, len(rows))))
