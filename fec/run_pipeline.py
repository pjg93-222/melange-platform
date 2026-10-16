# -*- coding: utf-8 -*-
import os as _o,sys as _s
_s.path.insert(0,_o.path.dirname(_o.path.abspath(__file__)))
from paths import DRAFT as MB_DRAFT
from paths import ROOT as MB_ROOT
from paths import LOCAL_MELANGE

"""Canonical fail-closed Moneyball pipeline runner.

Python owns all derived math. ``compute_pillars.py --write`` produces ``ranks_python.json`` as the
official reporting-rank authority; primacy tiers anchor allocation priority
with a leave-one-topic-out CU-orientation adjustment, and the workbook is a
recalculating audit/view artifact. A single exclusive lock covers
the entire writer chain so two runs cannot publish a mixed-vintage artifact set.
"""
import argparse
import bounded_subprocess
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
import uuid
from bounded_subprocess import run_bounded
from pipeline_lock import pipeline_run_lock

sys.stdout.reconfigure(encoding='utf-8',errors='replace')
FE=os.path.dirname(os.path.abspath(__file__))

# Glass Box: every process emits to the shared stream or it is not acceptable.
# This chain used to write only to stdout, so a twenty-minute production run
# that rewrote the rank authority was invisible to the dashboard. Emission is
# best-effort by design -- glassbox_emit swallows its own failures so an
# unwritable event can never take down a pipeline run.
try:
    from surfaces.glassbox_emit import emit as _gb, new_run_id as _gb_run, new_correlation_id as _gb_corr
except Exception:
    def _gb(*a, **k): return None
    def _gb_run(prefix='moneyball'): return ''
    def _gb_corr(): return ''
_GB_RUN=_gb_run('moneyball-pipeline')
_GB_CORR=_gb_corr()

CHAIN=[
    # FIRST, deliberately: halt before ANY artifact is written if this run would change a published
    # calibration (2026-07-19 incident — a run from a branch missing a ratified ladder silently
    # reverted Ally 3.00 -> 2.50 with no error and no audit failure). Intended changes pass with
    # CALIB_ACK=1; the guard prints the exact command. It is a chain STEP rather than an audit_full
    # ck() because a ck() failure exits 1 and aborts the whole chain — a warning must not do that.
    ('calibration_guard.py',[]),
    ('apply_fec_money_refresh.py',[]),
    # Collection intake is a hard, hash-bound pre-publication gate.  Audio is queued only as a
    # candidate awaiting two independent reviews; press mentions remain relationship-only.
    ('audit_collection.py',['--output','data/engine/collection_intake_receipt.json']),
    ('build_press_engine_feed.py',[]),
    ('b5_audio_manifest_adapter.py',[]),
    ('b5_audio_descriptors_candidate.py',[
        '--review','data/members/b5_audio_reviews_v2.json.candidate',
        '--out','data/members/b5_audio_descriptors_v2.json.candidate']),
    # D-013 descriptive audio lane: prepares the complete accepted manifest,
    # measures missing clips, and emits a no-engine artifact for the surfaces.
    # Repointed 2026-09-02 at the cumulative source-media lane.  The bare defaults
    # still name the 28-clip v2 set sealed 08-30 02:06, which two sibling producers
    # invalidated within the day (calibration rewritten 02:18, inventory 17:44) and
    # which predates the final-receipt vocabulary change -- so the chain form of this
    # step had been failing "verified final receipt is stale or incomplete" since
    # 08-30, unnoticed because the collection gate aborted four steps earlier.  These
    # are the paths the last successful build used (08-31 01:29, 1847 verified
    # finals); the manifest and receipt hashes match that build byte for byte.
    ('build_descriptive_speech.py',[
        '--verified-manifest','data/speech/source_media_verified_cumulative_exact_windows_v6.jsonl',
        '--verified-receipt','data/speech/source_media_verified_cumulative_exact_windows_v6.receipt.json',
        '--identity-manifest','data/speech/source_media_identity_cumulative_all_verified_v5.jsonl',
        '--identity-receipt','data/speech/source_media_identity_cumulative_all_verified_v5.receipt.json',
        '--candidate-manifest','data/speech/source_media_candidates_cumulative_all_verified_v5.jsonl',
        '--candidate-receipt','data/speech/source_media_candidates_cumulative_all_verified_v5.receipt.json',
        '--source-media-inventory','data/speech/source_media_inventory_cumulative_expanded_v3_snapshot.jsonl',
        '--speaker-calibration','data/speech/speaker_anchor_calibration_all_verified_v3.json',
    ]),
    ('coop_v2_candidate.py',[]),
    ('build_electoral.py',[]),
    # COMPET_MULT (Paul 2026-10-05, default ON 2026-10-06): the Cook-PVI competitiveness multiplier c. Writes data/money/competitiveness_c.json (deterministic,
    # no timestamps), which every flip-family engine below reads through moneyball_core.cmult unless COMPET_MULT=0. A build failure is FATAL by default
    # (the engines would refuse a missing artifact anyway) and non-fatal only with COMPET_MULT=0. Rank-neutral: RA_SCORE ranks do not move.
    # receptivity_layer.py (two steps below) writes pressure_mult_compet when the dial is on (Paul ruling 2026-10-07, D-107 R1); the engines refuse a receptivity file without it.
    ('build_competitiveness.py',[]),
    ('build_district_industry.py',[]),
    ('receptivity_layer.py',[]),
    # Same-cycle bill transaction: verify the official repository, persist
    # canonical bill IDs + review state into Excel, then let archive scoring
    # and labels consume that worksheet.  This must precede every legislative
    # downstream producer.
    # Verified collector -> ledger gate (2026-08-29 Paul). Consumes the previous-cycle
    # member_desk_bundle exactly like champions_v2/industry_tiers, plus the weekly
    # Congress.gov/GovInfo bill repository and its refresh receipt, and publishes the
    # additive cosponsor-join sidecar make_member_desk merges as its fourth membership
    # source. Local files only -- the chain's network-free law holds. It runs BEFORE
    # compute_pillars/build_primacy_surfaces so the same-cycle desk rebuild inside
    # build_primacy_surfaces --engine-inputs-only already sees the fresh sidecar.
    # Its refusal set is a strict SUBSET of the validate_live_repository gate that
    # make_member_desk already trips at import, so this step can only ever halt a chain
    # that was going to halt anyway -- and it halts it before compute_pillars --write.
    ('verify_collector_roster_updates.py',[]),
    # Runtime-absent is a CLEAN SKIP, not a failure: the sync keeps the last-good ledger
    # and records status SKIPPED_RUNTIME_ABSENT in its receipt. That skip cannot go
    # unnoticed -- audit_full's sentinel_ledger_freshness check ages it out at 21 days.
    ('sync_sentinel_bill_ledger.py',[]),
    ('build_archive_legislative.py',[]),
    ('extract_cu_speeches.py',[]),
    ('speech_stance_stack.py',[]),
    ('hearing_stance_stack.py',[]),
    ('extract_cu_interviews.py',[]),
    ('interview_stance_stack.py',[]),
    ('bluesky_stance_stack.py',[]),
    ('tweet_stance_stack.py',['--score']),
    ('press_stance_stack.py',[]),
    ('press_engagement_layer.py',[]),
    ('press_hcoi.py',[]),
    # Rebuild the live augmented cascade only after both fresh press-derived layers exist.
    ('build_multilayer_network.py',[]),
    ('member_reference_layer.py',['--rebuild']),
    # Remove the last bound recognition projection before the first analytical
    # workbook writer. Preparing after media blending would reject its legitimate
    # edits, or erase them if the binding protection were bypassed.
    ('honors_workbook.py',['--prepare-model-update']),
    ('build_media_blend.py',[]),
    # Rebuild votes from the frozen, hash-bound source snapshot before Path V.
    # Source downloads remain a separate explicit refresh; stale evidence fails.
    ('build_vote_category.py',['--publish']),
    ('per_category_stance.py',[]),
    # Committee score is a DERIVED column, and until 2026-09-18 nothing in this chain derived
    # it. B/C/D on 'Committee Data' are CACHED VALUES, not formulas, so a tier marker edited in
    # column E -- Paul's prerogative -- left the old weight standing, and compute_pillars, which
    # reads column B as Influence at 40% of the composite, then published a rank that
    # contradicted the seat text printed beside it on the member desk. Not hypothetical:
    # retier_committee.py moved 44 Veterans' Affairs markers and the lane repaired 92 cells by
    # hand (audits/members_section_20260914/evidence/committee-score-recompute.md).
    # It sits HERE, immediately before the first consumer of column B, so every later reader
    # (build_dashboard_bundle, make_member_desk, audit_full) sees the same cycle's values; no
    # earlier chain step writes this sheet. It saves the workbook ONLY when a cell actually
    # changes, so on a clean tree it is a no-op in the literal sense -- not one byte moves --
    # and when the seat text cannot be priced at all it halts the chain BEFORE --write rewrites
    # the rank authority.
    ('committee_scores.py',['--write']),
    ('compute_pillars.py',['--write']),
    # Bind the preserved adjudication decisions to this generation's rank hash.
    ('legislation_culling_status.py',[]),
    # Per-area primacy grade (D-031, PRIMACY_BOARD=per_area_v1) USED to run here,
    # ahead of the step below. It no longer does: its rankable set and its
    # provenance hashes are taken from primacy_factors.json, which the very next
    # step rebuilds, so a standalone slot here made the grade lag a full cycle
    # (2026-09-17: the grade recorded a factor hash that build_primacy_surfaces
    # overwrote 2.5 hours later in the same run). It is now the LAST entry of
    # build_primacy_surfaces.ENGINE_INPUT_STEPS, immediately after
    # engine/primacy_factors.py -- same chain, same cycle, correct order. It still
    # reads the routing audit (:102), the three stance stacks (:108-110) and the
    # republished vote lean (:123), all of which are final well before this point.
    # Primacy is an allocation-engine input. Build its beneficiary factor and
    # conduct-adjusted tier before any engine imports moneyball_core.
    ('build_primacy_surfaces.py',['--engine-inputs-only']),
    # Display-only CU ask take-up/capacity context.  It consumes the pre-chain
    # desk bundle plus the just-refreshed primacy factors, and must exist before
    # the final member desk and lobbying-board surfaces render it.
    # D5 (2026-09-16): the desk section it fed is retired, so DESK_RESPONSIVENESS=0
    # (the default) DROPS this step from the chain below.  The producer stays in the
    # tree and stays reproducible; DESK_RESPONSIVENESS=1 restores both the step and
    # the desk card.  Its only other consumer, make_lobby_network_html.py, treats the
    # artifact as optional by construction (:4414 os.path.isfile guard), so a chain
    # that never writes it is not a failure there.
    ('responsiveness_layers.py',[]),
    # Honors are an evidence-qualified display projection. They must see the
    # completed overall board and never feed the analytical stance engines.
    ('build_rank_honors.py',['--publish']),
    ('audit_rank_honors.py',[]),
    ('sync_stance_g.py',[]),
    ('honors_workbook.py',[]),
    ('build_electoral_plays.py',[]),
    ('per_topic_flip.py',[]),
    ('per_topic_coalition.py',[]),
    ('lt_priming.py',[]),
    ('budget_frontier.py',[]),
    ('budget_frontier.py',['--electoral']),
    ('optimize.py',[]),
    ('build_im_allocation.py',[]),
    ('salience_shock.py',['--advise']),
    ('personal_texture_layer.py',['--refresh-local']),
    ('communication_style.py',[]),
    ('validate_communication_style.py',[]),
    ('b5_external_gold_validate.py',[]),
    ('b5_gold_multitrait_validate.py',[]),
    ('b5_profile_basis.py',[]),
    ('validate_guarded_profile_artifacts.py',[]),
    # Both surfaces require the current basis and its evidence-bound profiles.
    # Keep the ratified long-form profile template, re-run its current-evidence
    # claim gates, and rebuild the complementary meeting-playbook layer.
    ('build_member_longform_turn_features.py',['--if-current']),
    ('build_member_narrative_ledger.py',[]),
    ('regenerate_member_profiles.py',['--activate']),
    ('build_dashboard_bundle.py',[]),
    ('make_dashboard_html.py',[]),
    # Coalition access price + majority-neutral capability for the builder's Underpriced basis.
    # Decision-layer only (like dashboard_legislative_workbench): reads congestion/say-do/LEP/
    # dispositions and the LDA attention window, writes coalition_pricing.json, and moves no rank.
    # Must precede make_member_desk.py, which folds it into the desk bundle.
    ('coalition_pricing.py',[]),
    # 2026-08-05 (Paul): the DESK renders LAST — it is the face, the display —
    # with the lobbying network generated before it. Order: network surfaces,
    # then the bill-lobbying sidecar, then the desk that folds everything in,
    # then the combine, then the audit.
    #
    # Industry/lobbying surface. Emits lobby-network.html, which make_combined.py
    # folds into the combined dashboard -- so it must precede make_combined.py or the
    # combine step merges a stale surface.
    # CU x fintech under-contested screen (Paul 2026-08-05): descriptive
    # residual lens from the round-3 look, rendered by the industry page.
    # Screen, not a score - no engine or rank reads it.
    ('contest_screen.py',[]),
    # 2026-08-06 Paul: direction-aware boards replace the residual-attention headline.
    ('champions_v2.py',[]),
    # Contest-adjusted investment and lobbying efficiency; decision layer only, moves no rank.
    # 2026-08-09 Paul: moved AFTER champions_v2 so the ADVERSARY exclusion consumes the
    # same-cycle boards (champion crown law) and the same-cycle coalition_pricing artifact;
    # both were one-cycle lagged at the old pre-dashboard slot. Safe since the desk-tab
    # move (d07eaea): the dashboard no longer reads efficiency output — its consumer is
    # make_member_desk, which renders later.
    ('efficiency_layers.py',[]),
    # 2026-08-10: consumes the previous-cycle member_desk_bundle exactly like champions_v2
    # (the documented bundle producer/consumer cycle), and hard-fails unless its credit-union
    # instance reproduces the desk bundle's tiers exactly. make_lobby_network_html.py hard-requires
    # data/lobbying/industry_tiers.json, so this must precede that step.
    ('industry_tiers.py',[]),
    ('org_mentions.py',[]),
    # Cosponsorship forecast freshness, immediately before its first consumer
    # (make_lobby_network_html.py); the sidecar and desk follow.
    # DIAL: COSPONSOR_FORECAST_REFRESH — unset/0 writes a SKIPPED_DIAL_OFF receipt and
    # returns, so the default chain is byte-for-byte what it was before this step existed.
    # GATE: re-hashes cosponsor_forecast_report.json's provenance.every_input_sha256 and
    # globs for unlisted BILLSTATUS zips; it re-runs the forecaster (~16 min, ~1.1 GB RSS)
    # ONLY when an input actually moved, and promotes atomically. Network-free: the
    # forecaster reads local files only, so the chain's network-free law holds.
    ('refresh_cosponsor_forecast.py',[]),
    ('make_lobby_network_html.py',[]),
    # Interactions surface. Emits lobby-interactions.html, which make_combined.py deliberately
    # does NOT merge (it stays a real navigation target). It is chained so the surface stops
    # going stale silently. Its LEDGER producer (fec/lobbying/lobby_interactions.py) is
    # deliberately NOT chained (position classification, expensive); the ledger stays a manual
    # refresh and this step consumes whatever ledger is current.
    ('make_lobby_interactions_html.py',[]),
    # Staffer network, the fourth top-level surface of Moneyball.html (2026-09-04).
    # Two steps, both OPTIONAL by the same contract as verify_collector_roster_updates.py:
    # the producer reads a receipt-gated staffer social-network generation under
    # audits/staffer_network/, which a fresh or data-isolated tree simply does not have, and
    # the html builder then has no section to render. Absence is a clean SKIP; a generation
    # that is PRESENT but unverifiable still refuses loudly (the producer binds every byte it
    # reads to BOTH the generation receipt and the independent verification receipt).
    # Rank-neutral by construction: the producer writes only
    # data/engine/staffer_network_section.json and the builder only staffer-network.html;
    # no engine artifact is read or written. They sit here, before the desk and the combine,
    # for the same reason the industry surfaces do -- make_combined.py folds the page in and
    # refuses if it is older than its section.
    # Duration-attributed rankings with complete lists and explicit ties measured
    # 5,393,532 bytes on the full 2026-09-08 comparison. Keep a 6 MB JSON cap;
    # the rendered-page cap is checked independently by its producer.
    ('build_staffer_network_section.py',['--max-bytes','6000000']),
    # The Groups artifacts (owner ruling D-022): offices, organisations and clients, THREE
    # files written by one --part both run. Keep the existing 5 MB cap for each group
    # artifact; the network section above has its own 6 MB budget. The all-or-nothing rule means a
    # part that refuses the cap leaves the whole set alone rather than shipping a page assembled
    # from one fresh part and two stale ones. It runs HERE, after the section producer (it reads
    # that same verified generation) and before the page builder (which renders all three).
    ('build_staffer_groups_section.py',['--part','both','--max-bytes','5000000']),
    # The page builder still runs fine with NO arguments -- nothing below is needed to find the
    # data, which it reads from data/engine by default. The two switches choose which SURFACE
    # ships: _01 is the simplified Groups surface D-022 asked for, and --sdata provenance keeps
    # the ~4 MB member map (which _01 renders none of) off the page. --template is redundant
    # with the default as of 2026-09-07 (make_staffer_network_html.DEFAULT_TEMPLATE flipped to
    # _01 here -- a bare run used to rebuild the OLD network surface and silently revert the
    # shipped page on the next chain run) but stays explicit anyway: a chain step that names its
    # own output cannot be silently changed by some future default edit, which is exactly the
    # failure this comment used to describe. --sdata has no such default (it is still 'full')
    # and must stay explicit regardless.
    ('make_staffer_network_html.py',
     ['--template','make_staffer_network_html_01.tpl','--sdata','provenance']),
    # Bill-level lobbying sidecar. The desk folds it into the repository; the
    # basename resolver owns the producer path, like every other chain step.
    ('bill_lobbying_sidecar.py',[]),
    # The engine-input half already ran before allocation. Rebuild the final
    # evidence-bearing desk after the profile layers AND the lobbying surfaces finish.
    ('make_member_desk.py',[]),
    ('make_combined.py',[]),
    ('audit_rank_honors.py',['--surfaces']),
    ('sync_live_dashboard_alias.py',[]),
    ('make_email_package.py',[]),
    ('audit_source_to_dashboard.py',[]),
    ('audit_full.py',[]),
]

# D5 -- DESK_RESPONSIVENESS (packet audits/members_section_20260914 §D5).  The desk
# responsiveness card is retired by default, so the step that builds its input is not
# run.  Filtering here rather than deleting the entry keeps the step, its placement and
# its comment intact for the revert switch, and keeps CHAIN a plain literal that a test
# can read.  build_system_manifest.py:517 keeps responsiveness_layers.json in
# sentinel_excl either way, so a stale artifact left on disk raises no manifest failure.
DESK_RESPONSIVENESS=os.environ.get('DESK_RESPONSIVENESS','0')=='1'
if not DESK_RESPONSIVENESS:
    CHAIN=[_step for _step in CHAIN if _step[0]!='responsiveness_layers.py']

# build_descriptive_speech.py is TOLERANT: a display-only descriptive layer (its docstring forbids it
# from touching any engine value). It validates ten hash-bound speech-lane inputs and has blocked the
# whole chain on stale receipts (08-30, 09-04). Only exit code 2 -- the producer's own contract
# refusal (SpeechBuildError in its main) -- is tolerated: the failure logs, records a receipt, and the
# chain continues on the last-good artifact. A crash (any other nonzero exit) still aborts the chain.
# audit_full.py must remain the LAST chain entry (weekly_refresh chain-tail guard).
TOLERANT_STEPS={'build_descriptive_speech.py'}
# Per-run receipt of tolerated failures (a JSON list). Relative, resolved against MB_ROOT exactly like
# the OPTIONAL_INPUTS paths below; reset to [] once per run so it reports only that run's tolerances.
TOLERATED_RECEIPT='data/engine/chain_tolerated_failures.json'
# The artifact a tolerated step leaves in place, and the producer's own receipt for it. Tolerance
# REQUIRES the artifact to exist and be non-empty AND its receipt to parse with status PASS (the
# retained bytes must be ones the producer itself accepted); without both, the failure aborts the
# chain exactly as it always has.
LAST_GOOD={'build_descriptive_speech.py':'data/speech/speech_descriptive_v1.json'}
LAST_GOOD_RECEIPT={'build_descriptive_speech.py':'data/speech/speech_descriptive_v1.receipt.json'}


def _last_good_ok(path):
    """True only when the last-good artifact exists and is a non-empty regular file."""
    if not path:
        return False
    try:
        return os.path.isfile(path) and os.path.getsize(path)>0
    except OSError:
        return False


def _last_good_receipt_ok(path):
    """True only when the last-good artifact's own receipt exists, parses as JSON, and records
    status PASS -- i.e. the retained artifact is one the producer itself accepted."""
    if not path:
        return False
    try:
        with open(path,encoding='utf-8') as f:
            receipt=json.load(f)
    except (OSError,ValueError):
        return False
    return isinstance(receipt,dict) and receipt.get('status')=='PASS'


def _last_good_receipt_binds_artifact(receipt_path,artifact_path):
    """True only when the receipt's artifact_sha256 IS the digest of the retained artifact.
    status PASS alone does not establish the pairing: build_descriptive_speech.build() writes the
    artifact and the receipt as two separate atomic writes, so an interrupted run -- or any
    out-of-band edit of either file -- can leave a PASS receipt beside bytes the producer never
    accepted. Without this the chain "tolerates" an artifact that descriptive_contract.validate()
    then rejects at read time ("speech receipt is not bound to the current artifact")."""
    if not receipt_path or not artifact_path:
        return False
    try:
        with open(receipt_path,encoding='utf-8') as handle:
            receipt=json.load(handle)
    except (OSError,ValueError):
        return False
    if not isinstance(receipt,dict):
        return False
    expected=receipt.get('artifact_sha256')
    if not isinstance(expected,str) or len(expected)!=64:
        return False
    digest=hashlib.sha256()
    try:
        with open(artifact_path,'rb') as handle:
            for block in iter(lambda: handle.read(1024*1024),b''):
                digest.update(block)
    except OSError:
        return False
    return digest.hexdigest()==expected


def _record_tolerated(receipt_path,entry=None,reset=False):
    """Read-modify-write the tolerated-failure receipt (a JSON list) atomically: temp file + os.replace.
    reset=True starts from [] (each run reports only its own tolerated failures); entry, when given, is
    appended. A missing or unparseable receipt is treated as []. Returns the list that was written."""
    entries=[]
    if not reset and os.path.exists(receipt_path):
        try:
            with open(receipt_path,encoding='utf-8') as f:
                entries=json.load(f)
        except (OSError,ValueError):
            entries=[]
        if not isinstance(entries,list):
            entries=[]
    if entry is not None:
        entries.append(entry)
    parent=os.path.dirname(receipt_path)
    if parent:
        os.makedirs(parent,exist_ok=True)
    tmp='%s.%d.tmp'%(receipt_path,os.getpid())
    with open(tmp,'w',encoding='utf-8', newline='\r\n') as f:
        json.dump(entries,f,indent=2)
    os.replace(tmp,receipt_path)
    return entries


def _prune_tolerated(receipt_path,steps_in_slice,full_reset):
    """Start-of-run maintenance of the tolerated-failure receipt, with _record_tolerated's exact
    read/write semantics (a missing or unparseable receipt is []; temp file + os.replace).
    full_reset (a run from step 1) starts from []. Otherwise (a --from resume) only entries whose
    'step' is in steps_in_slice are dropped -- those steps are about to run again and re-record
    themselves if they fail again -- while entries for steps outside the slice are kept, since
    nothing in this run re-examines them. Entries that are not objects carry no attributable step
    and are dropped. Returns the list that was written."""
    entries=[]
    if not full_reset and os.path.exists(receipt_path):
        try:
            with open(receipt_path,encoding='utf-8') as f:
                entries=json.load(f)
        except (OSError,ValueError):
            entries=[]
        if not isinstance(entries,list):
            entries=[]
    drop=set(steps_in_slice)
    entries=[entry for entry in entries if isinstance(entry,dict) and entry.get('step') and entry.get('step') not in drop]
    parent=os.path.dirname(receipt_path)
    if parent:
        os.makedirs(parent,exist_ok=True)
    tmp='%s.%d.tmp'%(receipt_path,os.getpid())
    with open(tmp,'w',encoding='utf-8', newline='\r\n') as f:
        json.dump(entries,f,indent=2)
    os.replace(tmp,receipt_path)
    return entries

# 2026-08-05 Phase 3: this resolver is the single source of chain-path truth;
# CHAIN names are location-independent, and a missing or duplicated producer
# basename fails loudly at chain start. Only CHAIN members are producers;
# unrelated research helpers can legitimately share their filenames.
_chain_producer_names={os.path.basename(script) for script,_args in CHAIN}
_producer_paths={}
for _producer_dir,_producer_dirs,_producer_files in os.walk(FE):
    _producer_dirs[:]=sorted(
        _dir for _dir in _producer_dirs
        if _dir not in ('tests','attic','__pycache__')
        and not _dir.startswith('_quarantine')
    )
    for _producer_file in sorted(_producer_files):
        if _producer_file not in _chain_producer_names:
            continue
        _producer_full_path=os.path.join(_producer_dir,_producer_file)
        if _producer_file in _producer_paths:
            raise RuntimeError('duplicate producer basename %s: %s and %s'%
                               (_producer_file,_producer_paths[_producer_file],
                                _producer_full_path))
        _producer_paths[_producer_file]=_producer_full_path

def _producer_path(_producer_name):
    _producer_basename=os.path.basename(_producer_name)
    if _producer_basename not in _producer_paths:
        raise RuntimeError('chain producer script not found: %s'%_producer_name)
    return _producer_paths[_producer_basename]

for _chain_script,_chain_args in CHAIN:
    _producer_path(_chain_script)

# Steps whose input artifact may be absent in a fresh / data-isolated worktree.  When the input is
# missing we SKIP the step cleanly (no-op) instead of aborting the whole chain.  Both entries feed the
# CU floor-speech channel (Paul-approved 2026-07-15), which is gated OFF downstream (CU_SPEECH_CHANNEL),
# so skipping them can never move ranks.
_DEMO_DATA_ROOT=os.environ.get('DEMO_DATA_ROOT')
if not _DEMO_DATA_ROOT:
    _DEMO_DATA_ROOT=os.path.join(LOCAL_MELANGE,'demographics')
OPTIONAL_INPUTS={
    # The collector->ledger gate needs the previous desk bundle AND a completed weekly bill
    # scrape. In a fresh or data-isolated worktree those are absent, and a clean skip is
    # correct: no sidecar is written, so the desk simply assembles the ledger as it did before.
    # A PRESENT-but-unverifiable receipt still fails the step loudly -- absence is not failure.
    'verify_collector_roster_updates.py':(
        'member_desk_bundle.json',
        'bill_repository_live.json',
        'data/engine/congress_bill_refresh_receipt.json',
    ),
    'sync_sentinel_bill_ledger.py':(
        'member_desk_bundle.json',
        'bill_repository_live.json',
        'data/engine/congress_bill_refresh_receipt.json',
        'data/members/collector_roster_updates.ndjson',
        'data/members/collector_roster_pending.ndjson',
    ),
    # Staffer network surface (2026-09-04). Same shape as the two entries above: the producer
    # needs a verified staffer social-network generation, and the html builder needs the
    # section that producer writes. Absent -> clean skip, no artifact written, and
    # make_combined.py simply does not merge the panel. The generation name is the producer's
    # own GENERATION constant (fec/surfaces/build_staffer_network_section.py); the producer
    # ALSO clean-skips on its own when that directory is missing, so a rename here degrades to
    # a skip rather than a chain halt.
    'build_staffer_network_section.py':(
        'audits/staffer_network/staffer_social_network_20260908_v8_math_primary',
        'audits/staffer_network/staffer_social_network_20260908_v8_math_verification/'
        'staffer_social_network_verification_receipt.json',
    ),
    # The Groups producer needs strictly MORE than the section producer does: the same verified
    # social-network generation, plus the tiered corroboration sidecar it labels employers from,
    # plus the LDA source-native corpus -- which lives OFF-REPO under LOCALAPPDATA, exactly like
    # the demographics panel below, so a fresh or data-isolated tree legitimately has none of it.
    # All four are named here so that absence is a clean SKIP; the page builder then renders an
    # honest empty directory for each missing part rather than failing (make_staffer_network_html
    # EMPTY_OFFICES / EMPTY_ORGANISATIONS / EMPTY_CLIENTS). The generation names are the
    # producer's own GENERATION / SIDECAR_GENERATION / LDA_GENERATION constants.
    'build_staffer_groups_section.py':(
        'audits/staffer_network/staffer_social_network_20260908_v8_math_primary',
        'audits/staffer_network/staffer_social_network_20260908_v8_math_verification/'
        'staffer_social_network_verification_receipt.json',
        'audits/staffer_network/tiered_corroboration_sidecar_20260907_v3_primary',
        os.path.join(
            LOCAL_MELANGE,'staffer_network','normalized','lda-source-native',
            'generation-20260830T231200Z'),
    ),
    'make_staffer_network_html.py':'data/engine/staffer_network_section.json',
    'extract_cu_speeches.py':'social_media.db',   # sqlite source of the CREC floor speeches
    'speech_stance_stack.py':'speeches_cu.csv',   # produced by extract_cu_speeches.py
    # build_district_industry.py hard-requires the panel MANIFEST as well as the
    # panel itself (it validates schema + coverage against it). Without the
    # manifest listed here a half-collected DEMO_DATA_ROOT halts the whole chain
    # instead of skipping this step cleanly.
    'build_district_industry.py':(
        'data/district/district_geography.json',
        os.path.join(_DEMO_DATA_ROOT,'lodes','lodes_cd_panel.csv.gz'),
        os.path.join(_DEMO_DATA_ROOT,'lodes','lodes_panel_manifest.json'),
    ),
    'org_mentions.py':(
        'press_text_cache.jsonl',
        'social_media.db',
        'data/stance/x_catalog.jsonl',
        'Quorum Processed.xlsx',
        os.path.join(MB_DRAFT,'cu_press_stance_tone.csv'),
        os.path.join(MB_DRAFT,'cu_press_debate_in.csv'),
    ),
}

CANONICAL_DEPENDENCIES={
    'networkx':'networkx',
    'numpy':'numpy',
    'openpyxl':'openpyxl',
    'pandas':'pandas',
    'scikit-learn':'sklearn',
    'scipy':'scipy',
}


def preflight_dependencies():
    """Fail before any producer runs when the canonical environment is incomplete."""
    missing=[package for package,module in CANONICAL_DEPENDENCIES.items()
             if importlib.util.find_spec(module) is None]
    if missing:
        raise RuntimeError(
            'missing canonical Python dependencies: %s; run: "%s" -m pip install -r requirements.txt'
            % (', '.join(missing),sys.executable)
        )


def start_index(resume):
    """Resolve one exact/case-sensitive chain prefix or reject it loudly."""
    if resume is None:
        return 0
    matches=[i for i,(script,_args) in enumerate(CHAIN)
             if resume in (script,os.path.splitext(script)[0]) or script.startswith(resume)]
    if len(matches)!=1:
        choices=', '.join(os.path.splitext(script)[0] for script,_args in CHAIN)
        raise ValueError('unknown --from target %r; choose exactly one of: %s'%(resume,choices))
    return matches[0]


def _step_failure_metadata(script,argv,run):
    """Keep the child result distinct from the chain's eventual exit 1."""
    return {
        'schema':'pipeline-step-failure-v1',
        'captured_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
        'step':script,'argv':list(argv),'cwd':os.getcwd(),'pid':run.pid,
        'returncode':run.returncode,
        'returncode_u32_hex':'0x%08X'%(run.returncode & 0xffffffff),
        'native_returncode':None if run.timed_out else run.returncode,
        'timed_out':run.timed_out,'timeout_note':run.timeout_note,
        'timeout_seconds':bounded_subprocess.CHAIN_STEP_TIMEOUT_SECONDS,
        'elapsed_seconds':run.seconds,
        'stream_format':'Full UTF-8 text returned by bounded_subprocess; replacement decoding and universal newlines.',
    }


def _record_step_failure(directory,metadata,run):
    """Create a unique capture; callers report recording errors without changing the verdict."""
    directory=os.path.abspath(directory)
    os.makedirs(directory,exist_ok=True)
    capture=os.path.join(directory,'failure-'+uuid.uuid4().hex)
    os.mkdir(capture)
    receipt=dict(metadata)
    receipt['streams']={}
    for name,text in (('stdout',run.stdout or ''),('stderr',run.stderr or '')):
        filename=name+'.txt'
        with open(os.path.join(capture,filename),'x',encoding='utf-8',newline='') as handle:
            handle.write(text)
        encoded=text.encode('utf-8')
        receipt['streams'][name]={'file':filename,'bytes':len(encoded),
                                 'sha256':hashlib.sha256(encoded).hexdigest()}
    with open(os.path.join(capture,'metadata.json'),'x',encoding='utf-8',newline='') as handle:
        json.dump(receipt,handle,indent=2)
        handle.write('\n')
    return capture


def main(argv=None):
    if os.name != 'nt':
        import signal
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))   # `docker stop` / a timeout kill unwinds like Ctrl-C: the lease context exits cleanly
    forwarded = list(sys.argv[1:] if argv is None else argv)
    if '--effectiveness-shadow' in forwarded:
        # Separate frozen-evidence branch, before the production writer chain,
        # workbook refreshes and rank/allocation publication. Never insert an
        # uncalibrated policy into the default CHAIN.
        if forwarded[0] != '--effectiveness-shadow':
            raise ValueError('--effectiveness-shadow must be the first option')
        from legislation.run_effectiveness_shadow import main as shadow_main
        return shadow_main(forwarded[1:])
    parser=argparse.ArgumentParser()
    parser.add_argument('--recalc',action='store_true')
    parser.add_argument('--from',dest='frm',default=None)
    parser.add_argument('--diagnostics-dir',default=None,
                        help='retain complete failed-step streams and child metadata in unique subdirectories')
    args=parser.parse_args(argv)
    try:
        first=start_index(args.frm)
    except ValueError as exc:
        parser.error(str(exc))
    if args.recalc:
        print('note: --recalc is retired; Python owns rank math and the workbook is a view.')
    t0=time.time()
    tolerated_steps=[]
    receipt_path=os.path.join(MB_ROOT,TOLERATED_RECEIPT)
    _gb('pipeline.run','running','chain started (%d steps)'%len(CHAIN[first:]),
        run_id=_GB_RUN,correlation_id=_GB_CORR)
    try:
        preflight_dependencies()
        with pipeline_run_lock():
            # Each run reports only its own tolerated failures. A run from step 1 resets the receipt
            # to [] once, before the first step; a --from resume drops only the entries for the steps
            # inside its slice, so a tolerance an earlier run recorded for a step this run does not
            # re-run stays on the record. No receipt is created when none exists, so a clean chain
            # writes nothing it did not write before. Best-effort: the receipt is a report, not a
            # gate, so a directory squatting on its path (or any other write failure) must not abort
            # the chain.
            if os.path.exists(receipt_path):
                try:
                    _prune_tolerated(receipt_path,[step for step,_step_args in CHAIN[first:]],first==0)
                except Exception as exc:
                    print('note: tolerated-failure receipt not written (%s: %s)'%(
                        type(exc).__name__,str(exc)[:120]))
            for script,script_args in CHAIN[first:]:
                started=time.time()
                req=OPTIONAL_INPUTS.get(script)
                reqs=(req,) if isinstance(req,str) else (req or ())
                missing=next((path for path in reqs
                              if not os.path.exists(
                                  path if os.path.isabs(path) else os.path.join(MB_ROOT,path))),None)
                if missing:
                    print('  %-28s %s %5.1fs  %s'%(
                        script+' '+' '.join(script_args),'--',time.time()-started,'skip: %s absent'%missing))
                    continue
                _gb('pipeline.step','running','%s'%(script+' '+' '.join(script_args)).strip(),
                    stage=script,run_id=_GB_RUN,correlation_id=_GB_CORR)
                step_argv=[sys.executable,_producer_path(script)]+script_args
                run=run_bounded(step_argv,timeout=bounded_subprocess.CHAIN_STEP_TIMEOUT_SECONDS)
                tail=(run.stdout or '').strip().splitlines()[-1:] or ['']
                # A frozen step returns 124, never the 2 a display-only step is forgiven for, so it
                # halts the chain through the existing failure path even inside TOLERANT_STEPS.
                if run.timed_out:
                    tail=[run.timeout_note]
                last_good=LAST_GOOD.get(script)
                if last_good and not os.path.isabs(last_good):
                    last_good=os.path.join(MB_ROOT,last_good)
                last_good_receipt=LAST_GOOD_RECEIPT.get(script)
                if last_good_receipt and not os.path.isabs(last_good_receipt):
                    last_good_receipt=os.path.join(MB_ROOT,last_good_receipt)
                tolerated=run.returncode==2 and script in TOLERANT_STEPS and _last_good_ok(last_good) and _last_good_receipt_ok(last_good_receipt) and _last_good_receipt_binds_artifact(last_good_receipt,last_good)
                print('  %-28s %s %5.1fs  %s%s'%(
                    script+' '+' '.join(script_args),'OK' if run.returncode==0 else '!!',
                    time.time()-started,tail[0][:90],
                    ' (tolerated: display-only; last-good artifact retained)' if tolerated else '',
                ))
                _gb('pipeline.step','completed' if run.returncode==0 else 'failed',
                    tail[0][:200] or (script+' finished'),stage=script,
                    run_id=_GB_RUN,correlation_id=_GB_CORR,
                    duration_ms=int((time.time()-started)*1000),
                    detail='' if run.returncode==0 else ((run.stderr or '')[-1200:]))
                if run.returncode!=0:
                    failure=_step_failure_metadata(script,step_argv,run)
                    print('  child pid=%s returncode=%s (%s)%s'%(
                        run.pid,run.returncode,failure['returncode_u32_hex'],
                        ' [timeout sentinel; native exit unavailable]' if run.timed_out else ''))
                    if args.diagnostics_dir:
                        try:
                            capture=_record_step_failure(args.diagnostics_dir,failure,run)
                            print('  failure diagnostics: '+capture)
                        except Exception as exc:
                            print('note: failure diagnostics not written (%s: %s)'%(
                                type(exc).__name__,str(exc)[:200]))
                    from audit_failure_gate import pipeline_failure_output
                    print(pipeline_failure_output(script,run.stdout))
                    print((run.stderr or '')[-800:])
                    if tolerated:
                        try:
                            last_good_mtime=time.strftime('%Y-%m-%dT%H:%M:%SZ',
                                                          time.gmtime(os.path.getmtime(last_good)))
                        except OSError:
                            last_good_mtime=None
                        entry={
                            'step':script,
                            'args':list(script_args),
                            'returncode':run.returncode,
                            'tail':((run.stderr or '').strip() or (run.stdout or '').strip())[-800:],
                            'timestamp_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                            'last_good_path':last_good,
                            'last_good_mtime_iso':last_good_mtime,
                        }
                        try:
                            _record_tolerated(receipt_path,entry)
                        except Exception as exc:
                            print('note: tolerated-failure receipt not written (%s: %s)'%(
                                type(exc).__name__,str(exc)[:120]))
                        tolerated_steps.append(script)
                        _gb('pipeline.tolerated_failure','completed',
                            'tolerated display-only failure at %s; last-good artifact retained'%script,
                            stage=script,run_id=_GB_RUN,correlation_id=_GB_CORR,
                            duration_ms=int((time.time()-started)*1000),detail=entry['tail'][:1200],
                            metadata={'step':script,'returncode':run.returncode,'tolerated':True,
                                      'last_good_path':last_good,'receipt':receipt_path})
                        continue
                    _gb('pipeline.run','failed','chain halted at %s'%script,stage=script,
                        run_id=_GB_RUN,correlation_id=_GB_CORR,
                        duration_ms=int((time.time()-t0)*1000))
                    return 1
    except RuntimeError as exc:
        print('FATAL:',exc,file=sys.stderr)
        _gb('pipeline.run','failed','FATAL: %s'%exc,run_id=_GB_RUN,correlation_id=_GB_CORR,
            duration_ms=int((time.time()-t0)*1000))
        return 1
    done='PIPELINE COMPLETE in %.1f min'%((time.time()-t0)/60)
    if tolerated_steps:
        done+=' (%d tolerated display-only failure(s): %s)'%(len(tolerated_steps),', '.join(tolerated_steps))
    print(done)
    _gb('pipeline.run','completed',done,
        run_id=_GB_RUN,correlation_id=_GB_CORR,duration_ms=int((time.time()-t0)*1000))
    return 0


if __name__=='__main__':
    sys.exit(main())
