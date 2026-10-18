# -*- coding: utf-8 -*-
"""SYSTEM MANIFEST PRODUCER — Phase 0 of the Melange containerization effort.

Emits data/system/system_manifest.json (schema "melange-system-manifest-v1"): one row per DATA
STORE the system depends on, measured from the filesystem at run time, plus the host topology that
decides whether losing one disk loses the store.

WHY THIS EXISTS.  The 2026-08-26 census established that this box has ONE physical disk, no
external drive, no network share, File History stopped and zero restore points; that the live tree
and Draft Systems sit inside OneDrive (cloud-REPLICATED, which is not the same as backed up); and
that C:\\melange-data, %LOCALAPPDATA%\\Melange, ~\\GitMirrors and ~\\.claude\\glassbox sit outside
OneDrive and therefore have no copy anywhere but Disk 0.  Nothing in the repo recorded that.  This
producer records it, per store, from measurement rather than from memory.

THE THREE RULES THAT MAKE THIS TRUSTWORTHY

  1. DISCOVERED, NEVER AUTHORED.  Sizes, file counts, mtimes, hashes, sentinel selection, disk and
     volume topology are all read off the machine at run time.  The registry below supplies ONLY the
     things a filesystem cannot know: what a store is for, who writes it, who reads it, how it would
     be restored, how irreplaceable it is, and what integrity evidence it warrants.  `single_disk`,
     `offbox_replica` and `durable_backup` are DERIVED here and must never be typed into the
     registry — a hand-authored copy story is exactly the claim this manifest exists to replace.

  2. SECRETS ARE NEVER READ.  A store classed SECRET yields path, size and mtime and nothing else.
     The guard is a hard code path (`_forbid_secret`) that raises, not a comment asking nicely.
     Every routine that would open a file for content calls it first.

  3. THE HASH ENVELOPE IS ENFORCED, NOT DOCUMENTED.  `hash_always` is emitted only for a store that
     is small (<= 8 MiB), shallow (<= 200 files), genuinely irreplaceable (irreplaceability >= 2),
     fully walked, present, and not secret.  A registry row asking for hash_always outside that
     envelope is DOWNGRADED to hash_never, with the failing predicate recorded in
     `policy_overrides` and printed — never silently honoured, never silently dropped.

  4. HASH_ALWAYS COVERS ONLY WHAT GIT DOES NOT PROTECT.  (Paul's ruling, 2026-08-27: "use the
     narrower scope".)  The envelope above answers "can we AFFORD to hash this?"; this rule answers
     "is hashing this OUR job at all?".  For anything git tracks, git is already the integrity
     authority — the blob sha is the hash, `git status` is the detector and `git checkout` is the
     restore — so a second hash set here buys no evidence and costs real damage: it goes stale on
     every ordinary commit (this branch's own merge included), the audit reports a disagreement
     that is not a defect, and a check that cries wolf is a check that gets waved through.  So a
     store the producer determines is GIT-TRACKED is REFUSED hash_always and downgraded to
     hash_never, recorded in `policy_overrides` with refused_on='git-tracked' exactly the way the
     scale refusal is recorded.  What is left is the narrow scope the rule is named for: gitignored
     or off-repo material no producer can regenerate — adjudicated label sets, hand-ruled registries
     and ndjson, rescue manifests — the material whose only integrity evidence is the one recorded
     here.  Tracked-ness is MEASURED (one `git ls-files` pass per root, cached), never declared; if
     git cannot answer, the store records git_tracked=null and the guard does not fire (see
     GIT_UNKNOWN_HONOURS_HASH_ALWAYS — an unverified guard must not silently strip evidence).

  5. A SENTINEL MUST BE ABLE TO HOLD STILL.  A sentinel is an expectation re-checked days later, so
     anything ordinary operation rewrites is disqualified from sentinel DUTY (it still counts toward
     the store's bytes and file_count).  That covers build caches, temp files, locks and logs — and,
     since 2026-08-27, GIT BOOKKEEPING: the index, refs, reflogs, packed-refs and the transient
     *_HEAD pointers.  Learned the hard way when this gate went red on its own landing commit; the
     GIT BOOKKEEPING block below carries the byte-exact evidence.  Git OBJECTS stay eligible because
     they are content-addressed.  `content_stable` is DERIVED from that same predicate, so a
     volatile path can never reach the manifest claiming stable bytes.

WRITES.  Exactly one path: data/system/system_manifest.json (creating data/system/ if absent, and a
temp sibling that dump_atomic replaces).  Nothing else on the box is created, modified or deleted.
The git probe is `git ls-files`, which reads the index and prints; it takes no lock, touches no
work tree, and is safe while another lane is mid-commit.
Read-only against every store it measures.  Safe to run repeatedly.  Not a chain step; consumed by
nothing yet.  fec/audit/audit_full.py checks the emitted manifest against live disk.

USAGE
    python fec/audit/build_system_manifest.py
    python fec/audit/build_system_manifest.py --out <path>      # alternate output, same content
    python fec/audit/build_system_manifest.py --dry-run         # measure and print, write nothing

DIALS
    SYSMAN_STORE_FILE_CAP    per-store walk budget in files (default 300000)
    SYSMAN_TOTAL_FILE_CAP    whole-run walk budget in files (default 1500000)
    SYSMAN_HOST_PROBE        set to 0 to skip the PowerShell host probe (records it as skipped)
    SYSMAN_GIT_PROBE         set to 0 to skip the git tracked-ness probe (records every store
                             git_tracked=null; the scope guard then cannot fire — used by the
                             control battery to exercise the degraded path on purpose)
"""
import os, re, sys, json, time, hashlib, fnmatch, argparse, subprocess, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import ROOT, DRAFT                                    # centralized root (arch review 2026-07-02)
import moneyball_core as MC                                      # dump_atomic

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

SCHEMA = 'melange-system-manifest-v1'
OUT_REL = os.path.join('data', 'system', 'system_manifest.json')

# ---------------------------------------------------------------------------------------------
# Integrity envelope.  These are the caps the producer ENFORCES; they are echoed into the manifest
# so the audit can re-assert them against whatever the manifest actually recorded.
# ---------------------------------------------------------------------------------------------
HASH_ALWAYS_MAX_BYTES = 8 * 1024 * 1024
HASH_ALWAYS_MAX_FILES = 200
HASH_ALWAYS_MIN_IRREPLACEABILITY = 2
SENTINEL_HASH_MAX_BYTES = 8 * 1024 * 1024
SENTINEL_COUNT = 3
# A sentinel is an EXPECTATION the audit re-checks days later, so it must be a stable artifact.
# These are regenerated or rewritten by ordinary operation, so pinning one guarantees a false
# disagreement. Found the hard way: the first run picked a __pycache__ .pyc as the newest file in
# .claude/worktrees, and merely importing the module changed its bytes. They still COUNT toward the
# store's size and file count — this rule governs sentinel duty only, not measurement.
SENTINEL_EXCLUDE = ('__pycache__/', '.pytest_cache/', '.tmp', '.part', '.lock', '.log', '.swp')
MTIME_TOLERANCE_S = 2.0            # NTFS/derivative same-second writes are not an ordering violation

# ---------------------------------------------------------------------------------------------
# GIT BOOKKEEPING IS NOT SENTINEL MATERIAL.  (Root-cause fix, 2026-08-27.)
#
# Found by this gate going red on its OWN landing commit.  The picker took the NEWEST file in each
# git store — `.git/index` for repo.git, `refs/heads/main` for mirror.melange — and marked both
# content_stable:true.  The next commit rewrote the index (8,371,954 -> 8,372,431 bytes) and the
# next push moved the ref, so the recorded mirror.melange sha256 was the hash of the PRE-PUSH ref
# and the audit reported a disagreement that was simply "a commit happened".  A gate that goes red
# after every ordinary commit stops being evidence, exactly like the checks that could never fail
# which this file's anti-vacuity rules exist to prevent — the always-red twin of never-fails.
#
# A git directory's bookkeeping is REWRITTEN by ordinary commits, pushes, fetches and checkouts:
# the index, the loose and symbolic refs, the reflogs, packed-refs, the transient *_HEAD pointers,
# the in-flight rebase/bisect state, and the caches git regenerates (commit-graph, multi-pack-index,
# the dumb-http listings).  Byte identity is not a property those files have, so pinning one asserts
# an invariant the store does not hold.
#
# OBJECT files are deliberately still eligible, and that is the whole reason this is a predicate
# rather than a ban on git directories: a loose object or a pack is NAMED BY THE HASH OF ITS OWN
# CONTENTS, so it cannot change without changing its name.  That is precisely the immutability a
# sentinel needs.  Residual, accepted knowingly: `git gc` can RETIRE an object file, and A2b would
# then report it missing.  That is infrequent, it is a real change to the store, and regenerating
# the manifest is the honest response — unlike a commit, which happens hourly.
#
# These paths still COUNT toward the store's bytes and file_count.  This governs sentinel duty only.
# ---------------------------------------------------------------------------------------------
GIT_BOOKKEEPING_FILES = frozenset((
    'index', 'HEAD', 'ORIG_HEAD', 'FETCH_HEAD', 'MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD',
    'REBASE_HEAD', 'AUTO_MERGE', 'BISECT_HEAD', 'BISECT_START', 'BISECT_LOG', 'BISECT_NAMES',
    'BISECT_EXPECTED_REV', 'BISECT_ANCESTORS_OK', 'packed-refs', 'COMMIT_EDITMSG', 'MERGE_MSG',
    'MERGE_MODE', 'SQUASH_MSG', 'TAG_EDITMSG', 'shallow', 'gc.pid', 'gc.log',
))
#: directories INSIDE a git directory whose whole contents are bookkeeping
GIT_BOOKKEEPING_DIRS = ('refs', 'logs', 'rebase-merge', 'rebase-apply', 'sequencer',
                        'worktrees', 'modules')
#: caches git regenerates; content, not bookkeeping, but rewritten by maintenance all the same
GIT_DERIVED_INDEX_PREFIXES = ('objects/info/',)
GIT_DERIVED_INDEX_PATHS = ('info/refs', 'objects/pack/multi-pack-index')
#: the three entries every git directory has, bare mirror and work-tree `.git` alike
GIT_DIR_MARKERS = (('HEAD', 'file'), ('objects', 'dir'), ('refs', 'dir'))

POLICIES = ('hash_always', 'hash_never', 'sentinel_files', 'presence_only')
CLASSES = ('SOURCE', 'SNAPSHOT', 'DERIVED', 'SECRET', 'OPS')

# ---------------------------------------------------------------------------------------------
# SCOPE.  Paul's ruling 2026-08-27, "use the narrower scope": hash_always covers ONLY material git
# does not protect.  Git tracks it -> git is the integrity authority -> this producer stays out.
# The rule is enforced on MEASURED tracked-ness, one `git ls-files` pass per root, cached.
# ---------------------------------------------------------------------------------------------
GIT_PROBE = os.environ.get('SYSMAN_GIT_PROBE', '1') != '0'
# When git cannot answer (binary missing, probe timed out, root is not a work tree we can read),
# tracked-ness is null and the guard DOES NOT fire — a declared hash_always is honoured, and every
# store honoured on an unverified probe is listed in manifest['git_tracking']['unverified_...'].
# The alternative — refusing hash_always on an unproven suspicion — would silently delete the only
# integrity evidence the irreplaceable stores have, on exactly the hosts where git is not there to
# replace it. Guards fire on evidence, not on the absence of it. Flip this one flag to invert.
GIT_UNKNOWN_HONOURS_HASH_ALWAYS = True
GIT_LS_FILES_TIMEOUT_S = 120

STORE_FILE_CAP = int(os.environ.get('SYSMAN_STORE_FILE_CAP', '300000'))
TOTAL_FILE_CAP = int(os.environ.get('SYSMAN_TOTAL_FILE_CAP', '1500000'))

_HOME = os.path.expanduser('~')

# ---------------------------------------------------------------------------------------------
# ROOTS.  A store lives under exactly one root.  When a root is absent (fresh clone, another box)
# every store beneath it is SKIPPED by the audit rather than failed — but if EVERY root is absent
# the manifest is describing a different machine and that is a failure, not a skip.
# ---------------------------------------------------------------------------------------------
ROOTS = (
    {'id': 'live_tree',     'path': ROOT,
     'label': 'Melange live tree (repo + deployed surfaces + root-level data plane)'},
    {'id': 'draft_systems', 'path': DRAFT,
     'label': 'Draft Systems — the R press/collection pipeline root'},
    {'id': 'melange_data',  'path': r'C:\melange-data',
     'label': 'Off-repo bulk store: audio corpora, BICAM snapshot, lane scratch scripts'},
    {'id': 'local_melange', 'path': os.path.join(os.environ.get('LOCALAPPDATA', os.path.join(_HOME, 'AppData', 'Local')), 'Melange'),
     'label': 'Per-user collection cache: LDA/LobbyView/BILLSTATUS/demographics/FEC raw + derived'},
    {'id': 'git_mirrors',   'path': os.path.join(_HOME, 'GitMirrors'),
     'label': 'Bare git mirrors (melange-systems, glassbox-runtime)'},
    {'id': 'glassbox',      'path': os.path.join(_HOME, '.claude', 'glassbox'),
     'label': 'GLASS BOX orchestration runtime (scripts + event stream + state)'},
)

# ---------------------------------------------------------------------------------------------
# REGISTRY.  Curated, and ONLY the fields no filesystem can supply.
#
#   cls              SOURCE | SNAPSHOT | DERIVED | SECRET | OPS
#   producer         the script that WRITES this store, or None where none exists in-repo
#   consumers        scripts/surfaces that READ it (illustrative, not exhaustive where noted)
#   restore_route    what you would actually do if this store were gone
#   cadence          how often it legitimately changes
#   irrep            irreplaceability 0-4, INTRINSIC (cost to recreate the content from upstream),
#                    deliberately independent of whether a copy happens to exist — the copy story is
#                    carried by the derived single_disk / offbox_replica / restore_route fields
#   policy           requested integrity policy; enforced against the envelope above
#   derives_from     store ids whose mtime must not be newer than this store's
#   count            False when the store overlaps another store and must not double-count totals
#   excl             path fragments excluded from the walk (first-segment or fnmatch on the relpath)
#   sentinel_excl    path fragments excluded from SENTINEL DUTY only (same matcher as excl). The
#                    files are still walked, still counted, still carry the store's bytes; they are
#                    merely ineligible to be PINNED. Declared for paths the pipeline CHAIN rewrites
#                    (fec/run_pipeline.py's CHAIN), because a sentinel is an expectation re-checked
#                    later and a chain output has no byte-stability invariant to assert. Every entry
#                    carries its chain step and writer file:line in the row's comment: an entry
#                    without a receipt is a silent coverage reduction, which is the exact defect
#                    this field could otherwise become.
#   sentinel_newest  False when the store's NEWEST file is by construction whatever the last run
#                    just wrote. Generalises the git-directory opt-out below from "the last commit"
#                    to "the last chain run"; identical reasoning, wider scope.
# ---------------------------------------------------------------------------------------------
def _s(sid, root, rel, cls, producer, consumers, restore_route, cadence, irrep, policy,
       derives_from=(), count=True, excl=(), note=None, cap=None,
       sentinel_excl=(), sentinel_newest=True):
    return {'id': sid, 'root_id': root, 'rel': rel, 'class': cls, 'producer': producer,
            'consumers': list(consumers), 'restore_route': restore_route, 'cadence': cadence,
            'irreplaceability': irrep, 'integrity_policy_declared': policy,
            'derives_from': list(derives_from), 'counts_toward_totals': count,
            'excludes': list(excl), 'note': note, 'cap': cap,
            'sentinel_excludes': list(sentinel_excl), 'sentinel_newest': bool(sentinel_newest)}


REGISTRY = (
    # ---- live tree: adjudicated inputs (the expensive, hand-ruled material) -------------------
    _s('repo.bill_beneficiary_tags', 'live_tree', 'bill_beneficiary_tags.ndjson', 'SOURCE',
       'NO IN-REPO PRODUCER writes this path directly. '
       'fec/legislation/build_greenlit_legislation_primacy_merge.py:450 writes the sibling '
       'bill_beneficiary_tags.primacy_merged.ndjson, which '
       'fec/legislation/promote_greenlit_primacy_integration.py:55-56 promotes onto this name.',
       ['fec/engine/primacy_factors.py:451', 'fec/engine/responsiveness_layers.py:619',
        'fec/lobbying/industry_tiers.py:41', 'fec/legislation/build_primacy_act_memberships.py:106',
        'fec/tools/b0_bicam_arrival_audit.py:1508', 'fec/audit/audit_full.py:1498'],
       'Not mechanically recoverable: the class column is adjudicated per act against the ACU '
       'outline. Re-deriving means re-running the 19-category review.',
       'on adjudication', 4, 'hash_always',
       note='The 648-act universe. One of four load-bearing artifacts with no live in-repo '
            'producer — losing it is unrecoverable inside this repo. Declares hash_always ON '
            'MERIT and is REFUSED by the git scope rule, because git tracks it: the refusal is '
            'the record that git — not this manifest — is what stands behind this file.'),
    _s('repo.industry_axes', 'live_tree', 'data/lobbying/industry_axes.ndjson', 'SOURCE',
       'NO IN-REPO PRODUCER. Both references in fec/ are reads '
       '(fec/lobbying/industry_tiers.py:50 AXES_PATH, fec/engine/primacy_factors.py:81 CU_TENSOR); '
       'INDUSTRY_AXES_DESIGN.md:125 describes it as the output of an adjudication, not a script.',
       ['fec/lobbying/industry_tiers.py:50', 'fec/engine/primacy_factors.py:81'],
       'Rebuild by hand from INDUSTRY_AXES_DESIGN.md; the relevance x valence cells are rulings.',
       'on ruling', 4, 'hash_always', count=False,
       note='31-row relevance x valence tensor. No live producer — hand-adjudicated. Declares '
            'hash_always ON MERIT and is REFUSED by the git scope rule: git tracks it.'),
    _s('repo.network_base_live', 'live_tree',
       'congress_cosponsor_network_20260625_205554.xlsx', 'SOURCE',
       'NO IN-REPO PRODUCER. Nothing under fec/ (.py/.R/.mjs/.cjs/.ps1) writes this name; the '
       '~19 call sites all glob it read-only via moneyball_core.select_network_file. It is the '
       'output of an EXTERNAL R scraper in Draft Systems.',
       ['fec/moneyball_core.py:235,367 (select_network_file / _directed)',
        'fec/network/build_multilayer_network.py:88 (AUG base pick)',
        'fec/members/behavior_panel.py:66', 'fec/network/press_engagement_layer.py:71'],
       'Re-run the external R cosponsor scraper in Draft Systems. There is no Python path to it.',
       'frozen (vintage 2026-06-25)', 4, 'hash_always',
       note='THE live CU network BASE vintage, per fec/legislation/leg_evidence_recompute.py:58. '
            'The substrate under 19 call sites and it has no in-repo producer. Small enough to '
            'hash, so it is hashed. Note the known Menendez cross-roster misattribution: all 9 of '
            'M001226\'s CU out-edges are unsupported by BILLSTATUS.'),
    _s('repo.acu_registry', 'live_tree', 'source_data/legislation/acu_legislation_search_registry.json',
       'SOURCE', 'fec/legislation/build_legislation_search_registry.py',
       ['fec/legislation/*', 'primacy engine'],
       'Regenerate from Moneyball_ACU_Search_Outline.docx (sha256-pinned) in source_data/legislation.',
       'on outline change', 3, 'hash_always', count=False),
    _s('repo.acu_outline_docx', 'live_tree',
       'source_data/legislation/Moneyball_ACU_Search_Outline.docx', 'SOURCE',
       'external (ACU) — collected, not produced here',
       ['fec/legislation/build_legislation_search_registry.py'],
       'Re-obtain the outline from ACU; the pinned sha256 is the acceptance test. It is the ACU '
       'source of record for all 19 categories.',
       'rare', 4, 'hash_always', count=False,
       note='Declares hash_always ON MERIT and is REFUSED by the git scope rule: git tracks the '
            'docx, so git holds its bytes. Note the sibling acu_legislation_search_registry.json '
            'derived FROM it is NOT tracked, and does keep its hash set — the two rows side by '
            'side are the clearest statement of where the scope line falls.'),
    _s('repo.congestion', 'live_tree', 'data/engine/congestion.json', 'SOURCE',
       'NO IN-REPO PRODUCER (roster snapshot; the 120th-rollover campaign owns its resurrection)',
       ['fec/moneyball_core.py congestion()', 'every engine that resolves the 533 roster'],
       'Rebuild from voteview/legislators rosters per the rollover campaign; there is no live '
       'producer script today.',
       'per Congress', 3, 'hash_always', count=False,
       note='Known gap: congestion.json has no live producer and does not populate a usable state '
            'field (reads "?"). Recorded here so the gap is inventoried, not discovered again.'),
    _s('repo.culling_receipt', 'live_tree', 'legislation_culling_receipt.json', 'SOURCE',
       'legfix5 culling lane (Paul-gated)',
       ['fec/audit/audit_full.py', 'dashboard bundle'],
       'Re-derive only by re-running the gated culling adjudication.',
       'on adjudication', 3, 'hash_always'),
    _s('repo.source_data', 'live_tree', 'source_data', 'SOURCE',
       'mixed: collectors + hand-placed source documents',
       ['collectors', 'legislation lane', 'campaign-site lane'],
       'Per-subtree: some re-collectable from upstream, some (rosters, tranches) hand-verified.',
       'on collection', 3, 'sentinel_files', count=True),

    # ---- live tree: derived artifacts (the chain's outputs) -----------------------------------
    _s('repo.ranks_python', 'live_tree', 'ranks_python.json', 'DERIVED',
       'fec/engine/compute_pillars.py:221 (dump_atomic, only under --write)',
       ['fec/moneyball_core.py:109-114', 'fec/tier_util.py', 'fec/weekly_refresh.py:66,102',
        'fec/members/lep_criterion.py:112', 'fec/audit/legacy_workbook_audit.py:20',
        'fec/surfaces/build_dashboard_bundle.py', '~30 further call sites'],
       'Regenerate with the full chain (run_pipeline.py); it is the rank authority, the workbook is '
       'a view of it.',
       'per chain run', 1, 'hash_never'),
    _s('repo.dashboard_data', 'live_tree', 'dashboard_data.json', 'DERIVED',
       'fec/surfaces/build_dashboard_bundle.py:712,717 (DASH_OUT redirects it)',
       ['fec/surfaces/make_dashboard_html.py:70', 'fec/surfaces/make_member_desk.py:143',
        'fec/engine/efficiency_layers.py:731', 'fec/audit/audit_full.py:193,1727,1878'],
       'Regenerate with fec/surfaces/build_dashboard_bundle.py after a chain run.',
       'per chain run', 1, 'hash_never', derives_from=['repo.ranks_python']),
    _s('repo.bipartisan', 'live_tree', 'bipartisan.json', 'DERIVED',
       'fec/surfaces/build_dashboard_bundle.py:716 (side output of the same write)',
       ['fec/moneyball_core.py', 'fec/network/build_multilayer_network.py',
        'fec/members/behavior_panel.py', 'fec/stance/stance_dynamics.py'],
       'Regenerate with build_dashboard_bundle.py; needs two runs to converge.',
       'per chain run', 1, 'hash_never'),
    _s('repo.category_stance', 'live_tree', 'category_stance.json', 'DERIVED',
       'fec/stance/per_category_stance.py:120-155 (CATEGORY_STANCE_OUT redirects it)',
       ['all 10 engines under fec/engine/', 'fec/surfaces/*', 'fec/moneyball_core.py',
        '46 live call sites in total'],
       'Regenerate with the chain.',
       'per chain run', 1, 'hash_never'),
    _s('repo.member_receptivity', 'live_tree', 'member_receptivity.json', 'DERIVED',
       'fec/members/receptivity_layer.py:215',
       ['fec/moneyball_core.py rc()', 'fec/surfaces/build_dashboard_bundle.py',
        'fec/engine/outcome_snapshot.py:29', 'fec/audit/audit_full.py:293,533,592'],
       'Regenerate with the chain.',
       'per chain run', 1, 'hash_never'),
    _s('repo.say_do_gap', 'live_tree', 'say_do_gap.json', 'DERIVED',
       'fec/stance/say_do_gap.py:76-80 (self-declared NON-chain diagnostic)',
       ['fec/moneyball_core.py delivery_mult()', 'fec/surfaces/make_member_desk.py',
        'fec/lobbying/coalition_pricing.py'],
       'Re-run fec/stance/say_do_gap.py; it is not a chain step, so a chain run alone will not '
       'restore it.',
       'ad hoc', 1, 'hash_never'),
    _s('repo.vote_category_lean', 'live_tree', 'vote_category_lean.json', 'DERIVED',
       'fec/legislation/build_vote_category.py --publish',
       ['fec/moneyball_core.py vote_corroboration()', 'fec/stance/per_category_stance.py',
        'fec/weekly_refresh.py'],
       'Rebuild from the hash-bound Voteview source snapshot configured in '
       'data/legislation/vote_source_snapshot.json, the current bill archive and retained '
       'hold/exclusion decisions. The V-1 audit checks source coverage and input/output hashes.',
       'per chain run', 1, 'hash_never'),
    _s('repo.workbook', 'live_tree', 'Moneyball Main Version - Blended.xlsx', 'DERIVED',
       'MULTIPLE writers, no single producer. In chain: fec/stance/build_media_blend.py:479 '
       'and fec/engine/committee_scores.py:325 (save_workbook_atomic; committee_scores.py '
       "--write is run_pipeline.py:146 and owns the 'Committee Data' B/C/D cache only, "
       'immediately before compute_pillars.py --write reads column B), plus '
       'fec/stance/sync_stance_g.py:75, which never saves the book it opened: it collects the '
       'changed cells and calls publish_cells (fec/stance/workbook_cell_sync.py:96), which '
       're-authors them out of process, merges them into a sibling copy and commits that copy '
       'with os.replace at :150. Out of chain: four legislation repair scripts and '
       'fec/engine/add_judiciary_seats.py:105 (NOT atomic); five Excel-COM writers under '
       'fec/engine/apply_*.py. compute_pillars.py:43 READS it, never writes it.',
       ['legacy_workbook_audit.py', 'committee scoring INDEX/MATCH formulas', 'human readers'],
       'Regenerate from the chain; the O/E/F formula caches revive only via a human Excel '
       'open+save, which is why the staleness tripwire exists.',
       'per chain run', 2, 'hash_never',
       note='Workbook is a VIEW of ranks_python.json, not the rank authority.'),
    _s('repo.holistic_base', 'live_tree', 'holistic_network_BASE_20261011.xlsx', 'DERIVED',
       'fec/network/build_holistic_base.py:206-240 (HOLISTIC_BASE_OUT; name guard at :141-143)',
       ['fec/moneyball_core.py:331 load_network() under NET_CONFIG=holistic',
        'fec/network/build_multilayer_network.py:90-94'],
       'Rebuild from the BILLSTATUS archive at %LOCALAPPDATA%\\Melange\\lda_rebuild\\raw; '
       'deterministic (canonical row hash pinned in its manifest).',
       'rare', 2, 'hash_never'),
    _s('repo.holistic_aug', 'live_tree', 'holistic_network_AUG_20261011.xlsx', 'DERIVED',
       'fec/network/build_multilayer_network.py:194-196 under NET_CONFIG=holistic',
       ['fec/moneyball_core.py:331 load_network() under NET_CONFIG=holistic'],
       'Rebuild from the BASE vintage; the BASE->AUG chain is idempotent.',
       'rare', 1, 'hash_never', derives_from=['repo.holistic_base']),
    _s('repo.data_holistic', 'live_tree', 'data/holistic', 'DERIVED',
       'fec/network/build_holistic_base.py:172-198 (billcache_11[789].ndjson) + '
       'fec/network/build_sector_diagnostic.py:249-272 (sectorcache_11[789].ndjson); both write '
       'cache+".tmp" then os.replace',
       ['fec/network/build_holistic_base.py:180-183 (cache-hit path)',
        'fec/tools/identity_collision_census.py:141-157'],
       'Rebuild from the BILLSTATUS archive at %LOCALAPPDATA%\\Melange\\lda_rebuild\\raw.',
       'rare', 1, 'hash_never'),
    _s('repo.dashboard_html', 'live_tree', 'Moneyball Dashboard.html', 'DERIVED',
       'fec/surfaces/make_dashboard_html.py:73-74 (write_text_atomic; DASHBOARD_HTML_OUT override)',
       ['Paul / lobbyists (deployed surface)', 'fec/surfaces/make_combined.py:128'],
       'Rebuild and redeploy from the template + bundle.',
       'per deploy', 1, 'hash_never', derives_from=['repo.dashboard_data']),
    _s('repo.desk_html', 'live_tree', 'cu-member-desk.html', 'DERIVED',
       'fec/surfaces/make_member_desk.py:2322,2358 (write_text_atomic; DESK_OUT dir override)',
       ['Paul / lobbyists (deployed surface)', 'fec/surfaces/make_combined.py:129',
        'fec/audit/audit_full.py:850,1760'],
       'Rebuild and redeploy from the template + desk bundle.',
       'per deploy', 1, 'hash_never', derives_from=['repo.desk_bundle']),
    _s('repo.combined_html', 'live_tree', 'Moneyball.html', 'DERIVED',
       'fec/surfaces/make_combined.py:384-386 (concatenates the dashboard, desk and lobby-network '
       'surfaces read at :126-130)',
       ['fec/surfaces/lock_dashboard.py:12 -> deploy/index.html',
        'fec/audit/audit_content_freshness.py:83'],
       'Reassemble with make_combined.py from the individual surfaces.',
       'per deploy', 1, 'hash_never',
       derives_from=['repo.dashboard_html', 'repo.desk_html']),
    _s('repo.desk_bundle', 'live_tree', 'member_desk_bundle.json', 'DERIVED',
       'fec/surfaces/make_member_desk.py:2318 (same script that writes the desk HTML)',
       ['cu-member-desk.html', 'fec/audit/audit_full.py:925'],
       'Rebuild with fec/surfaces/make_member_desk.py.',
       'per deploy', 1, 'hash_never'),
    _s('repo.press_text_cache', 'live_tree', 'press_text_cache.jsonl', 'DERIVED',
       'press collection lane',
       ['press pillar', 'stance stack'],
       'Re-collect; note 97.7% of rows are TRUNCATED, so counts derived from it are floors.',
       'on press refresh', 2, 'hash_never'),
    _s('repo.data_stance', 'live_tree', 'data/stance', 'DERIVED',
       'fec/stance/* — notably own_source_stance_model.py:702-704 writes '
       'own_source_stance_model.joblib via its own tmp+os.replace, not dump_atomic',
       ['fec/stance/{speech,hearing,interview,bluesky}_stance_stack.py (via run_channel)',
        'engines'],
       'Regenerate with the stance stack; the fitted model artifact is a retrain, not a recompute.',
       'on stance refresh', 2, 'sentinel_files',
       # CHAIN-WRITTEN (DE-011). own_source_stance_model.joblib is retrained by load_or_train()
       # (fec/stance/own_source_stance_model.py:702-704 — joblib.dump to a .tmp then os.replace),
       # reached through run_channel from the four stance stacks, which are chain steps at
       # fec/run_pipeline.py:112 (speech), :113 (hearing), :115 (interview) and :116 (bluesky).
       # It reached by_size rank 2 and disagreed on sha256 (15:47 run).
       # 2026-10-11: rank_honors/workbook_publications.json is rewritten by publish()'s
       # _write_bindings (fec/stance/honors_workbook.py:713, path at :601), chain step
       # honors_workbook.py at fec/run_pipeline.py:178; disagreed on sha256 after the 10-11 chain.
       sentinel_excl=['own_source_stance_model.joblib', 'workbook_publications.json'], sentinel_newest=False),
    _s('repo.data_legislation', 'live_tree', 'data/legislation', 'DERIVED',
       'fec/legislation/*',
       ['primacy engine', 'legislation surfaces'],
       'Regenerate with the legislation lane.',
       'on legislation refresh', 2, 'sentinel_files',
       # CHAIN-WRITTEN (DE-011). by_time landed on bank_anchor_reconciliation.json, written by
       # fec/legislation/build_bank_anchor_backfill.py:512 (write_text_atomic onto the RECEIPT
       # constant at :42). That script is not itself a chain entry: it is run as a SUBPROCESS by
       # fec/engine/build_primacy_surfaces.py:31 (its ENGINE_INPUT_STEPS tuple), which the chain
       # invokes as `build_primacy_surfaces.py --engine-inputs-only` at fec/run_pipeline.py:162.
       # Official refresh generations and pointers are validated by their source receipts,
       # not by a promise that weekly output bytes never change.
       sentinel_excl=['bank_anchor_reconciliation.json', 'congress_advanced_current/*', 'sentinel_admissions.json',
                      'sentinel_admission_checkpoint.json', 'official_membership_movements.json',
                      'vote_source_snapshot.json', 'vote_refresh_receipt.json'],
       sentinel_newest=False),
    _s('repo.data_lobbying', 'live_tree', 'data/lobbying', 'DERIVED',
       'fec/lobbying/*',
       ['industry boards', 'lobby surfaces'],
       'Regenerate with the lobbying lane.',
       'on lobbying refresh', 2, 'sentinel_files',
       # CHAIN-WRITTEN (DE-011). bill_lobbying_sidecar.json (chain step at fec/run_pipeline.py:288;
       # written by fec/lobbying/bill_lobbying_sidecar.py:733 via _write_json_atomic, the path
       # default declared at :70-71) holds by_size rank 2 at 6.0 MB — it has not disagreed yet,
       # which is luck, not an invariant. by_time landed on the audit scratch file, relocated out
       # of this store by the audit_full change in this same landing.
       sentinel_excl=['bill_lobbying_sidecar.json', 'industry_tiers.json'], sentinel_newest=False),
    _s('repo.data_network', 'live_tree', 'data/network', 'DERIVED',
       'network builders + diagnostics',
       ['engines', 'kb_benchmark'],
       'Regenerate with the network lane.',
       'on network refresh', 1, 'sentinel_files',
       # CHAIN-WRITTEN (DE-011). Not in either red run, flagged on evidence not symptom: by_time
       # is lt_priming.json, written every chain run by fec/engine/lt_priming.py:121 (chain step at
       # fec/run_pipeline.py:182). Exclude it from size-based selection as well.
       sentinel_excl=['lt_priming.json'], sentinel_newest=False),
    _s('repo.data_members', 'live_tree', 'data/members', 'DERIVED',
       'fec/members/*',
       ['engines', 'desk'],
       'Regenerate with the members lane.',
       'on refresh', 1, 'sentinel_files',
       # CHAIN-WRITTEN (DE-011). member_narrative_evidence_v4.json (by_size rank 3, 7.6 MB) is
       # written by chain step build_member_narrative_ledger.py (fec/run_pipeline.py:199; the write
       # is fec/members/build_member_narrative_ledger.py:1082 json.dump + :1084 os.replace, the
       # path default at :35); it disagreed on size at 15:47 and on sha256 at 19:35. by_time was
       # member_narratives_v3_descriptive_receipt.json (fec/members/regenerate_descriptive_
       # profiles.py:247 write_json onto the RECEIPT constant at :27), also a chain step
       # (fec/run_pipeline.py:148).
       sentinel_excl=['member_narrative_evidence_v4.json', 'member_reference_cache_fable.jsonl',
                      'member_reference_classification_receipt.json', 'collector_roster_updates.ndjson',
                      'collector_roster_pending.ndjson', 'sentinel_verified_membership_history.ndjson'], sentinel_newest=False),
    _s('repo.data_money', 'live_tree', 'data/money', 'DERIVED',
       'FEC collectors + fec/engine/build_electoral_history.py:111 (electoral_history.json)',
       ['fec/surfaces/make_member_desk.py', 'fec/members/behavior_panel.py',
        'fec/members/member_identity.py', 'money engines'],
       'Regenerate from the FEC cache at %LOCALAPPDATA%\\Melange\\fec_spending.',
       'on FEC refresh', 2, 'sentinel_files',
       # CHAIN-WRITTEN (DE-011). SIX of this store's EIGHT files are chain outputs, so the
       # exclusion has to be near-total or a chain output keeps a by_size slot. Writer, then the
       # chain step that runs it:
       #   Moneyball_email.zip            fec/surfaces/make_email_package.py:116-120 (alias copy +
       #                                  os.replace)                    run_pipeline.py:295
       #   budget_frontier.json           fec/engine/budget_frontier.py:153 (dump_atomic onto the
       #                                  :151-152 path)                 run_pipeline.py:183
       #   budget_frontier_electoral.json same writer, ELECTORAL branch (:34 reads --electoral or
       #                                  the ELECTORAL env var)         run_pipeline.py:184
       #   electoral.json                 fec/engine/build_electoral.py:32
       #                                                                 run_pipeline.py:83
       #   competitiveness_c.json.candidate  fec/engine/build_competitiveness.py (dump_atomic; deterministic,
       #                                  no timestamps)                 run_pipeline.py: the step after build_electoral
       #   electoral_plays.json           fec/engine/build_electoral_plays.py:37
       #                                                                 run_pipeline.py:179
       # What SURVIVES as sentinel material: electoral_history.json (208 KB) and affective_n.json
       # (13 KB) — both hashed, both actually stable. Today's four rows include three that churn
       # every run, so the informative evidence RISES 1 -> 2. (The sixth chain-touched file is the
       # relocated audit scratch _audit_tmp_frontier.json; see the audit_full change. Its abandoned
       # copy is left on disk by this landing and would take the third by_size slot until deleted.)
       # COMPET_MULT (2026-10-05): fec/engine/build_competitiveness.py (chain step, right after build_electoral.py) writes
       # competitiveness_c.json (the live artifact since Paul 2026-10-06; the earlier .candidate is kept as evidence) -- a chain output, so excluded from sentinel duty like its siblings. The Cook PVI
       # inputs under cook_pvi_2026/ (+ PROVENANCE.json with their sha256s) are static and stay ordinary store content.
       sentinel_excl=['Moneyball_email.zip', 'budget_frontier.json', 'competitiveness_c.json.candidate',
                      'competitiveness_c.json', 'budget_frontier_electoral.json', 'electoral.json', 'electoral_plays.json',
                      'fec_page_cache/*', 'fec_refresh_current.json', 'fec_money_application.json'],
       sentinel_newest=False),
    _s('repo.data_engine', 'live_tree', 'data/engine', 'DERIVED',
       'fec/engine/*',
       ['engines'],
       'Regenerate with the chain; apply_fec_money_refresh.py owns measured money in congestion.json.',
       'per chain run', 2, 'sentinel_files',
       # CHAIN-WRITTEN (DE-011), and the registry already SAYS so: cadence is 'per chain run'.
       # That declaration is NOT reused as the switch, deliberately — widening stable_cadence
       # would make the WHOLE store existence-only and silently drop byte evidence on
       # repair_sublayer_plan.json and congestion.json, which do not change.
       #   staffer_network_section.json  fec/surfaces/build_staffer_network_section.py:918
       #                                 (write_atomic)   run_pipeline.py:266  by_size rank 1, sha256 red
       #   responsiveness_layers.json    fec/engine/responsiveness_layers.py:647 (dump_atomic)
       #                                                  run_pipeline.py:172  by_size rank 3, size red
       #   efficiency_layers.json        fec/engine/efficiency_layers.py:862 (dump_atomic onto the
       #                                 :23 OUTPUT_PATH) run_pipeline.py:228  would take rank 3 next
       # by_time was dashboard_integrity_state.json (fec/audit/dashboard_integrity.py:571
       # MC.dump_atomic onto the state_path built at :554), reached because chain step
       # make_member_desk.py (run_pipeline.py:291) imports dashboard_integrity at
       # fec/surfaces/make_member_desk.py:29.
       #   per_area_grade.json           fec/engine/per_area_grade.py:642 (dump_atomic onto OUT_PATH :76),
       #                                 run as a subprocess by build_primacy_surfaces.py:57 (ENGINE_INPUT_STEPS),
       #                                 chain step fec/run_pipeline.py:162; size red after the 2026-10-11 chain
       sentinel_excl=['per_area_grade.json', 'staffer_network_section.json', 'responsiveness_layers.json',
                      'efficiency_layers.json', 'congestion.json',
                      'staffer_offices_section.json', 'staffer_organisations_section.json',
                      'staffer_clients_section.json', 'source_to_dashboard_receipt.json',
                      'live_dashboard_alias_receipt.json', 'live_legislative_pillar.json', 'primacy_factors.json',
                      'cosponsor_forecast_refresh_receipt.json', 'weekly_refresh.log',
                      # Native daily refresh receipts/progress and the canonical
                      # workbook authoring workspace are regenerated outputs.
                      # Keep stable model/policy inputs as sentinel candidates.
                      'congress_pipeline_status.json', 'congress_pipeline_receipt.json',
                      'daily_refresh_state.json', 'daily_refresh_observations/*',
                      'daily_media_refresh_receipt.json', 'daily_media_refresh_success.json',
                      'social_source_receipts/*', 'workbook_measurement_sync.json',
                      'workbook_measurement_staging/*', 'workbook_measurement_preview/*'],
       sentinel_newest=False),
    _s('repo.data_district', 'live_tree', 'data/district', 'DERIVED',
       'fec/district/*',
       ['district industry surfaces'],
       'Regenerate from the demographics cache at %LOCALAPPDATA%\\Melange\\demographics.',
       'rare', 1, 'sentinel_files'),
    _s('repo.data_reference', 'live_tree', 'data/reference', 'DERIVED',
       'reference-layer builder',
       ['desk bill repository tab'],
       'Rebuild the reference layer (2400s timeout).',
       'rare', 1, 'sentinel_files'),
    _s('repo.outputs', 'live_tree', 'outputs', 'DERIVED',
       'lane scripts (per-run output directories)',
       ['humans reading lane records'],
       'Per-run outputs; individually re-derivable only by re-running that lane.',
       'per lane run', 2, 'sentinel_files',
       # CHAIN-WRITTEN (DE-011), and the ONLY store here that a path list cannot fix: its lane
       # directories are TIMESTAMPED, so the newest file has a different NAME every run
       # (email_package/EMAIL_PACKAGE_MANIFEST.json at 19:35; a sentinel_bill_ledger_<ts>/ file
       # two hours later). Only the by_time opt-out closes it. email_package/ is named as well
       # because make_email_package.py is a chain step (fec/run_pipeline.py:295) with a STABLE
       # path: fec/surfaces/make_email_package.py:109 defaults out_dir to outputs/email_package,
       # :112-113 write the two zips into it and :152 writes EMAIL_PACKAGE_MANIFEST.json there.
       # NOTE: 38k files puts this store above the checker's WALK_LIMIT, so A2 is skipped and A2b
       # is its only content evidence — see the _by_hashable route below.
       sentinel_excl=['email_package'], sentinel_newest=False),

    # ---- live tree: databases (sqlite, sidecar-bearing) ---------------------------------------
    _s('repo.db_hearings_seq', 'live_tree', 'hearings_seq.db', 'DERIVED',
       'fec/collect/fetch_hearings_seq.py:22 (SEQDB)',
       ['fec/members/b5_audio_align_turns.py:115', 'fec/members/fetch_hearings_media.py',
        'fec/members/hearing_antagonism_build.py', 'fec/members/hearing_substance_prep.py'],
       'Re-collect from GovInfo CHRG; expensive. Note the 65,912 cross-chamber turns already in it '
       'stay misattributed until the corpus is re-derived.',
       'on hearings refresh', 3, 'hash_never'),
    _s('repo.db_hearings_wit', 'live_tree', 'hearings_wit.db', 'DERIVED',
       'NO PRODUCER IN THE MAIN TREE. The only writer, fec/fetch_hearings_witness.py:5,32, and its '
       'only reader, fec/hearing_features_candidate.py:27, exist solely in the UNMERGED worktree '
       '.claude/worktrees/melange-coalition-builder-design-7babbe.',
       ['NONE in the main tree — zero references in any fec/**/*.py or any main-tree .md'],
       'Merge (or re-run from) the coalition-builder worktree. Nothing in the main tree can '
       'recreate or consume it.',
       'orphaned', 3, 'hash_never',
       note='ORPHANED: 465 MiB in the main tree with producer and consumer both stranded in an '
            'unmerged worktree. Flagged for the containerization plan as an unowned artifact — '
            'a decision (merge the lane, or retire the file), not a measurement gap.'),
    _s('repo.db_social_media', 'live_tree', 'social_media.db', 'DERIVED',
       'fec/collect/tweet_collector.py:31, fec/collect/fetch_congressional_record.py:35, '
       'fec/collect/fetch_hearings_antag.py:185, fec/collect/fetch_hearings_seq.py:262 '
       '(hearing_meta only), fec/collect/collector_supervisor.py:64, '
       'fec/members/{load_cspan,rebuild_cspan_turns}.py',
       ['fec/members/* B5 lanes', 'fec/lobbying/org_mentions.py:162',
        'fec/audit/audit_full.py:244-249,1146-1148'],
       'Re-collect; Quorum history is not fully re-obtainable.',
       'on social refresh', 3, 'hash_never'),

    # ---- live tree: code, tooling and governance ----------------------------------------------
    _s('repo.fec_code', 'live_tree', 'fec', 'OPS',
       'humans + the Codex executor lane',
       ['everything'],
       'Git-tracked; recover from the repo history. Note the only mirror is on this same disk.',
       'per commit', 3, 'sentinel_files', excl=['__pycache__', '*/__pycache__/*', '.pytest_cache'],
       # BK-S2 (2026-10-11, audits/decisions_20261011/D_backlog.md): the newest file here is
       # git-tracked source, so a newest-file pin detects the last commit, not corruption (the
       # D-014 reasoning; DE-011 family). The largest-file slots stay.
       sentinel_newest=False),
    # The four stores below are git-TRACKED code/protocol.  They declare hash_never, not because
    # they are unimportant but because git already holds their integrity: the blob sha is the hash
    # and `git status` is the detector.  Hashing them here bought nothing and cost a manifest that
    # went stale on every commit (this branch's own edits made fec/audit and fec/tests disagree
    # with their own hash set mid-session).  Paul's 2026-08-27 ruling: use the narrower scope.
    # Nor sentinel_files: a sentinel set includes the NEWEST file, which is exactly what a commit
    # changes.  If one of these ever reverts to hash_always, git_scope_refusal() refuses it.
    _s('repo.fec_audit', 'live_tree', 'fec/audit', 'OPS',
       'humans',
       ['gates', 'this producer'],
       'Git-tracked; recover from repo history.',
       'per commit', 3, 'hash_never', count=False,
       excl=['__pycache__', '*/__pycache__/*'],
       note='hash_never by SCOPE, not by scale: git tracks it, so git is the integrity authority.'),
    _s('repo.fec_tests', 'live_tree', 'fec/tests', 'OPS',
       'humans',
       ['fec/tests/run_tests.py kernel gate'],
       'Git-tracked; recover from repo history.',
       'per commit', 3, 'hash_never', count=False,
       excl=['__pycache__', '*/__pycache__/*', '.pytest_cache'],
       note='hash_never by SCOPE, not by scale: git tracks it, so git is the integrity authority.'),
    _s('repo.claude_commands', 'live_tree', '.claude/commands', 'OPS',
       'humans',
       ['Claude Code slash commands'],
       'Git-tracked; hand-authored lane protocol.',
       'rare', 4, 'hash_never',
       note='hash_never by SCOPE, not by scale: git tracks it, so git is the integrity authority.'),
    _s('repo.claude_skills', 'live_tree', '.claude/skills', 'OPS',
       'humans',
       ['Claude Code skill library — the change-control constitution'],
       'Git-tracked; hand-authored. Recreating it means recreating the project constitution.',
       'rare', 4, 'hash_never',
       note='hash_never by SCOPE, not by scale: git tracks it, so git is the integrity authority. '
            'The store also carries untracked residue (.bak files, __pycache__), which is why '
            'tracked-ness is judged on whether git protects the STORE, not on every last file.'),
    _s('repo.audits', 'live_tree', 'audits', 'OPS',
       'lane sessions (dated records)',
       ['humans', 'the brain'],
       'FROZEN VINTAGE: dated records preserved as written. Not regenerable at all — a lost audit '
       'record is lost evidence, not a recomputable artifact.',
       'per lane record', 4, 'hash_always',
       note='Declares hash_always ON MERIT: frozen, non-mutating, and the least regenerable material '
            'in the tree. The envelope will refuse it on scale and record the refusal — that gap '
            '(what warrants hashing vs what can afford it) is exactly what the containerization '
            'plan needs to see, so it is declared honestly rather than pre-softened. It is ALSO '
            'partly git-tracked (39k of 297k files), but the envelope is checked first on purpose '
            'so this row keeps reporting SCALE and keeps its sentinel evidence: 258k of its files '
            'are untracked bulk that no git anywhere holds.'),
    _s('repo.worktrees', 'live_tree', '.claude/worktrees', 'OPS',
       'git worktree (other lanes)',
       ['other lanes in flight'],
       'Each worktree is recoverable from its branch, but UNCOMMITTED work in one is not.',
       'continuous', 2, 'sentinel_files', count=False, cap=500000,
       note='NOT counted toward totals: these are lane-local copies of the same repo plus their own '
            'data planes, so counting them would double-count the tree they are checked out from '
            'and inflate the extinction figure. Measured anyway because the disk footprint is real '
            'and uncommitted lane work has no other copy.'),
    _s('repo.git', 'live_tree', '.git', 'SNAPSHOT',
       'git',
       ['every lane'],
       'The repo history itself. Its only other copy is the bare mirror in ~\\GitMirrors, which is '
       'on this same physical disk.',
       'per commit', 4, 'sentinel_files'),

    # ---- live tree: the 2026-08-26 off-box rescue ---------------------------------------------
    _s('repo.rescue_dir', 'live_tree', 'backups/offbox_rescue_20260826', 'SNAPSHOT',
       'ad-hoc rescue run 2026-08-26 (COPY-ONLY; no source was moved or modified)',
       ['disaster recovery'],
       'Itself the restore route for the off-OneDrive stores it covers. It lives inside OneDrive, '
       'so it is cloud-replicated — replication, not backup.',
       'one-off', 4, 'sentinel_files'),
    _s('repo.rescue_manifest_a', 'live_tree', 'backups/offbox_rescue_20260826/RESCUE_MANIFEST.json',
       'SNAPSHOT', 'rescue phase A',
       ['disaster recovery', 'this producer'],
       'The evidence of WHAT was copied (74 files, 74/74 sha256-verified, 4 credentials '
       'deliberately excluded). Without it the archive is bytes without provenance.',
       'one-off', 4, 'hash_always', count=False),
    _s('repo.rescue_manifest_b', 'live_tree',
       'backups/offbox_rescue_20260826/RESCUE_MANIFEST_PHASE_B.json', 'SNAPSHOT', 'rescue phase B',
       ['disaster recovery'],
       'Evidence for the four compressed sets (lobbyview/positions/sweeps/derived) and the '
       'credential-exclusion enforcement claim.',
       'one-off', 4, 'hash_always', count=False),
    _s('repo.rescue_manifest_c', 'live_tree',
       'backups/offbox_rescue_20260826/RESCUE_MANIFEST_PHASE_C.json', 'SNAPSHOT', 'rescue phase C',
       ['disaster recovery'],
       'Evidence for the 8 media sets (2,270 files, 39.0 GiB) copied byte-for-byte with '
       'source-and-destination sha256 verification.',
       'one-off', 4, 'hash_always', count=False),

    # ---- live tree: SECRETS (path/size/mtime only — never read, never hashed) -----------------
    _s('secret.anthropic_key', 'live_tree', 'fec/anthropic_key.txt', 'SECRET',
       'human',
       ['fec/* LLM call sites'],
       'Reissue from the Anthropic console. Never restore from a backup — rotate instead.',
       'on rotation', 0, 'presence_only', count=False),
    _s('secret.govinfo_key', 'live_tree', 'fec/govinfo_key.txt', 'SECRET',
       'human',
       ['GovInfo collectors'],
       'Reissue from api.data.gov. Never restore from a backup — rotate instead.',
       'on rotation', 0, 'presence_only', count=False),

    # ---- Draft Systems (R press pipeline; inside OneDrive) ------------------------------------
    _s('draft.root', 'draft_systems', '.', 'SOURCE',
       'the R press/collection pipeline (cu_press_release_scraper.R et al.)',
       ['fec/moneyball_core.py via DRAFT', 'press pillar', 'stance stack'],
       'Re-scrape; the CU press corpus and its adjudicated label set are the expensive part.',
       'on press refresh', 3, 'sentinel_files',
       excl=['.venv_debate', '*/.venv_debate/*', '__pycache__', '*/__pycache__/*']),
    _s('draft.stance_label_set', 'draft_systems', 'cu_stance_label_set.csv', 'SOURCE',
       'human adjudication (the gold label set of record)',
       ['stance training', 'accuracy reports'],
       'Not recoverable: these are adjudicated labels. The .xlsx beside it is a mirror, not a source.',
       'on adjudication', 4, 'hash_always', count=False,
       note='The gold label set of record. cu_stance_label_set.csv in Draft Systems is the source; '
            'the .xlsx is a mirror of it.'),
    _s('secret.renviron', 'draft_systems', '.Renviron', 'SECRET',
       'human',
       ['the R pipeline'],
       'Reissue the keys it holds. Never restore from a backup — rotate instead.',
       'on rotation', 0, 'presence_only', count=False),

    # ---- C:\melange-data (OUTSIDE OneDrive) ---------------------------------------------------
    _s('mdata.npr_audio', 'melange_data', 'npr_audio', 'SOURCE',
       'NPR collector (speech lane)',
       ['fec/audio/*', 'speech reaction profiles'],
       'Re-download from NPR; 678 episodes. Copied to backups/offbox_rescue_20260826/media/npr_audio '
       '(7.24 GB, sha256-verified both sides) on 2026-08-26.',
       'on collection', 2, 'sentinel_files'),
    _s('mdata.wj_audio', 'melange_data', 'wj_audio', 'SOURCE',
       'Washington Journal collector (speech lane)',
       ['fec/audio/*', 'wj featurizer'],
       'Re-scrape C-SPAN; 1,243 clips across two pad conventions. Rescued 2026-08-26 (2.79 GB).',
       'on collection', 3, 'sentinel_files',
       note='Two clip families with different pad conventions (turns/ unpadded, turns_v2/ ~20s '
            'stimulus pad). Any featurizer must anchor per family.'),
    _s('mdata.sunday_audio', 'melange_data', 'sunday_audio', 'SOURCE',
       'Sunday-show collector (speech lane)',
       ['fec/audio/*'],
       'Re-scrape; 144 files. Rescued 2026-08-26 (13.09 GB).',
       'on collection', 2, 'sentinel_files'),
    _s('mdata.axe_audio', 'melange_data', 'axe_audio', 'SOURCE',
       'interview collector (speech lane)',
       ['fec/audio/*'],
       'Re-scrape; 54 files. Rescued 2026-08-26 (5.70 GB).',
       'on collection', 2, 'sentinel_files'),
    _s('mdata.youtube_audio', 'melange_data', 'youtube_audio', 'SOURCE',
       'YouTube collector (speech lane)',
       ['fec/audio/*'],
       'Re-download; 94 files. Rescued 2026-08-26 (1.06 GB).',
       'on collection', 1, 'sentinel_files'),
    _s('mdata.sunday_pilot', 'melange_data', 'sunday_pilot', 'DERIVED',
       'Sunday segmentation pilot',
       ['profile pilot'],
       'Re-run segmentation over sunday_audio. Rescued 2026-08-26 (773 MB).',
       'per pilot run', 1, 'sentinel_files'),
    _s('mdata.bicam_zips', 'melange_data', 'bicam_snapshot/zips', 'SOURCE',
       'external (BICAM public drop) — downloaded, not produced here',
       ['fec/tools/b0_bicam_arrival_audit.py'],
       'Re-download from the BICAM public drop; 6/6 sha256 pins recorded. Rescued 2026-08-26 '
       '(11.28 GB across 7 zips).',
       'rare', 1, 'sentinel_files',
       note='Mixed-vintage public drop (members early-118th, a 131-bill 119th sliver); the sha256 '
            'pins are what make a re-download checkable.'),
    _s('mdata.bicam_extracted', 'melange_data', 'bicam_snapshot/extracted', 'DERIVED',
       'extraction of bicam_snapshot/zips',
       ['fec/tools/b0_bicam_arrival_audit.py'],
       'Re-extract from the zips (amendments_texts.csv alone is ~28.6 GB raw).',
       'rare', 0, 'sentinel_files'),
    _s('mdata.models', 'melange_data', 'models', 'DERIVED',
       'model training lanes',
       ['stance / audio inference'],
       'Retrain. A retrain is not a byte-restoration.',
       'on retrain', 2, 'sentinel_files'),
    _s('mdata.golden_run', 'melange_data', 'golden_run', 'SNAPSHOT',
       'golden-fixture drivers',
       ['golden fixture tests'],
       'Regenerate golden fixtures from a pinned run.',
       'rare', 2, 'sentinel_files'),
    _s('mdata.residual', 'melange_data', '.', 'OPS',
       'lane sessions (ad-hoc scratch scripts and scratch directories written outside the repo)',
       ['their own lanes'],
       'Hand-written and NEVER version-controlled. The 74 root-level scripts were copied into '
       'backups/offbox_rescue_20260826/melange_data_scripts on 2026-08-26 — that copy is the only '
       'record of them. The unclaimed lane scratch directories folded in here were NOT rescued.',
       'continuous', 3, 'sentinel_files',
       excl=['npr_audio', 'wj_audio', 'sunday_audio', 'axe_audio', 'youtube_audio', 'sunday_pilot',
             'bicam_snapshot', 'models', 'golden_run', 'chrome_debug_profile_20260817'],
       note='C:\\melange-data MINUS the stores named above it. Deliberately a residual so nothing '
            'in that root goes uninventoried; its own backups/ subtree is included because nothing '
            'else claims it.'),

    # ---- %LOCALAPPDATA%\Melange (OUTSIDE OneDrive) --------------------------------------------
    _s('local.lda_sweeps', 'local_melange', 'lda_rebuild/raw/sweeps', 'SOURCE',
       'LDA sweep collectors',
       ['fec/lobbying/*', 'LobbyView network'],
       'Re-sweep the LDA API (21,605 files, rate-limited, months of collection). Rescued '
       '2026-08-26 as lda_sweeps_20260826.zip (293 MB compressed from 2.33 GB).',
       'on LDA refresh', 4, 'sentinel_files'),
    _s('local.lda_positions', 'local_melange', 'lda_rebuild/raw/positions', 'SOURCE',
       'LDA position collectors',
       ['fec/lobbying/*'],
       'Re-collect (25 files, 113 MB). Rescued 2026-08-26 as lda_positions_20260826.zip.',
       'on LDA refresh', 3, 'sentinel_files'),
    _s('local.lda_derived', 'local_melange', 'lda_rebuild/derived', 'DERIVED',
       'LDA rebuild lane',
       ['fec/lobbying/*'],
       'Re-derive from raw sweeps/positions (123 files, 751 MB). Rescued 2026-08-26 as '
       'lda_derived_20260826.zip.',
       'on LDA refresh', 2, 'sentinel_files',
       # CHAIN-WRITTEN (DE-011): chain step org_mentions.py (fec/run_pipeline.py:234) writes
       # org_mentions/mentions.{json,csv} into THIS off-repo store — fec/lobbying/org_mentions.py
       # :1659 (json tmp + os.replace) and :1712 (csv tmp + os.replace), onto the paths its
       # argparse defaults declare at :1731-1733 — and mentions.csv was by_time at 15:47. Its
       # three by_size rows are all 60-170 MB, i.e. over the hash cap, so without the by_hashable
       # route below this store would keep ZERO byte evidence.
       # 2026-10-11: cosponsor_sc/{cosponsor_scores.csv,cosponsor_forecast_report.json} are rewritten by
       # fec/lobbying/cosponsor_forecast.py:2695-2697 (write_outputs), run as a subprocess by chain step
       # refresh_cosponsor_forecast.py (fec/run_pipeline.py:243; out-dir at refresh_cosponsor_forecast.py:74-76)
       # whenever COSPONSOR_FORECAST_REFRESH=1 (weekly_refresh.py:396 sets it); scores.csv size red 10-11.
       sentinel_excl=['cosponsor_sc/*'], sentinel_newest=False),
    _s('local.billstatus', 'local_melange', 'lda_rebuild/raw', 'SOURCE',
       'BILLSTATUS bulk collectors',
       ['fec/build_holistic_base.py', 'legislation lane'],
       'Re-download the GovInfo BILLSTATUS bulk archive. COMPLETE BY CONSTRUCTION for 117-118 '
       '(gap=0 on all 16 congress x type strata) at its 2026-08-01 vintage.',
       # DELIBERATELY NOT FLAGGED. Its A2b red is real but COLLECTOR-driven: BILLSTATUS-119.receipt
       # .json is written by weekly_refresh's GovInfo bulk step, and the only chain step that
       # touches these directories, refresh_cosponsor_forecast.py, merely READS them (:146-151,
       # :208-221) and puts its own receipt under data/engine (path resolved at :85). Flagging it
       # would reduce integrity coverage for a failure this change does not address.
       'per Congress', 3, 'sentinel_files',
       count=False, excl=['sweeps', 'sweeps/*', 'positions', 'positions/*'],
       note='Overlaps local.lda_sweeps / local.lda_positions, which are excluded from this walk; '
            'not counted toward totals to avoid double-counting the rest.'),
    _s('local.lobbyview_raw', 'local_melange', 'lobbyview/raw', 'SOURCE',
       'LobbyView bulk download',
       ['fec/lobbying/lobbyview_*'],
       'Re-download from LobbyView (14 files, 1.11 GB). Rescued 2026-08-26 as '
       'lobbyview_raw_20260826.zip (160 MB compressed).',
       'rare', 2, 'sentinel_files'),
    _s('local.lobbyview_derived', 'local_melange', 'lobbyview/derived', 'DERIVED',
       'LobbyView integration lane',
       ['network repository'],
       'Re-derive from lobbyview/raw.',
       'rare', 1, 'sentinel_files'),
    _s('local.demographics', 'local_melange', 'demographics', 'SOURCE',
       'ACS/census collectors',
       ['fec/district/demographic_potential.py'],
       'Re-download from Census/ACS + QCEW. NOT rescued 2026-08-26 — no off-box copy exists.',
       'annual', 2, 'sentinel_files'),
    _s('local.fec_spending', 'local_melange', 'fec_spending', 'SOURCE',
       'FEC collectors',
       ['money engines', 'electoral layer'],
       'Re-download from the FEC bulk API (per-DATABASE budget). NOT rescued 2026-08-26 — no '
       'off-box copy exists.',
       'per cycle', 2, 'sentinel_files'),
    _s('secret.lda_anthropic_key', 'local_melange', 'lda_rebuild/anthropic_key.txt', 'SECRET',
       'human', ['LDA rebuild LLM calls'],
       'Reissue from the Anthropic console. Never restore from a backup — rotate instead.',
       'on rotation', 0, 'presence_only'),
    _s('secret.lda_api_key', 'local_melange', 'lda_rebuild/lda_api_key.txt', 'SECRET',
       'human', ['LDA collectors'],
       'Reissue from the LDA/Senate API. Never restore from a backup — rotate instead.',
       'on rotation', 0, 'presence_only'),

    # ---- GitMirrors (OUTSIDE OneDrive, and on the SAME physical disk) -------------------------
    _s('mirror.melange', 'git_mirrors', 'melange-systems.git', 'SNAPSHOT',
       'git push mirror',
       ['disaster recovery for git-tracked work'],
       'Re-push from the live tree. NOTE: this is a LOCAL bare repo on C:, i.e. on the same '
       'physical disk as the live tree — it is not an off-box copy despite carrying all 174 '
       'branches and 3 tags.',
       'per push', 3, 'sentinel_files',
       note='Named "mirror" but resolves to a path on Disk 0. Redundancy against accidental '
            'deletion, not against disk loss.'),
    _s('mirror.glassbox', 'git_mirrors', 'glassbox-runtime.git', 'SNAPSHOT',
       'git push mirror',
       ['disaster recovery for the glassbox runtime'],
       'Re-push from ~\\.claude\\glassbox. Same disk as everything else.',
       'per push', 3, 'sentinel_files'),

    # ---- glassbox runtime (OUTSIDE OneDrive, largely untracked) -------------------------------
    _s('glassbox.scripts', 'glassbox', '.', 'OPS',
       'humans (built 2026-08-14 and after)',
       ['every orchestrated lane'],
       'Hand-written PowerShell. A bare mirror exists at ~\\GitMirrors\\glassbox-runtime.git — on '
       'the same disk. Rescued 2026-08-26 into backups/offbox_rescue_20260826/glassbox_scripts.',
       'on tooling change', 4, 'hash_always',
       excl=['events', 'state', 'streams', 'backups', 'inbox', 'outbox', 'hookcap', '.git',
             '*.lock', '*.log'],
       note='Lock files, logs and .git are excluded from the walk on purpose: they mutate while a '
            'lane runs, and a hash set that churns every run is a check nobody can read. Declares '
            'hash_always ON MERIT and is REFUSED by the git scope rule: ~/.claude/glassbox is its '
            'OWN git repo and all 15 walked files are tracked in it. The scope rule is about '
            'integrity authority, not disk redundancy — that repo is still single-disk, which is '
            'what the extinction section is for.'),
    _s('glassbox.events', 'glassbox', 'events', 'OPS',
       'Write-GlassboxEvent / Invoke-GlassboxRetention (common.ps1)',
       ['glassbox dashboard', 'fanout.ps1', 'agentwatch.ps1'],
       'NOT regenerable. This is a rolling event window, not a permanent archive: common.ps1 '
       'retains the newest 5000 event files and removes older files at five-minute intervals. '
       'Gitignored by the glassbox repo. Historical rescue: glassbox_events_20260826.zip.',
       'continuous with retention', 4, 'hash_never',
       note='Individual event paths retire under the installed retention policy. A1/A2 still '
            'require the store, its measured size class and file count within tolerance. '
            'Retain the single-disk and non-regenerability findings; individual retired event '
            'filenames cannot serve as permanent sentinels.'),
    _s('glassbox.state', 'glassbox', 'state', 'OPS',
       'glassbox runtime',
       ['metaloop.ps1', 'health.ps1'],
       'Rebuilt by the runtime on next use, but in-flight run state is lost. Rescued 2026-08-26 '
       'inside glassbox_events_20260826.zip.',
       'continuous', 2, 'sentinel_files'),
)

# ---------------------------------------------------------------------------------------------
# ABSENT-EXPECTED.  Negative facts: things whose APPEARANCE is the signal.  Recorded as globs so
# the audit can re-run the same query against live disk instead of trusting a remembered claim.
# severity 'notice' -> the audit reports a NOTICE; 'alarm' -> the audit FAILS.
# ---------------------------------------------------------------------------------------------
ABSENT_EXPECTED = (
    {'id': 'no_credentials_in_offbox_rescue', 'root_id': 'live_tree',
     'base': 'backups/offbox_rescue_20260826', 'severity': 'alarm',
     'patterns': ('*key*.txt', '*token*', '*secret*', '*credential*', '.Renviron', '*.pem', '*.env'),
     'why': 'RESCUE_MANIFEST_PHASE_B claims credential exclusion was enforced during the walk AND '
            're-scanned inside each finished archive. This directory is inside OneDrive, so a '
            'credential landing here is a credential uploaded to the cloud. Re-checked, not trusted.'},
    {'id': 'no_network_json_at_root', 'root_id': 'live_tree', 'base': '.', 'severity': 'notice',
     'patterns': ('network.json',), 'depth': 1,
     'why': 'The holistic Gate-0 census verified repo-wide that no network.json exists; the network '
            'plane is the congress_cosponsor_network_*/holistic_network_* families only. A '
            'network.json appearing means some producer invented a third plane.'},
    {'id': 'no_leaked_atomic_temp_in_data_system', 'root_id': 'live_tree', 'base': 'data/system',
     'severity': 'notice', 'patterns': ('*.tmp',),
     'why': 'dump_atomic writes a temp sibling then os.replace()s it. A surviving *.tmp beside the '
            'manifest means an atomic write died mid-flight and the guarantee did not hold.'},
)


# =============================================================================================
# SECRET GUARD.  Every routine that would open a store file for CONTENT calls this first.
# =============================================================================================
class SecretAccessError(RuntimeError):
    pass


def _secret_path(path):
    """Protect declared secret paths through canonical overlaps and symlinks."""
    target = os.path.normcase(os.path.realpath(path))
    roots = {row['id']: row['path'] for row in ROOTS}
    for spec in REGISTRY:
        if spec.get('class') != 'SECRET':
            continue
        secret = os.path.normcase(os.path.realpath(os.path.join(
            roots[spec['root_id']], spec['rel'].replace('/', os.sep))))
        if target == secret or target.startswith(secret + os.sep):
            return True
    return False


def _forbid_secret(store, what, path=None):
    """Hard guard: a SECRET store's bytes are never read, hashed, or archived by this producer."""
    if store.get('class') == 'SECRET' or (path is not None and _secret_path(path)):
        raise SecretAccessError(
            'SECRET GUARD: refused to %s for store %s (%s). Secrets yield path, size and mtime only.'
            % (what, store.get('id'), store.get('path')))


def sha256_file(path, store):
    _forbid_secret(store, 'sha256 a file', path)
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# =============================================================================================
# MEASUREMENT
# =============================================================================================
def size_class(n):
    if n <= 0:              return 'empty'
    if n < (1 << 20):       return 'tiny'
    if n < (10 << 20):      return 'small'
    if n < (100 << 20):     return 'medium'
    if n < (1 << 30):       return 'large'
    if n < (10 << 30):      return 'huge'
    return 'massive'


def _excluded(rel, excludes):
    """A pattern WITHOUT a slash matches by basename at any depth (so '__pycache__' prunes nested
    ones too); a pattern WITH a slash matches the relpath, or its directory prefix."""
    if not excludes:
        return False
    base = rel.rsplit('/', 1)[-1]
    for pat in excludes:
        if '/' in pat:
            if fnmatch.fnmatch(rel, pat):
                return True
            trimmed = pat.rstrip('*').rstrip('/')
            if trimmed and (rel == trimmed or rel.startswith(trimmed + '/')):
                return True
        else:
            if fnmatch.fnmatch(base, pat):
                return True
            if rel == pat or rel.startswith(pat + '/'):
                return True
    return False


_TOP_KEEP = SENTINEL_COUNT * 8


def walk_store(path, excludes, cap, budget):
    """Bounded walk.  Sentinel candidates (largest-by-size, newest-by-mtime) are maintained
    STREAMING, so a store of any size still yields sentinels; the full file list is retained only
    while the store is still small enough to be hashable at all.  BUDGET-EXCEEDED is explicit and
    recorded — this walk never silently truncates."""
    files = dirs = 0
    total = 0
    newest = None
    oldest = None
    listing = []
    keep_listing = True
    top = []                     # bounded largest-by-size candidates
    newest_file = None           # (rel, bytes, mtime) with the greatest mtime
    truncated = False
    unreadable = 0
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            unreadable += 1
            continue
        dirs += 1
        for e in entries:
            try:
                rel = os.path.relpath(e.path, path).replace('\\', '/')
            except ValueError:
                continue
            if _excluded(rel, excludes):
                continue
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                unreadable += 1
                continue
            if is_dir:
                stack.append(e.path)
                continue
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                unreadable += 1
                continue
            files += 1
            total += st.st_size
            m = st.st_mtime
            newest = m if newest is None or m > newest else newest
            oldest = m if oldest is None or m < oldest else oldest
            row = (rel, st.st_size, m)
            if newest_file is None or m > newest_file[2]:
                newest_file = row
            top.append(row)
            if len(top) > _TOP_KEEP * 4:
                top = sorted(top, key=lambda t: (-t[1], t[0]))[:_TOP_KEEP]
            if keep_listing:
                listing.append(row)
                if len(listing) > HASH_ALWAYS_MAX_FILES:
                    keep_listing = False       # beyond the envelope: hash_always is unreachable
                    listing = []
            if files >= cap or (budget['used'] + files) >= budget['total']:
                truncated = True
                stack = []
                break
    budget['used'] += files
    top = sorted(top, key=lambda t: (-t[1], t[0]))[:_TOP_KEEP]
    return {'file_count': files, 'dirs_walked': dirs, 'bytes': total,
            'newest_mtime_epoch': newest, 'oldest_mtime_epoch': oldest,
            'unreadable_entries': unreadable, 'walk_complete': not truncated and unreadable == 0,
            'budget_exceeded': truncated,
            '_listing': listing if keep_listing else None,
            '_top': top, '_newest_file': newest_file}


def measure(store, budget):
    """Measure one store from the filesystem.  Never authors a value it could read."""
    path = store['path']
    exists = os.path.exists(path)
    out = {'present': exists, 'kind': None, 'file_count': 0, 'dirs_walked': 0, 'bytes': 0,
           'size_class': 'empty', 'newest_mtime_epoch': None, 'oldest_mtime_epoch': None,
           'unreadable_entries': 0, 'walk_complete': True, 'budget_exceeded': False}
    listing, cands = [], []
    if not exists:
        return out, listing, cands
    if os.path.isdir(path):
        out['kind'] = 'dir'
        w = walk_store(path, store['excludes'], store.get('cap') or STORE_FILE_CAP, budget)
        listing = w.pop('_listing') or []
        top = w.pop('_top') or []
        newest_file = w.pop('_newest_file')
        cands = list(top) + ([newest_file] if newest_file and newest_file[0] not in {t[0] for t in top} else [])
        out.update(w)
    else:
        out['kind'] = 'file'
        st = os.stat(path)
        out.update({'file_count': 1, 'dirs_walked': 0, 'bytes': st.st_size,
                    'newest_mtime_epoch': st.st_mtime, 'oldest_mtime_epoch': st.st_mtime})
        listing = [(os.path.basename(path), st.st_size, st.st_mtime)]
        cands = list(listing)
        budget['used'] += 1
    out['size_class'] = size_class(out['bytes'])
    return out, listing, cands


def sqlite_sidecars(store):
    """A sqlite database is three files, not one: a stranded -wal is unreplayed committed data."""
    path = store['path']
    if store['measured']['kind'] != 'file' or not path.lower().endswith(('.db', '.sqlite', '.sqlite3')):
        return None
    info = {'db_bytes': store['measured']['bytes']}
    for suffix, key in (('-wal', 'wal'), ('-shm', 'shm')):
        side = path + suffix
        if os.path.exists(side):
            st = os.stat(side)
            info[key + '_present'] = True
            info[key + '_bytes'] = st.st_size
            info[key + '_mtime_epoch'] = st.st_mtime
        else:
            info[key + '_present'] = False
            info[key + '_bytes'] = None
            info[key + '_mtime_epoch'] = None
    return info


def is_git_dir(path):
    """MEASURED, never declared: does this path have the SHAPE of a git directory?

    True for a work tree's `.git` and for a bare mirror alike — both carry HEAD + objects/ + refs/.
    Nothing is inferred from the store's id or from a `.git` suffix on its name, because a registry
    row that could TYPE this flag could also type it wrong and switch off the checks below."""
    try:
        if not os.path.isdir(path):
            return False
        for name, kind in GIT_DIR_MARKERS:
            probe = os.path.isfile if kind == 'file' else os.path.isdir
            if not probe(os.path.join(path, name)):
                return False
        return True
    except OSError:
        return False


def _git_dir_relative(rel, store_is_git_dir):
    """The part of `rel` that is relative to a GIT DIRECTORY, or None when it is not inside one.

    Two shapes, and both occur in this registry:
      * the STORE IS the git directory (repo.git = <root>/.git; mirror.* = a bare *.git), so the
        whole relpath is git-dir-relative;
      * the store CONTAINS one (repo.worktrees holds a `.git` per lane worktree), so everything
        after the last `.git` component is.
    Anywhere else `index` or `HEAD` is just a filename and must NOT be excluded.  Keeping this
    predicate narrow is what stops it becoming the blanket off-switch it exists to avoid; the
    controls in fec/tests/audit/test_system_manifest_check.py plant exactly that case."""
    parts = [p for p in str(rel).replace('\\', '/').split('/') if p not in ('', '.')]
    if '.git' in parts:
        return '/'.join(parts[len(parts) - parts[::-1].index('.git'):])
    return '/'.join(parts) if store_is_git_dir else None


def is_git_bookkeeping(rel, store_is_git_dir):
    """True when `rel` is git's OWN bookkeeping — rewritten by ordinary commits/pushes/fetches.

    Object files are excluded from this on purpose: they are content-addressed, so their bytes
    cannot change without their name changing.  See the GIT BOOKKEEPING block at the top."""
    inner = _git_dir_relative(rel, store_is_git_dir)
    if inner is None:
        return False
    if inner == '':
        return True                       # the `.git` entry itself (a worktree's gitdir pointer)
    if inner in GIT_DERIVED_INDEX_PATHS or inner.startswith(GIT_DERIVED_INDEX_PREFIXES):
        return True
    seg = inner.split('/')
    if seg[0] in GIT_BOOKKEEPING_DIRS:
        return True
    return len(seg) == 1 and (seg[0] in GIT_BOOKKEEPING_FILES or seg[0].endswith('.lock'))


def _volatile(rel, store_is_git_dir=False):
    """The ONE predicate that decides sentinel eligibility.  Both the selection filter and the
    per-row content_stable flag below are derived from it, so a path this call rejects can never
    reach the manifest carrying content_stable:true — which is the exact defect being fixed."""
    r = str(rel).replace('\\', '/')
    if any((frag in r) if frag.endswith('/') else r.endswith(frag) for frag in SENTINEL_EXCLUDE):
        return True
    if is_git_bookkeeping(r, store_is_git_dir):
        return True
    # REPACK-VOLATILE (2026-10-11).  A pack is named by the hash of the objects it HOLDS, not of
    # the history it protects, so `git gc` / `git repack` retires every pack and writes new ones
    # with new names: the store loses nothing and every pinned pack turns red (both A2b failures
    # on the 2026-10-08 manifest were exactly this).  Git-directory stores pin COMMITS instead
    # (git_commit_pins), which no repack can rename.  Canonical loose objects stay eligible: the
    # checker verifies a since-packed loose object by its canonical object hash.  Anything else
    # under objects/ is not content-addressed either: the 2026-10-11 rebuild pinned three orphaned
    # objects/xx/tmp_obj_* files (git writes left by crashes), which the next gc deletes.
    inner = _git_dir_relative(r, store_is_git_dir)
    return (inner is not None and inner.startswith('objects/')
            and not GIT_LOOSE_OBJECT_RE.fullmatch(inner))


#: the only sentinel-eligible path under a git directory's objects/: a canonical loose object
#: (SHA-1 or SHA-256 name).  Packs, their indexes and crash-orphaned tmp_obj_* files are not.
GIT_LOOSE_OBJECT_RE = re.compile(r'objects/[0-9a-f]{2}/(?:[0-9a-f]{38}|[0-9a-f]{62})')
#: the refs a git-directory store pins by commit: HEAD, the trunk, and every tag.  Lane branches
#: are not pinned: deleting a merged-or-abandoned lane is ordinary, and a pin on it would go red.
GIT_PIN_REFS = ('refs/heads/main', 'refs/heads/master', 'refs/tags')


def git_commit_pins(git_dir):
    """Sentinels for a git directory that survive gc/repack AND ordinary commits.

    Each row pins the COMMIT a ref resolved to when the manifest was built.  The checker asserts
    that commit is still reachable from the repository's current refs: a new commit or push keeps
    it reachable, a repack renames no commit, and a truncated, clobbered or history-rewritten
    repository loses it.  Returns (rows, None) or ([], reason)."""
    git = ['git', '--git-dir=' + git_dir]
    try:
        head = subprocess.run(git + ['rev-parse', '--verify', '--quiet', 'HEAD^{commit}'],
                              capture_output=True, text=True, timeout=60)
        refs = subprocess.run(git + ['for-each-ref', '--format=%(refname) %(objecttype) '
                                     '%(objectname) %(*objecttype) %(*objectname)']
                              + list(GIT_PIN_REFS), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return [], 'git unavailable: %s' % type(exc).__name__
    if refs.returncode != 0:
        return [], 'git for-each-ref failed: %s' % (refs.stderr or '').strip()[:120]
    pins = [('HEAD', head.stdout.strip())] if head.returncode == 0 and head.stdout.strip() else []
    for line in refs.stdout.splitlines():
        f = line.split() + ['', '', '', '']
        sha = f[2] if f[1] == 'commit' else (f[4] if f[3] == 'commit' else None)
        if sha:
            pins.append((f[0], sha))
    if not pins:
        return [], 'no HEAD, trunk or tag resolves to a commit'
    return [{'rel': None, 'git_ref': ref, 'git_commit': sha, 'bytes': None, 'mtime_epoch': None,
             'sha256': None, 'content_stable': True,
             'sha256_skipped_reason': 'git commit pin: checked by reachability from current refs, '
                                      'which gc/repack and new commits both preserve'}
            for ref, sha in pins], None


def _by_hashable(by_size, by_time, candidates, cap=SENTINEL_HASH_MAX_BYTES):
    """FALLBACK ROUTE: the largest candidate UNDER the hash cap, and only when nothing else is.

    A2b's three verdicts are existence, size and bytes.  A store whose every sentinel exceeds
    SENTINEL_HASH_MAX_BYTES gets only the first two — and where the store is also above the
    checker's WALK_LIMIT its A2 scale check is SKIPPED as well (system_manifest_check.py:518-529),
    so the store degenerates to A1 existence.  Two stores land exactly there once DE-011's chain
    exclusions apply: repo.outputs (three .f32 blobs of 0.8-1.1 GB) and local.lda_derived (three
    CSV/JSONL of 60-170 MB).  Recording ONE hashable file costs one sha256 of at most 8 MiB and is
    the difference between "this store still has bytes we can check" and "this store still exists".

    Narrow by construction: it fires only when the by_size/by_time picks yielded nothing hashable,
    so no store that already has byte evidence gains a row."""
    picked = list(by_size) + list(by_time)
    if any(b is not None and b <= cap for _rel, b, _m in picked):
        return []
    chosen = {rel for rel, _b, _m in picked}
    small = [c for c in candidates
             if c[0] not in chosen and c[1] is not None and c[1] <= cap]
    if not small:
        return []
    return sorted(small, key=lambda t: (-t[1], t[0]))[:1]


def pick_sentinels(store, candidates):
    """File sentinels, preceded inside a git directory by its commit pins (git_commit_pins)."""
    files = _pick_file_sentinels(store, candidates)
    if not store.get('git_dir_store'):
        return files
    pins, why = git_commit_pins(store['path'])
    if why:
        store['git_commit_pins_unavailable'] = why
    return pins + files


def _pick_file_sentinels(store, candidates):
    """DISCOVERED, not authored: the 3 largest files plus the newest, chosen from the streaming
    candidates the walk maintained.  Recording WHICH files were chosen is what turns them into an
    expectation the audit can re-check against disk."""
    _forbid_secret(store, 'select sentinel files')
    git_dir = bool(store.get('git_dir_store'))
    candidates = [c for c in candidates if not _volatile(c[0], git_dir)]
    before_secret_filter = len(candidates)
    candidates = [c for c in candidates if not _secret_path(
        store['path'] if store['measured']['kind'] == 'file'
        else os.path.join(store['path'], c[0]))]
    store['secret_sentinel_exclusion_count'] = before_secret_filter - len(candidates)
    # CHAIN OUTPUTS (DE-011).  A sentinel proves a store was not LOST or CORRUPTED.  A file the
    # pipeline chain rewrites on every run has no byte-stability invariant to assert, so pinning it
    # does not detect corruption — it detects the chain, minutes before the chain's own terminal
    # audit reads the pin.  That is the always-red twin of a never-fails check.  The files stay
    # walked and counted (loss detection for them rides A2 file_count, exactly as it does for the
    # continuous-cadence rows below); they are only ineligible to be PINNED.
    #
    # This is a PATH filter, not a store-level flag, on purpose.  A store-level relaxation would
    # also stop hashing the stable material that shares the directory, and — worse — a NEW chain
    # output would silently inherit the relaxation forever.  Excluding by path fails RED when the
    # declaration goes stale (the new output takes a slot and A2b reports it), which is the failure
    # direction to want.
    chain_excl = list(store.get('sentinel_excludes') or ())
    if chain_excl:
        kept = [c for c in candidates if not _excluded(c[0], chain_excl)]
        # FLOOR.  An exclusion that empties the pool would leave the store with NO content evidence
        # at all, which is a bigger hole than the false red it was fixing.  Refuse it and say so.
        if kept:
            matched = {c[0] for c in candidates} - {c[0] for c in kept}
            store['sentinel_exclusions_unmatched'] = [
                p for p in chain_excl if not any(_excluded(rel, [p]) for rel in matched)]
            candidates = kept
        else:
            store['sentinel_exclusions_refused'] = (
                'declared sentinel_excludes %r would leave no candidate; honouring it would delete '
                'this store\'s only content evidence. Selection proceeded on the unfiltered set.'
                % (chain_excl,))
    if not candidates:
        return []
    by_size = sorted(candidates, key=lambda t: (-t[1], t[0]))[:SENTINEL_COUNT]
    # The NEWEST file is a sentinel everywhere EXCEPT inside a git directory, where "newest" is by
    # construction whatever the last commit or push just wrote — a loose object git's own
    # maintenance will later pack away.  Pinning it re-creates one level down the staleness the
    # bookkeeping exclusion exists to remove; it is the same reasoning the registry already applies
    # to the tracked code stores ("a sentinel set includes the NEWEST file, which is exactly what a
    # commit changes").  The largest files — packs, their idx, loose objects — are content-addressed
    # and stay, so a git store keeps real evidence rather than losing sentinels altogether.
    #
    # DE-011 widens that opt-out from "the last commit" to "the last CHAIN RUN" via the registry's
    # sentinel_newest flag. Same argument, wider scope: for repo.outputs the newest file is not even
    # a REWRITE — every lane run creates a new timestamped directory, so the by_time pin names a
    # different file each time and no path list could ever keep up with it.
    by_time = ([] if (git_dir or not store.get('sentinel_newest', True))
               else sorted(candidates, key=lambda t: (-(t[2] or 0), t[0]))[:1])
    # A sentinel proves a store has not been LOST or CORRUPTED. For a store this registry declares
    # `cadence: continuous`, in-place rewriting IS normal operation, so a content mismatch is not
    # evidence of corruption and pinning bytes there asserts an invariant the store does not have.
    # Those sentinels are recorded for EXISTENCE only. (Found live: glassbox.state's 28-byte
    # retention.cursor is a moving pointer, and the same registry row calls the store "rebuilt by
    # the runtime on next use" — hashing it contradicted its own declared contract.)
    stable_cadence = store.get('cadence') != 'continuous'
    # D-015 (2026-08-27): inside a git directory the sentinels are content-addressed objects
    # (packs until 2026-10-11, now excluded as repack-volatile), often far above the cap, so the cap left repo.git with NO byte evidence. Paul
    # accepted the cost of hashing them in full; every other store keeps the cap. Each over-cap
    # hash records its own seconds (sha256_seconds) because D-015 requires the cost be MEASURED.
    hash_cap = float('inf') if git_dir else SENTINEL_HASH_MAX_BYTES
    chosen, seen = [], set()
    for rel, nbytes, mtime in (list(by_size) + list(by_time)
                               + _by_hashable(by_size, by_time, candidates, hash_cap)):
        if rel in seen:
            continue
        seen.add(rel)
        # content_stable is DERIVED, never authored: cadence AND the volatility predicate that
        # picked this row. The second conjunct cannot be true here (a volatile path was filtered
        # out above) and is kept deliberately as a structural guarantee — the 2026-08-27 defect was
        # precisely a volatile file reaching the manifest with content_stable:true, so the flag and
        # the filter are wired to the same predicate and cannot drift apart again.
        volatile = _volatile(rel, git_dir)
        content_stable = stable_cadence and not volatile
        row = {'rel': rel, 'bytes': nbytes, 'mtime_epoch': mtime, 'sha256': None,
               'content_stable': content_stable, 'sha256_skipped_reason': None}
        full = store['path'] if store['measured']['kind'] == 'file' else os.path.join(store['path'], rel)
        if volatile:
            row['sha256_skipped_reason'] = (
                'PRODUCER BUG GUARD: this path is volatile (git bookkeeping or a build/lock/temp '
                'artifact) and should never have been selected. Recorded for existence only.')
        elif not content_stable:
            row['sha256_skipped_reason'] = (
                'store cadence is "continuous": existing files are rewritten by ordinary '
                'operation, so byte identity is not a property this store has. Recorded for '
                'existence only; loss detection for it rides A2 file_count instead.')
        elif nbytes is not None and nbytes <= hash_cap:
            try:
                t0 = time.time()
                row['sha256'] = sha256_file(full, store)
                if nbytes > SENTINEL_HASH_MAX_BYTES:
                    row['sha256_seconds'] = round(time.time() - t0, 2)
            except OSError as exc:
                row['sha256_skipped_reason'] = 'unreadable: %s' % type(exc).__name__
        else:
            row['sha256_skipped_reason'] = 'exceeds sentinel hash cap %d bytes' % SENTINEL_HASH_MAX_BYTES
        chosen.append(row)
    return chosen


# =============================================================================================
# GIT SCOPE PROBE — measured tracked-ness, one `git ls-files` pass per root, cached
#
# This answers exactly one question, per store: does git already protect this material?  It is a
# SCOPE question, not a capacity question, so it lives beside the envelope rather than inside it.
# Everything here is read-only: `git ls-files` reads the index and prints; it does not touch the
# work tree, does not lock, and does not care that another lane may be mid-commit.
# =============================================================================================
class GitIndex(object):
    """Tracked-ness for ONE root, resolved from ONE `git ls-files` pass.

    Three answer shapes, and the difference between the last two is the whole point:
      * TRACKED / NOT TRACKED — git answered; the set is authoritative for this root.
      * NOT PROTECTED         — the root is absent, or is not a readable git work tree (a bare
                                mirror included). Authoritative NEGATIVE: git protects nothing here.
      * UNKNOWN               — git could not be asked (binary missing, timeout, probe disabled).
                                Not the same as "untracked", and never reported as if it were.
    """
    __slots__ = ('root_id', 'root_path', 'status', 'detail', 'tracked_count', '_files', '_dirs')

    #: statuses where the tracked set is a real answer rather than an absence of one
    AUTHORITATIVE = ('ok', 'not_a_work_tree', 'root_absent')

    def __init__(self, root_id, root_path, status, detail=None, rel_paths=()):
        self.root_id = root_id
        self.root_path = root_path
        self.status = status
        self.detail = detail
        self._files = set()
        self._dirs = set()
        for rel in rel_paths:
            rel = rel.replace('\\', '/').strip('/')
            if not rel:
                continue
            self._files.add(rel.casefold())
            parts = rel.split('/')
            for i in range(1, len(parts)):
                self._dirs.add('/'.join(parts[:i]).casefold())
        self.tracked_count = len(self._files)

    @property
    def answers(self):
        return self.status in self.AUTHORITATIVE

    def tracked(self, rel):
        """True / False / None(unknown) for a store's rel path under this root.

        A rel naming a DIRECTORY counts as tracked when git tracks anything at or under it: the
        question is whether git protects this store's material, not whether it protects every last
        byte of it.  (`.claude/skills` carries a couple of untracked .bak files and 13 __pycache__
        entries; git is still the integrity authority for that store, and hashing it would still go
        stale on every skill edit.)"""
        if not self.answers:
            return None
        rel = (rel or '.').replace('\\', '/').strip('/')
        if rel in ('', '.'):
            return self.tracked_count > 0
        key = rel.casefold()
        return key in self._files or key in self._dirs

    def row(self):
        return {'root_id': self.root_id, 'status': self.status, 'detail': self.detail,
                'authoritative': self.answers, 'tracked_files': self.tracked_count}


def _probe_git_index(root_id, root_path, present):
    if not GIT_PROBE:
        return GitIndex(root_id, root_path, 'probe_disabled', 'SYSMAN_GIT_PROBE=0')
    if not present:
        return GitIndex(root_id, root_path, 'root_absent', 'root is not on this host')
    try:
        r = subprocess.run(['git', '-C', root_path, 'ls-files', '-z'],
                           capture_output=True, timeout=GIT_LS_FILES_TIMEOUT_S)
    except FileNotFoundError:
        return GitIndex(root_id, root_path, 'git_unavailable', 'no git executable on PATH')
    except subprocess.TimeoutExpired:
        return GitIndex(root_id, root_path, 'timeout',
                        'git ls-files exceeded %ds' % GIT_LS_FILES_TIMEOUT_S)
    except Exception as exc:                                  # pragma: no cover - defensive
        return GitIndex(root_id, root_path, 'error', '%s: %s' % (type(exc).__name__, exc))
    if r.returncode != 0:
        err = (r.stderr or b'').decode('utf-8', 'replace').strip().splitlines()
        err = err[0] if err else 'git ls-files exit %d' % r.returncode
        low = err.lower()
        # "not a git repository" and "must be run in a work tree" are ANSWERS: git protects nothing
        # under this root.  Anything else is a failure to ask, and stays unknown.
        if 'not a git repository' in low or 'work tree' in low:
            return GitIndex(root_id, root_path, 'not_a_work_tree', err)
        return GitIndex(root_id, root_path, 'error', err)
    out = r.stdout.decode('utf-8', 'replace')
    return GitIndex(root_id, root_path, 'ok', None,
                    [p for p in out.split('\0') if p])


def build_git_indexes(root_rows):
    """One GitIndex per root, keyed by root_id.  Called once; every store reads from the cache."""
    return dict((r['id'], _probe_git_index(r['id'], r['path'], r['present'])) for r in root_rows)


def git_scope_refusal(store):
    """The reason hash_always is out of scope for this store, or None if it is in scope.

    Separate from envelope_violations on purpose: the envelope is about what we can AFFORD to hash,
    this is about what is OURS to hash.  Kept apart so a store refused on scale keeps reporting the
    scale gap it was declared to expose."""
    tracked = store.get('git_tracked')
    if tracked is True:
        return ('git tracks this store (%s) — git is the integrity authority for it; a hash set '
                'here would add no evidence and would go stale on every commit'
                % (store.get('git_tracking_basis') or 'measured'))
    if tracked is None and not GIT_UNKNOWN_HONOURS_HASH_ALWAYS:
        return ('git tracked-ness is UNKNOWN (%s) and this producer is configured to refuse '
                'hash_always it cannot scope' % (store.get('git_tracking_basis') or 'no probe'))
    return None


# =============================================================================================
# INTEGRITY POLICY — enforced here, echoed into the manifest so the audit can re-assert it
# =============================================================================================
def envelope_violations(store):
    """Return the list of predicates a hash_always request FAILS.  Empty list == inside the envelope."""
    m = store['measured']
    bad = []
    if store['class'] == 'SECRET':
        bad.append('store is SECRET (secrets are presence_only, always)')
    if not m['present']:
        bad.append('store is absent')
    if m['bytes'] > HASH_ALWAYS_MAX_BYTES:
        bad.append('bytes %d > cap %d' % (m['bytes'], HASH_ALWAYS_MAX_BYTES))
    if m['file_count'] > HASH_ALWAYS_MAX_FILES:
        bad.append('file_count %d > cap %d' % (m['file_count'], HASH_ALWAYS_MAX_FILES))
    if store['irreplaceability'] < HASH_ALWAYS_MIN_IRREPLACEABILITY:
        bad.append('irreplaceability %d < floor %d'
                   % (store['irreplaceability'], HASH_ALWAYS_MIN_IRREPLACEABILITY))
    if not m['walk_complete']:
        bad.append('walk incomplete (budget exceeded) — a partial file list cannot anchor a hash set')
    return bad


_SCALE_ONLY = ('bytes ', 'file_count ')


def resolve_policy(store, overrides):
    """SECRET is a hard error if the registry asked for anything but presence_only — that is a
    confidentiality bug, not a tuning question.  Every other out-of-envelope hash_always request is
    REFUSED and downgraded, with its failing predicates recorded.

    Downgrade target depends on WHY the request failed.  Refused purely on SCALE (too many bytes or
    too many files) the store still deserves the strongest affordable evidence, so it lands on
    sentinel_files.  Refused because it is absent, or because the walk was truncated (so no file
    list can anchor anything), it lands on hash_never.

    TWO refusals, checked in this order, and the order is load-bearing:

      1. THE ENVELOPE (can we afford it?).  Unchanged.  A store refused here keeps reporting the
         capacity gap it was declared to expose — repo.audits declares hash_always ON MERIT and is
         refused on scale, and that refusal must keep saying 'scale' and keep landing on
         sentinel_files.  Folding the scope rule in ahead of it would relabel that finding and
         strip the only evidence a 23 GiB store can afford.

      2. THE GIT SCOPE (is it ours to do?).  Only reached by a store the envelope would have
         ADMITTED.  git tracks it -> refused, hash_never, refused_on='git-tracked'.  Never
         sentinel_files: sentinels are the largest files plus the NEWEST file, and on a tracked
         store the newest file changes with every commit, so sentinels reproduce the exact
         staleness this ruling exists to remove."""
    declared = store['integrity_policy_declared']
    if declared not in POLICIES:
        raise RuntimeError('store %s declares unknown integrity policy %r' % (store['id'], declared))
    if store['class'] == 'SECRET':
        if declared != 'presence_only':
            raise RuntimeError(
                'REGISTRY BUG: SECRET store %s declares integrity_policy %r; secrets are '
                'presence_only, always.' % (store['id'], declared))
        return 'presence_only'
    if declared == 'presence_only':
        raise RuntimeError(
            'REGISTRY BUG: non-SECRET store %s declares presence_only; that policy exists for '
            'secrets and would silently drop all integrity evidence here.' % store['id'])
    if declared == 'hash_always':
        bad = envelope_violations(store)
        if bad:
            scale_only = all(r.startswith(_SCALE_ONLY) for r in bad)
            to = 'sentinel_files' if scale_only else 'hash_never'
            overrides.append({'store': store['id'], 'from': 'hash_always', 'to': to,
                              'refused_on': 'scale' if scale_only else 'availability',
                              'reasons': bad})
            return to
        scope = git_scope_refusal(store)
        if scope:
            overrides.append({'store': store['id'], 'from': 'hash_always', 'to': 'hash_never',
                              'refused_on': 'git-tracked' if store.get('git_tracked') is True
                                            else 'git-unknown',
                              'reasons': [scope]})
            return 'hash_never'
    return declared


def apply_policy(store, listing, candidates):
    policy = store['integrity_policy']
    store['hashes'] = None
    store['content_sha256'] = None
    store['sentinels'] = None
    if policy == 'presence_only':
        return                                          # SECRET: path/size/mtime already recorded
    if not store['measured']['present']:
        return
    if policy == 'hash_always':
        _forbid_secret(store, 'hash every member file')
        hashes = {}
        base = store['path']
        for rel, _nbytes, _mtime in sorted(listing):
            full = base if store['measured']['kind'] == 'file' else os.path.join(base, rel)
            try:
                hashes[rel] = sha256_file(full, store)
            except OSError as exc:
                hashes[rel] = 'UNREADABLE:%s' % type(exc).__name__
        store['hashes'] = hashes
        roll = hashlib.sha256()
        for rel in sorted(hashes):
            roll.update(rel.encode('utf-8')); roll.update(b'\0')
            roll.update(hashes[rel].encode('ascii')); roll.update(b'\n')
        store['content_sha256'] = roll.hexdigest()
    elif policy == 'sentinel_files':
        store['sentinels'] = pick_sentinels(store, candidates)


# =============================================================================================
# HOST TOPOLOGY — probed, never assumed
# =============================================================================================
_HOST_PS = r"""
$ErrorActionPreference='SilentlyContinue'
$disks=@(Get-PhysicalDisk | Select-Object DeviceId,FriendlyName,MediaType,Size)
$vols=@(Get-Volume | Where-Object {$_.DriveLetter} | Select-Object DriveLetter,FileSystemLabel,SizeRemaining,Size)
$parts=@(Get-Partition | Where-Object {$_.DriveLetter} | Select-Object DriveLetter,DiskNumber)
$fh=$null; $fhErr=$null
try { $fh=[string](Get-Service fhsvc -ErrorAction Stop).Status } catch { $fhErr=$_.Exception.Message }
$rpn=$null; $rpOk=$false; $rpErr=$null
try { $rp=@(Get-ComputerRestorePoint -ErrorAction Stop); $rpn=$rp.Count; $rpOk=$true } catch { $rpErr=$_.Exception.Message }
[pscustomobject]@{
  disks=$disks; volumes=$vols; partitions=$parts;
  file_history_status=$fh; file_history_error=$fhErr;
  restore_point_count=$rpn; restore_point_probe_ok=$rpOk; restore_point_error=$rpErr
} | ConvertTo-Json -Depth 5 -Compress
"""


def probe_host():
    onedrive = os.environ.get('OneDrive') or os.environ.get('OneDriveConsumer')
    src = 'env:OneDrive' if os.environ.get('OneDrive') else (
          'env:OneDriveConsumer' if os.environ.get('OneDriveConsumer') else None)
    if not onedrive:
        guess = os.path.join(_HOME, 'OneDrive')
        if os.path.isdir(guess):
            onedrive, src = guess, 'probe:~\\OneDrive exists'
    host = {'onedrive_root': os.path.abspath(onedrive) if onedrive else None,
            'onedrive_root_source': src,
            'physical_disk_count': None, 'physical_disks': None, 'volumes': None,
            'volume_disk_map': None, 'file_history_status': None, 'file_history_enabled': None,
            'restore_point_count': None,
            'probe': {'status': 'not_attempted', 'detail': None}}
    if os.environ.get('SYSMAN_HOST_PROBE', '1') == '0' or os.name != 'nt':
        host['probe'] = {'status': 'skipped',
                         'detail': 'SYSMAN_HOST_PROBE=0' if os.name == 'nt' else 'non-Windows host'}
        return host
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', _HOST_PS],
                           capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=120)
        raw = (r.stdout or '').strip()
        if not raw:
            host['probe'] = {'status': 'failed', 'detail': 'empty output; stderr=%s'
                             % ((r.stderr or '').strip()[:200])}
            return host
        d = json.loads(raw)
    except Exception as exc:
        host['probe'] = {'status': 'failed', 'detail': '%s: %s' % (type(exc).__name__, str(exc)[:200])}
        return host

    def _lst(v):
        if v is None: return []
        return v if isinstance(v, list) else [v]

    disks = [{'device_id': str(x.get('DeviceId')), 'friendly_name': x.get('FriendlyName'),
              'media_type': x.get('MediaType'), 'size_bytes': x.get('Size')} for x in _lst(d.get('disks'))]
    vols = [{'drive': (str(x.get('DriveLetter')) + ':') if x.get('DriveLetter') else None,
             'label': x.get('FileSystemLabel'), 'free_bytes': x.get('SizeRemaining'),
             'total_bytes': x.get('Size')} for x in _lst(d.get('volumes'))]
    vmap = {}
    for x in _lst(d.get('partitions')):
        if x.get('DriveLetter'):
            vmap[str(x['DriveLetter']) + ':'] = str(x.get('DiskNumber'))
    fh = d.get('file_history_status')
    host.update({'physical_disk_count': len(disks), 'physical_disks': disks, 'volumes': vols,
                 'volume_disk_map': vmap, 'file_history_status': fh,
                 'file_history_enabled': (fh == 'Running') if fh else None,
                 'restore_point_count': (d.get('restore_point_count')
                                         if d.get('restore_point_probe_ok') else None),
                 'probe': {'status': 'ok',
                           'detail': None if d.get('restore_point_probe_ok')
                           else 'restore-point probe failed: %s'
                                % str(d.get('restore_point_error'))[:160]}})
    return host


def derive_topology(path, host):
    """DERIVED, never authored.  offbox_replica is decided by OneDrive-root containment; single_disk
    follows from it; durable_backup is false system-wide because OneDrive is SYNC, not backup — a
    deletion or corruption propagates to the cloud copy."""
    od = host.get('onedrive_root')
    drive = (os.path.splitdrive(os.path.abspath(path))[0] or '').upper()
    disk_id = (host.get('volume_disk_map') or {}).get(drive)
    if od:
        try:
            under = os.path.commonpath([os.path.abspath(path), od]) == os.path.abspath(od)
        except ValueError:
            under = False
        replica = 'onedrive_sync' if under else None
        basis = ('path resolves under the OneDrive root %s' % od) if under else \
                ('path does NOT resolve under the OneDrive root %s' % od)
    else:
        replica = None
        basis = 'no OneDrive root could be established on this host; treated as no replica'
    return {'volume': drive or None, 'disk_id': disk_id, 'offbox_replica': replica,
            'single_disk': replica is None, 'durable_backup': False,
            'topology_basis': basis,
            'durable_backup_basis': _durable_backup_basis(host)}


def _durable_backup_basis(host):
    """Built from what the probe ACTUALLY established, so the prose can never assert a fact the
    host block records as unknown.  (First draft of this string said "zero restore points" while
    the probe had come back Access-denied — the exact defect this manifest exists to prevent.)"""
    disks = host.get('physical_disk_count')
    fh = host.get('file_history_status')
    rp = host.get('restore_point_count')
    parts = []
    parts.append('physical disks: %s' % (disks if disks is not None else 'NOT ESTABLISHED'))
    parts.append('File History service: %s' % (fh or 'NOT ESTABLISHED'))
    if rp is None:
        detail = (host.get('probe') or {}).get('detail')
        parts.append('restore points: NOT ESTABLISHED (%s)'
                     % (str(detail)[:80] if detail else 'probe returned nothing'))
    else:
        parts.append('restore points: %d' % rp)
    return ('durable_backup is false system-wide. OneDrive is a SYNC replica, not a backup — a '
            'delete or a corruption propagates to it — and no other backup target is configured '
            'on this host. Probe basis: ' + '; '.join(parts) + '.')


# =============================================================================================
# RESCUE INDEX — read out of the 2026-08-26 rescue manifests, never typed in
#
# A store outside OneDrive is single_disk, but that is not the same as EXTINCT-on-disk-loss: three
# manifests in backups/offbox_rescue_20260826 record what was actually copied into the
# OneDrive-replicated tree that day, with source-and-destination sha256 verification.  Reading them
# turns "single disk" into the sharper question the containerization plan needs: single disk AND no
# rescue copy.  Read-only; a manifest that will not parse yields no claim, never a guess.
# =============================================================================================
RESCUE_DIR_REL = 'backups/offbox_rescue_20260826'


def _norm(pth):
    try:
        return os.path.normcase(os.path.abspath(str(pth).replace('/', os.sep)))
    except Exception:
        return None


def load_rescue_index(root_path):
    """Return {'roots': [ {source_root, manifest, destination, label} ], 'files': {normpath: mf},
    'errors': [...]}.  Every entry is quoted from a manifest on disk."""
    base = os.path.join(root_path, RESCUE_DIR_REL.replace('/', os.sep))
    idx = {'source': RESCUE_DIR_REL, 'roots': [], 'files': {}, 'manifests': [], 'errors': []}
    specs = (('RESCUE_MANIFEST.json', 'A'), ('RESCUE_MANIFEST_PHASE_B.json', 'B'),
             ('RESCUE_MANIFEST_PHASE_C.json', 'C'))
    for name, phase in specs:
        fp = os.path.join(base, name)
        if not os.path.isfile(fp):
            idx['errors'].append('%s: absent' % name)
            continue
        try:
            with open(fp, encoding='utf-8') as fh:
                d = json.load(fh)
        except Exception as exc:
            idx['errors'].append('%s: unreadable (%s)' % (name, type(exc).__name__))
            continue
        idx['manifests'].append(name)
        if phase == 'A':
            for row in d.get('files') or []:
                k = _norm(row.get('source'))
                if k:
                    idx['files'][k] = {'manifest': name, 'destination': row.get('destination'),
                                       'sha256': row.get('sha256'),
                                       'verified': bool(row.get('verified_at_destination'))}
            # Generated artifacts name no single source file; their note carries the source dirs.
            for row in d.get('generated_artifacts') or []:
                note = str(row.get('note') or '')
                for frag in note.replace('\\\\', '\\').split():
                    frag = frag.strip('.,()')
                    if len(frag) > 3 and frag[1:3] == ':\\' and os.path.isdir(frag):
                        idx['roots'].append({'source_root': _norm(frag), 'manifest': name,
                                             'destination': row.get('destination'),
                                             'label': 'generated archive',
                                             'basis': 'path parsed out of the artifact note'})
        elif phase == 'B':
            for a in d.get('archives') or []:
                if str(a.get('status')) != 'VERIFIED_OK':
                    idx['errors'].append('%s: archive %s status=%s (not counted as rescued)'
                                         % (name, a.get('label'), a.get('status')))
                    continue
                idx['roots'].append({'source_root': _norm(a.get('source_path')), 'manifest': name,
                                     'destination': a.get('archive_path'), 'label': a.get('label'),
                                     'basis': 'archives[].source_path with status VERIFIED_OK'})
        else:
            for label, s in (d.get('sets') or {}).items():
                idx['roots'].append({'source_root': _norm(s.get('source_root')), 'manifest': name,
                                     'destination': s.get('destination_dir'), 'label': label,
                                     'basis': 'sets[].source_root'})
    idx['roots'] = [r for r in idx['roots'] if r['source_root']]
    return idx


def rescue_status(store_path, idx):
    """FULL when the store sits at or under a rescued source root; PARTIAL when some individually
    rescued file sits under it; None otherwise.  Nothing here is inferred from a store's name."""
    sp = _norm(store_path)
    if not sp:
        return {'coverage': None, 'via': None, 'basis': 'store path could not be normalised'}
    best = None
    for r in idx['roots']:
        sr = r['source_root']
        if sp == sr or sp.startswith(sr + os.sep):
            if best is None or len(sr) > len(best['source_root']):
                best = r
    if best:
        return {'coverage': 'full', 'via': {k: best[k] for k in ('manifest', 'destination', 'label')},
                'basis': 'store path is at or under %s (%s)' % (best['source_root'], best['basis'])}
    hits = [k for k in idx['files'] if k == sp or k.startswith(sp + os.sep)]
    if hits:
        return {'coverage': 'partial', 'via': {'manifest': idx['files'][hits[0]]['manifest'],
                                               'destination': None, 'label': 'per-file copy'},
                'basis': '%d individually rescued file(s) sit under this store' % len(hits)}
    return {'coverage': None, 'via': None,
            'basis': 'no rescue manifest names this path or any file under it'}


# =============================================================================================
# ABSENT-EXPECTED
# =============================================================================================
def scan_absent_expected(entry, root_path):
    base = os.path.join(root_path, entry.get('base', '.').replace('/', os.sep))
    base = os.path.abspath(base)
    out = {'id': entry['id'], 'root_id': entry['root_id'], 'base': base,
           'patterns': list(entry['patterns']), 'severity': entry.get('severity', 'notice'),
           'depth': entry.get('depth'), 'why': entry['why'],
           'base_present': os.path.isdir(base), 'matches': [], 'scan_complete': True}
    if not out['base_present']:
        return out
    depth = entry.get('depth')
    for dirpath, dirnames, filenames in os.walk(base):
        rel = os.path.relpath(dirpath, base)
        level = 0 if rel == '.' else rel.count(os.sep) + 1
        if depth is not None and level >= depth:
            dirnames[:] = []
        for name in filenames:
            for pat in entry['patterns']:
                if fnmatch.fnmatch(name.lower(), pat.lower()):
                    out['matches'].append(os.path.join(dirpath, name).replace('\\', '/'))
                    break
        if len(out['matches']) > 200:
            out['scan_complete'] = False
            break
    out['matches'] = sorted(out['matches'])
    return out


# =============================================================================================
# MAIN
# =============================================================================================
def git_head(cwd):
    try:
        r = subprocess.run(['git', '-C', cwd, 'rev-parse', 'HEAD'], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=60)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip(), 'git rev-parse HEAD in %s' % cwd
    except Exception:
        pass
    return None, 'git rev-parse HEAD failed in %s' % cwd


def gib(n):
    return round(n / float(1 << 30), 3)


def main(argv=None):
    t0 = time.time()
    ap = argparse.ArgumentParser(description='Build the Melange system manifest.')
    ap.add_argument('--out', default=None, help='output path (default: <ROOT>/data/system/system_manifest.json)')
    ap.add_argument('--dry-run', action='store_true', help='measure and print; write nothing')
    args = ap.parse_args(argv)

    out_path = os.path.abspath(args.out) if args.out else os.path.join(ROOT, OUT_REL)
    here = os.path.dirname(os.path.abspath(__file__))
    print('SYSTEM MANIFEST PRODUCER — schema %s' % SCHEMA)
    print('  root            : %s' % ROOT)
    print('  output          : %s%s' % (out_path, '  (DRY RUN — not written)' if args.dry_run else ''))

    host = probe_host()
    print('  host probe      : %s%s' % (host['probe']['status'],
                                        ('  :: ' + str(host['probe']['detail'])) if host['probe']['detail'] else ''))
    print('  physical disks  : %s   onedrive root: %s   file history: %s   restore points: %s'
          % (host['physical_disk_count'], host['onedrive_root'], host['file_history_status'],
             host['restore_point_count']))

    root_rows = []
    root_paths = {}
    for r in ROOTS:
        rp = os.path.abspath(r['path'])
        root_paths[r['id']] = rp
        present = os.path.isdir(rp)
        row = dict(r); row['path'] = rp; row['present'] = present
        row.update(derive_topology(rp, host))
        root_rows.append(row)
        print('  root %-14s %s  %s' % (r['id'], 'PRESENT' if present else 'ABSENT ', rp))
    if not any(r['present'] for r in root_rows):
        print('SYSTEM MANIFEST — ABORTED: every declared root is absent; this manifest would describe no machine.')
        return 2

    git_idx = build_git_indexes(root_rows)
    print('  git scope probe : %s'
          % ('  '.join('%s=%s(%d)' % (i.root_id, i.status, i.tracked_count)
                       for i in git_idx.values())))
    for i in git_idx.values():
        if not i.answers:
            print('    ! %s: git could not answer (%s :: %s) — tracked-ness is UNKNOWN for its '
                  'stores; the hash_always scope guard will not fire there'
                  % (i.root_id, i.status, i.detail))

    rescue = load_rescue_index(root_paths['live_tree'])
    print('  rescue index    : %d manifests, %d rescued source roots, %d individually rescued files%s'
          % (len(rescue['manifests']), len(rescue['roots']), len(rescue['files']),
             ('  errors: ' + '; '.join(rescue['errors'])) if rescue['errors'] else ''))

    budget = {'used': 0, 'total': TOTAL_FILE_CAP}
    overrides = []
    stores = []
    seen_ids, seen_paths = set(), {}
    print('\n[stores]')
    for spec in REGISTRY:
        store = dict(spec)
        rp = root_paths[store['root_id']]
        rel = store['rel']
        store['path'] = os.path.abspath(rp if rel == '.' else os.path.join(rp, rel.replace('/', os.sep)))
        store['root_present'] = os.path.isdir(rp)
        # MEASURED from the filesystem shape, never declared in the registry. Two consumers:
        # sentinel selection (git bookkeeping is not sentinel material) and the audit's A2 scale
        # rule (a git store's file_count is supposed to move — see system_manifest_check.py).
        store['git_dir_store'] = is_git_dir(store['path'])
        if store['class'] not in CLASSES:
            raise RuntimeError('store %s declares unknown class %r' % (store['id'], store['class']))
        if store['id'] in seen_ids:
            raise RuntimeError('duplicate store id %s' % store['id'])
        seen_ids.add(store['id'])
        key = os.path.normcase(store['path'])
        if key in seen_paths:
            raise RuntimeError('stores %s and %s resolve to the same path %s'
                               % (seen_paths[key], store['id'], store['path']))
        seen_paths[key] = store['id']
        m, listing, cands = measure(store, budget)
        store['measured'] = m
        store['sqlite'] = sqlite_sidecars(store)
        store.update(derive_topology(store['path'], host))
        store['rescue'] = rescue_status(store['path'], rescue)
        gidx = git_idx[store['root_id']]
        store['git_tracked'] = gidx.tracked(rel)
        store['git_tracking_basis'] = ('root %s: %s%s'
                                       % (gidx.root_id, gidx.status,
                                          ', %d tracked paths' % gidx.tracked_count
                                          if gidx.status == 'ok' else ''))
        store['integrity_policy'] = resolve_policy(store, overrides)
        apply_policy(store, listing, cands)
        store['measured']['mtime_utc'] = (
            datetime.datetime.fromtimestamp(m['newest_mtime_epoch'], datetime.timezone.utc).isoformat()
            if m['newest_mtime_epoch'] else None)
        store.pop('rel', None)
        stores.append(store)
        flag = '' if m['present'] else '  ABSENT'
        if m['budget_exceeded']:
            flag += '  BUDGET-EXCEEDED'
        print('  %-28s %-9s %-14s %8d files %10.3f GiB  %-14s%s'
              % (store['id'], store['class'], store['integrity_policy'], m['file_count'],
                 gib(m['bytes']), m['size_class'], flag))

    absent = [scan_absent_expected(e, root_paths[e['root_id']]) for e in ABSENT_EXPECTED]
    print('\n[absent-expected]')
    for a in absent:
        print('  %-38s %-6s base=%s matches=%d%s'
              % (a['id'], a['severity'], 'PRESENT' if a['base_present'] else 'ABSENT',
                 len(a['matches']), '' if a['scan_complete'] else '  SCAN-TRUNCATED'))

    counted = [s for s in stores if s['counts_toward_totals'] and s['measured']['present']]
    single = [s for s in counted if s['single_disk']]
    replicated = [s for s in counted if s['offbox_replica']]
    irrep_single = [s for s in single if s['irreplaceability'] >= 3]
    budget_hit = [s['id'] for s in stores if s['measured']['budget_exceeded']]
    secrets = [s for s in stores if s['class'] == 'SECRET']
    hashed = [s for s in stores if s['integrity_policy'] == 'hash_always']

    totals = {
        'stores_declared': len(stores),
        'stores_present': sum(1 for s in stores if s['measured']['present']),
        'stores_absent': sum(1 for s in stores if not s['measured']['present']),
        'stores_counted': len(counted),
        'files_counted': sum(s['measured']['file_count'] for s in counted),
        'bytes_counted': sum(s['measured']['bytes'] for s in counted),
        'walk_files_used': budget['used'],
        'walk_files_budget': TOTAL_FILE_CAP,
        'budget_exceeded_stores': budget_hit,
    }
    unrescued = [s for s in single if s['rescue']['coverage'] is None]
    part_rescued = [s for s in single if s['rescue']['coverage'] == 'partial']
    full_rescued = [s for s in single if s['rescue']['coverage'] == 'full']
    extinction = {
        'definition': 'What ceases to exist if Disk 0 fails, over the counted stores only. '
                      'single_disk means no off-box replica of the LIVE path; the rescue split '
                      'then separates what the 2026-08-26 off-box rescue already copied into the '
                      'OneDrive-replicated tree from what it did not.',
        'single_disk_stores': len(single),
        'single_disk_files': sum(s['measured']['file_count'] for s in single),
        'single_disk_bytes': sum(s['measured']['bytes'] for s in single),
        'single_disk_roots': sorted(set(s['root_id'] for s in single)),
        'irreplaceable_single_disk_stores': len(irrep_single),
        'irreplaceable_single_disk_bytes': sum(s['measured']['bytes'] for s in irrep_single),
        'rescued_full_stores': len(full_rescued),
        'rescued_full_bytes': sum(s['measured']['bytes'] for s in full_rescued),
        'rescued_partial_stores': len(part_rescued),
        'unrescued_stores': len(unrescued),
        'unrescued_files': sum(s['measured']['file_count'] for s in unrescued),
        'unrescued_bytes': sum(s['measured']['bytes'] for s in unrescued),
        'unrescued_store_ids': [s['id'] for s in unrescued],
        'unrescued_irreplaceable_store_ids': [s['id'] for s in unrescued
                                              if s['irreplaceability'] >= 3],
        'onedrive_replicated_stores': len(replicated),
        'onedrive_replicated_bytes': sum(s['measured']['bytes'] for s in replicated),
        'durable_backup_stores': sum(1 for s in counted if s['durable_backup']),
    }

    commit, commit_src = git_head(here)
    gen_sha = hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest()
    manifest = {
        'schema': SCHEMA,
        'generated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'generated_epoch': time.time(),
        'generator': 'fec/audit/build_system_manifest.py',
        'generator_commit': commit,
        'generator_commit_source': commit_src,
        'generator_sha256': gen_sha,
        'root': ROOT,
        'host': host,
        'policy': {
            'hash_always_envelope': {
                'max_bytes': HASH_ALWAYS_MAX_BYTES, 'max_files': HASH_ALWAYS_MAX_FILES,
                'min_irreplaceability': HASH_ALWAYS_MIN_IRREPLACEABILITY,
                'secret_forbidden': True, 'requires_complete_walk': True,
                'git_tracked_forbidden': True},
            'hash_always_scope': {
                'rule': 'hash_always covers only what git does not protect',
                'ruling': "Paul, 2026-08-27: 'use the narrower scope'",
                'why': 'Where git tracks a store it is already the integrity authority — the blob '
                       'sha is the hash, git status is the detector, git checkout is the restore. '
                       'A second hash set here adds no evidence and goes stale on every commit, '
                       'turning the audit into a false alarm nobody reads.',
                'enforced_on': 'measured tracked-ness (one `git ls-files` pass per root, cached), '
                               'never a declared field',
                'refusal_target': 'hash_never — never sentinel_files, because a sentinel set '
                                  'includes the newest file and that changes with every commit',
                'checked_after': 'the envelope, so a store refused on scale keeps reporting scale',
                'unknown_tracking_honours_hash_always': GIT_UNKNOWN_HONOURS_HASH_ALWAYS,
                'unknown_rule': 'If git cannot be asked, git_tracked is null and the guard does '
                                'NOT fire: refusing on an unproven suspicion would delete the only '
                                'integrity evidence the irreplaceable stores have, on exactly the '
                                'hosts where git is not there to replace it. Every store honoured '
                                'on an unverified probe is named in git_tracking.',
                'probe_enabled': GIT_PROBE},
            'sentinel': {'count': SENTINEL_COUNT, 'hash_max_bytes': SENTINEL_HASH_MAX_BYTES,
                         'hash_max_bytes_git_dir': None,
                         'git_dir_hash_rule': 'D-015: inside a git directory sentinels are hashed '
                                              'whatever their size; over-cap rows record '
                                              'sha256_seconds. Pack files (objects/pack/) are '
                                              'never sentinels: repack renames them. Instead the '
                                              'commits of HEAD, the trunk and every tag are pinned '
                                              'and must stay reachable from current refs.',
                         'selection': 'the %d largest files plus the newest file, by relpath — '
                                      'except inside a git directory, where the newest file is by '
                                      'construction whatever the last commit or push wrote, so only '
                                      'the largest are taken; and except where the registry '
                                      'declares sentinel_newest false, which extends that same rule '
                                      'to stores the pipeline chain rewrites. When no selected row '
                                      'is under the hash cap, the largest candidate that IS under '
                                      'it is added, so no store is left with size-only evidence.'
                                      % SENTINEL_COUNT,
                         'excluded_from_sentinel_duty': list(SENTINEL_EXCLUDE),
                         'chain_output_exclusion_rule':
                             'Per-store sentinel_excludes name paths the CHAIN '
                             '(fec/run_pipeline.py) rewrites every run. Such a file has no byte '
                             'stability to assert, so pinning it detects the chain rather than '
                             'corruption. The files are still walked and counted; loss detection '
                             'for them rides A2 file_count. Declared per store rather than per '
                             'store-class so a stale declaration surfaces as a red A2b row, never '
                             'as silent coverage loss.',
                         'chain_output_excluded_stores': sorted(
                             s['id'] for s in stores
                             if s.get('sentinel_excludes') or not s.get('sentinel_newest', True)),
                         'exclusion_rule': 'A sentinel is an expectation re-checked days later, so '
                                           'it must be a stable artifact. Build caches, temp files, '
                                           'locks and logs are rewritten by ordinary operation and '
                                           'would guarantee a false disagreement. They still count '
                                           'toward the store\'s bytes and file_count.',
                         'git_bookkeeping_excluded': True,
                         'git_bookkeeping_files': sorted(GIT_BOOKKEEPING_FILES),
                         'git_bookkeeping_dirs': list(GIT_BOOKKEEPING_DIRS),
                         'git_derived_index_paths': (list(GIT_DERIVED_INDEX_PREFIXES)
                                                     + list(GIT_DERIVED_INDEX_PATHS)),
                         'git_bookkeeping_rule':
                             'Inside a git directory (the store IS one, or the path passes through '
                             'a .git component) the index, refs, reflogs, packed-refs, the '
                             'transient *_HEAD pointers, in-flight rebase/bisect state and the '
                             'caches git regenerates are REWRITTEN by ordinary commits, pushes and '
                             'fetches. Byte identity is not a property they have. Recorded here '
                             'because the 2026-08-27 landing commit proved it: the picker chose '
                             '.git/index and the mirrors\' refs/heads/main, marked them '
                             'content_stable, and the gate went red on its own commit and push.',
                         'git_objects_still_eligible':
                             'Loose objects and packs ARE eligible: they are named by the hash of '
                             'their own contents, so they cannot change without changing name — the '
                             'immutability a sentinel wants. Known residual: `git gc` can retire '
                             'one, which A2b reports; that is infrequent and is a real change to '
                             'the store, unlike a commit.',
                         'git_dir_detection': 'measured — HEAD file + objects/ dir + refs/ dir; '
                                              'never declared in the registry'},
            'mtime_tolerance_s': MTIME_TOLERANCE_S,
            'size_classes': ['empty <1B', 'tiny <1MiB', 'small <10MiB', 'medium <100MiB',
                             'large <1GiB', 'huge <10GiB', 'massive >=10GiB'],
            'secret_rule': 'SECRET stores yield path, size and mtime only. Contents are never read, '
                           'hashed, or archived; the guard raises SecretAccessError.',
            'durable_backup_rule': 'durable_backup is false system-wide: OneDrive is a sync replica, '
                                   'not a backup, and no other target exists on this host.',
        },
        'totals': totals,
        'extinction': extinction,
        'git_tracking': {
            'probe_enabled': GIT_PROBE,
            'roots': [git_idx[r['id']].row() for r in root_rows],
            'roots_unanswered': sorted(i.root_id for i in git_idx.values() if not i.answers),
            'hash_always_stores': sorted(s['id'] for s in stores
                                         if s['integrity_policy'] == 'hash_always'),
            'unverified_hash_always_stores': sorted(
                s['id'] for s in stores
                if s['integrity_policy'] == 'hash_always' and s.get('git_tracked') is None),
            'refused_hash_always_stores': sorted(o['store'] for o in overrides
                                                 if o['refused_on'].startswith('git-')),
        },
        'policy_overrides': overrides,
        'roots': root_rows,
        'stores': stores,
        'absent_expected': absent,
    }

    if overrides:
        print('\n[policy refusals] hash_always requested but refused — downgraded, not waived')
        for o in overrides:
            print('  %-28s -> %-14s [%s] :: %s'
                  % (o['store'], o['to'], o['refused_on'], '; '.join(o['reasons'])))
    unverified = manifest['git_tracking']['unverified_hash_always_stores']
    if unverified:
        print('\n[git scope UNVERIFIED] git could not be asked about %d hash_always store(s); they '
              'are honoured on their declaration, not on measurement :: %s'
              % (len(unverified), ', '.join(unverified)))

    print('\n[totals] %d stores declared, %d present, %d absent; counted %d stores / %d files / %.3f GiB'
          % (totals['stores_declared'], totals['stores_present'], totals['stores_absent'],
             totals['stores_counted'], totals['files_counted'], gib(totals['bytes_counted'])))
    print('EXTINCTION EXPOSURE — %d of %d counted stores have NO off-box replica (%.3f GiB / %d '
          'files, roots %s); the 2026-08-26 rescue covers %d of them (%.3f GiB), leaving %d stores '
          '/ %.3f GiB / %d files that exist ONLY on Disk 0 — %d of those are irreplaceability>=3 '
          '[%s]; durable backups anywhere: %d'
          % (extinction['single_disk_stores'], totals['stores_counted'],
             gib(extinction['single_disk_bytes']), extinction['single_disk_files'],
             ','.join(extinction['single_disk_roots']),
             extinction['rescued_full_stores'], gib(extinction['rescued_full_bytes']),
             extinction['unrescued_stores'], gib(extinction['unrescued_bytes']),
             extinction['unrescued_files'],
             len(extinction['unrescued_irreplaceable_store_ids']),
             ','.join(extinction['unrescued_irreplaceable_store_ids']) or 'none',
             extinction['durable_backup_stores']))

    if not args.dry_run:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        MC.dump_atomic(manifest, out_path, indent=1, sort_keys=False, default=str)

    print('SYSTEM MANIFEST — %d stores (%d present), %.3f GiB counted, %d single-disk, %d secrets '
          'presence-only, %d hash_always, %d budget-exceeded, %d policy refusals -> %s in %.1fs'
          % (totals['stores_declared'], totals['stores_present'], gib(totals['bytes_counted']),
             extinction['single_disk_stores'], len(secrets), len(hashed), len(budget_hit),
             len(overrides), ('DRY RUN (nothing written)' if args.dry_run else out_path),
             time.time() - t0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
