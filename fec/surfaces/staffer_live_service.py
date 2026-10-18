"""Cached live graph API and independent advocacy-routing overlay."""
from __future__ import annotations

from collections import OrderedDict, Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
import gc
import gzip
import hashlib
import json
import os
import re
import threading
import time
from copy import copy, deepcopy
from dataclasses import dataclass

try:
    from .staffer_live_server import IncrementalProfiles, LIVE_LAYERS, POLL_SECONDS, digest, freshness_now, utcnow
    from .staffer_office_routes import office_index, search_offices
    from .staffer_search_index import ProfileSearchIndex, search_people
    from .staffer_connection_facts import PersonFacts, identity_profile_episodes, VERSION as OVERLAP_VERSION
    from .staffer_overlap_routes import OverlapRoutes, VERSION as AFFINITY_VERSION
    from .staffer_routing import AdvocacyRouter, VERSION as WORK_ROUTING_VERSION
    from .staffer_route_cancel import RouteCancelled, cancel_scope, check_cancelled
    from .staffer_routing_evidence import load_evidence
    from .staffer_person_inventory import load_inventory, apply_identity_overlay
    from .staffer_activity_index import ActivityIndex
    from .staffer_people_graph import NODE_LIMIT, project_people
    from .staffer_identity_resolution import load_identity_resolution, record_identity_decision
    from .staffer_identity_auto import resolve_identities, EVIDENCE_VERSION as IDENTITY_VERSION, NAME_STANDARD_VERSION
    from .staffer_payroll_identity import load_payroll_identity
    from .staffer_identity_records import consolidate_record_pairs
    from .staffer_identity_web import IdentityWebIndex, now as identity_now
    from .staffer_network_explorer import build_index, graph_context_record
    from .staffer_groups_index import build_groups_index
    from .staffer_contacts import ContactIndex
    from .staffer_source_catalog import SourceCatalog, CATALOG
    from .staffer_lda_catalog import LdaCatalog, LDA_CATALOG
    from ..staffer_network import lda_status_labels
except ImportError:  # direct script execution: no package context
    import os as _bootstrap_os, sys as _bootstrap_sys
    _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
    if _bootstrap_root not in _bootstrap_sys.path:
        _bootstrap_sys.path.insert(0, _bootstrap_root)
    from fec.surfaces.staffer_live_server import IncrementalProfiles, LIVE_LAYERS, POLL_SECONDS, digest, freshness_now, utcnow
    from fec.surfaces.staffer_office_routes import office_index, search_offices
    from fec.surfaces.staffer_search_index import ProfileSearchIndex, search_people
    from fec.surfaces.staffer_connection_facts import PersonFacts, identity_profile_episodes, VERSION as OVERLAP_VERSION
    from fec.surfaces.staffer_overlap_routes import OverlapRoutes, VERSION as AFFINITY_VERSION
    from fec.surfaces.staffer_routing import AdvocacyRouter, VERSION as WORK_ROUTING_VERSION
    from fec.surfaces.staffer_route_cancel import RouteCancelled, cancel_scope, check_cancelled
    from fec.surfaces.staffer_routing_evidence import load_evidence
    from fec.surfaces.staffer_person_inventory import load_inventory, apply_identity_overlay
    from fec.surfaces.staffer_activity_index import ActivityIndex
    from fec.surfaces.staffer_people_graph import NODE_LIMIT, project_people
    from fec.surfaces.staffer_identity_resolution import load_identity_resolution, record_identity_decision
    from fec.surfaces.staffer_identity_auto import resolve_identities, EVIDENCE_VERSION as IDENTITY_VERSION, NAME_STANDARD_VERSION
    from fec.surfaces.staffer_payroll_identity import load_payroll_identity
    from fec.surfaces.staffer_identity_records import consolidate_record_pairs
    from fec.surfaces.staffer_identity_web import IdentityWebIndex, now as identity_now
    from fec.surfaces.staffer_network_explorer import build_index, graph_context_record
    from fec.surfaces.staffer_groups_index import build_groups_index
    from fec.surfaces.staffer_contacts import ContactIndex
    from fec.surfaces.staffer_source_catalog import SourceCatalog, CATALOG
    from fec.surfaces.staffer_lda_catalog import LdaCatalog, LDA_CATALOG
    from fec.staffer_network import lda_status_labels


NODE_FIELDS = ('id', 'label', 'kind', 'member_ids', 'committee_ids', 'policy_ids', 'member_id',
               'name_status', 'review_status', 'identity_tier', 'canonical_eligible', 'is_search_match',
               'source_only_identity', 'source', 'verification', 'layout_only', 'routing_only')
NODE_FIELDS += ('source_summary', 'last_reported_at', 'identity_note')
NODE_FIELDS += ('source_as_of', 'lda_source_person_id', 'source_profile_uuid', 'profile_pending', 'profile_status')
NODE_FIELDS += ('identity_status', 'source_person_ids', 'identity_alias_ids')
NODE_FIELDS += ('identity_basis',)
NODE_FIELDS += ('identity_candidate_count',)
NODE_FIELDS += ('identity_references',)
NODE_FIELDS += ('aliases', 'contacts')
NODE_FIELDS += ('lda_status',)
EDGE_FIELDS = ('id', 'source', 'target', 'layer', 'weight', 'status', 'relationship', 'employer_label',
               'evidence_class', 'review_status', 'layout_only', 'operator_included', 'duration_weight',
               'canonical_eligible', 'provenance_available_on_selection')


def compact_node(node):
    return {key: node[key] for key in NODE_FIELDS if key in node}


def compact_edge(edge):
    return {key: edge[key] for key in EDGE_FIELDS if key in edge}


def bounded_put(cache, key, value, maximum=32):
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > maximum:
        cache.popitem(last=False)
    return value


def routing_signature(nodes):
    """Digest of every node field the work router reads: label, kind (or node_kind) and name_status.

    AdvocacyRouter._person_allowed decides from exactly these, so equal
    signatures mean the same people may be route nodes. The router's
    nodes_revision moves only when this changes, which lets the routing caches
    survive a refresh that rebuilds an equal mapping.
    """
    hasher = hashlib.blake2b(digest_size=16)
    for ident, node in nodes.items():
        hasher.update(repr((ident, node.get('label'), node.get('kind'), node.get('node_kind'), node.get('name_status'))).encode())
    return hasher.hexdigest()


def resolve_id(value, nodes, aliases, activity_provider):
    """The node id a typed or source id stands for: an LDA source id, or a confirmed-identity alias, resolves to its person.

    A pure function of the state it is given, so lock-free readers can resolve against the one view they hold.
    """
    value = str(value).strip()
    if value.startswith('lda:lobbyist:'):
        value = getattr(activity_provider, 'to_node', {}).get(value, 'person:' + value)
    elif value.startswith('person:lda:lobbyist:') and value not in nodes:
        # The node-id form of an LDA id folded into a spine person resolves like the bare source id.
        value = getattr(activity_provider, 'to_node', {}).get(value.removeprefix('person:'), value)
    seen = set()
    while value in aliases and value not in seen:
        seen.add(value)
        value = aliases[value]
    return value


@dataclass(frozen=True, slots=True)
class ServiceView:
    """Everything typeahead and the map read, taken together as one set of references.

    A refresh builds the next view off to the side and publishes it with a single assignment (LiveService._view), so a
    reader that takes the attribute once holds one generation's references (revision, static graph, profile people and
    their search index, aliases, router, office index): it can never pair one revision with another generation's router
    or profile index, and it needs no lock. The view cannot be edited, but it is not a frozen copy of the state behind
    it, because successive views share objects and a refresh touches some of them. Every view shares the static graph:
    before it publishes, a refresh assigns each person node's `contacts`, outside the lock, on nodes the previous
    view also reads (and one that rebuilds the router adds user-statement hub nodes to the graph's nodes).
    A refresh that finds the same routing inputs keeps the router and, under the service lock, reassigns its `nodes`
    mapping (and bumps `nodes_revision` only when a label, kind or name status differs), which a reader still holding
    the previous view then sees. Each of those is a single reference assignment, so a reader gets the old object or
    the new one, never a half-written one; but one answer can combine the view it took with the newer `router.nodes`
    or `contacts` of the next generation. The live fields a snapshot reports are taken in the same lock hold as the
    view (see `_snapshot`).
    """
    revision: str
    static: object
    profile_nodes: dict
    profile_search: list
    aliases: dict
    activity_provider: object
    router: object
    office_index: dict
    excluded: object
    index: ProfileSearchIndex


class _RefreshMemo:
    """What an unchanged refresh derived, with every object it was derived from.

    The key names the accepted profile snapshot (content revision, prefix hash, receipt digest); the environment is
    the static graph and its pending assertions, the identity aliases, the activity provider, the profile reader, the
    contacts and the router. The objects are held, so comparing them by identity is exact. A refresh whose inputs all
    match keeps the structures it published and rebuilds nothing; any difference, or a forced refresh, rebuilds.
    """
    __slots__ = ('key', 'environment', 'profile_nodes', 'profile_edges', 'by_person', 'search', 'nodes', 'digests')

    def __init__(self, key, environment, profile_nodes, profile_edges, by_person, search, nodes, digests):
        self.key, self.environment = key, environment
        self.profile_nodes, self.profile_edges, self.by_person, self.search = profile_nodes, profile_edges, by_person, search
        self.nodes, self.digests = nodes, digests

    def matches(self, service, key):
        return self.key == key and all(a is b for a, b in zip(self.environment, service._memo_environment()))


# A queued or running route search that no client has asked about for this long
# is cancelled; the sweeper wakes every ROUTE_REAP_SECONDS to look. Chrome slows
# the timers of a tab hidden for over five minutes to about one wake-up a minute,
# so a poll can be a minute apart for a search the person still wants. Closing a
# tab cancels at once (the page sends a cancel on pagehide); this sweeper is the fallback.
ROUTE_ABANDON_SECONDS = 90.0
ROUTE_REAP_SECONDS = 1.0

# The reference loop refreshes its displayed status (IdentityWebIndex.status(), full scans of a table that grows with every
# check: about 1 s of CPU at 933k outcome rows) at most this often, or at once when a reference changes; it ran after every
# lookup, every ~7 s, and was most of the sidecar's idle CPU. The lookup pace is unchanged (D-084).
IDENTITY_STATUS_SECONDS = 60.0

# D-084 pace = the configured wait after a worked lookup (3 s, unchanged since D-084); cadence varies with network time.
IDENTITY_WORKED_WAIT_SECONDS = 3

# Identical requests share one search (its id is a digest of the request), so a search can matter to several pages at
# once. Each page names itself with a holder token (it sends one random token per page load with every start, supersede
# and cancel). A cancel, a supersede or a closing page drops only that page's claim, and the search is cancelled only
# when no holder is left. A request that carries no token (an older page, a script) is the one shared anonymous holder:
# tokenless clients behave exactly as before among themselves (one tokenless cancel ends the search for all of them),
# and a tokenless cancel never ends a search another page holds under its own token. The sweeper above is unchanged:
# it works on the search, not on its holders.
ROUTE_ANONYMOUS_HOLDER = ''
ROUTE_HOLDER_MAX_CHARS = 64


def route_holder(value):
    """The holder a request names: its token (at most ROUTE_HOLDER_MAX_CHARS characters), or the anonymous holder."""
    if value is None:
        return ROUTE_ANONYMOUS_HOLDER
    if not isinstance(value, str):
        raise TypeError('A search holder must be a string')
    return value[:ROUTE_HOLDER_MAX_CHARS]


INSTITUTION_EDGE_COUNT_KEYS = ('membership', 'policy_area_membership', 'revolving')
IDENTITY_NOTES = {
    'USER_CONFIRMED': 'Identity confirmed by you; both source IDs and their dated evidence are retained',
    'RECORD_RESOLVED': 'Identity matched from verified source records; source IDs and dated evidence are retained',
    'REFERENCE_RESOLVED': 'Identity matched from references; source IDs and dated evidence are retained',
    'RECORD_AND_REFERENCE_RESOLVED': 'Identity matched from verified source records and references; source IDs, dated evidence and citations are retained',
}


def group_identity_basis(group):
    """The group's own proof kinds decide records versus references; every automatic group shares one overlay status literal."""
    basis = group.get('basis') or group.get('provenance', {}).get('basis')
    if basis:
        return str(basis)
    return 'REFERENCE_RESOLVED' if group.get('status') == 'REFERENCE_RESOLVED_SAME_PERSON' else 'USER_CONFIRMED'



try:
    from .staffer_assertions import load_coworker_assertions, record_coworker_decision, local_date
except ImportError:  # direct script execution: no package context
    import os as _bootstrap_os, sys as _bootstrap_sys
    _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
    if _bootstrap_root not in _bootstrap_sys.path:
        _bootstrap_sys.path.insert(0, _bootstrap_root)
    from fec.surfaces.staffer_assertions import load_coworker_assertions, record_coworker_decision, local_date

class LiveService:
    """Source polling and indexing run off-request; reads use immutable snapshots."""
    _view = None  # the published ServiceView: None until a refresh (or the first read) composes one
    _refresh_memo = None
    prewarm_enabled = False  # start() turns it on; a service built any other way (tests, tools, the identity rebuild's working copy) never prewarms

    def __init__(self, source_root, static_graph):
        self.source_root = Path(source_root)
        self.static = static_graph
        self.profiles = IncrementalProfiles(self.source_root / 'audits/staffer_network/politico_profile_crawl_20260903_raw')
        self._assertion_update_pending = False
        self.ready = False
        self.stage = 'Checking verified model artifacts'
        self.errors = []
        # The reason startup failed, kept apart from the rolling error list so a
        # later refresh failure cannot overwrite it. None once startup succeeds.
        self.startup_error = None
        self.lock = threading.RLock()
        self.profile_lock = threading.RLock()
        self.refresh_lock = threading.RLock()
        self.view_cache = OrderedDict()
        self.bytes_cache = OrderedDict()
        self.search_cache = OrderedDict()
        # Typeahead and the map read one immutable ServiceView without the service lock; these small locks only guard
        # their caches. They may be taken while holding the service lock; the service lock is never taken while holding one.
        self._search_lock = threading.Lock()
        self._view_cache_lock = threading.Lock()
        self._view = None
        self._refresh_memo = None
        self.route_jobs = OrderedDict()
        self.route_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="staffer-route")
        # Tests replace the clock to age a search without waiting for it.
        self.route_clock = time.monotonic
        self.route_reap_interval = ROUTE_REAP_SECONDS
        self._route_reaper = None
        self.evidence = {}
        self.inventory = {}
        self.identity_aliases = {}
        self.activity_provider = None
        self.identity_resolution = {}
        self.candidates_by_person = {}
        self._source_nodes = None
        self._source_edges = None
        self._source_episodes = None
        self._source_inventory = None
        self.router = None
        self.revision = 'loading'
        self.metadata = {}
        self.profile_nodes = {}
        self.profile_edges = []
        self.profile_by_person = {}
        self.profile_search = []
        self.metrics = {}
        self._stop = threading.Event()
        self.evidence_exceptions = []
        self.metadata_epoch = 0
        self.identity_web = None
        self.automatic_resolution = {}
        self.payroll_identity = None
        self._payroll_identity_loaded = False
        self.identity_progress = {}
        self._identity_profile_revision = None
        self._identity_profile_digests = {}
        self._identity_reference_revision = None
        self._identity_wakeup = threading.Event()
        self._full_network_revision = None
        self._full_network_payload = None
        self._explorer_lock = threading.Lock()
        self._explorer_index = None
        # Prewarm (see _start_prewarm). The lock guards the sequence number and the published record and is never held
        # while building; the serial lock lets one prewarm build at a time.
        self._prewarm_lock = threading.Lock()
        self._prewarm_serial = threading.Lock()
        self._prewarm_seq = 0
        self._prewarm_latest = None
        self._prewarm_finished = deque(maxlen=4)
        # Set when a publish replaced a router (_release_replaced_router), cleared by the prewarm that collects for it (under _prewarm_lock):
        # a collection runs only then, since at a start nothing was replaced and a collection would only stall every request thread.
        self._router_replaced = False
        self._groups_lock = threading.Lock()
        self._groups_index = None
        self.source_catalog = None
        self.lda_catalog = None
        self.lda_sidecar = None
        self._lda_sidecar_loaded = False
        # The 2026-09-07 employment ruling, read back once from the static
        # organisations artifact. See _load_lda_employment for why it is not
        # recomputed here.
        self.lda_employment = None
        self._lda_employment_loaded = False
        self.contacts = ContactIndex()

    def start(self):
        # A started service prewarms; MELANGE_STAFFER_PREWARM=0 turns that off without a code change. The entry is
        # published before the server's threads read the metrics, so the key never appears under a reader.
        self.prewarm_enabled = os.environ.get('MELANGE_STAFFER_PREWARM', '1').strip().lower() not in ('0', 'off', 'false', 'no')
        self.metrics['prewarm'] = {'enabled': self.prewarm_enabled, 'status': 'waiting' if self.prewarm_enabled else 'disabled',
                                   'stages': {}, 'earlier': []}
        threading.Thread(target=self._run, daemon=True, name='staffer-live-readonly').start()

    def _run(self):
        started = time.perf_counter()
        try:
            self.static.load()
            if self.static.errors:
                raise ValueError('; '.join(self.static.errors))
            self.metrics['model_load_ms'] = round(self.static.load_seconds * 1000, 2)
            self.stage = 'Resolving source names and dated employment evidence'
            mark = time.perf_counter()
            self.evidence = load_evidence(self.source_root, self.static)
            self.evidence_exceptions = [e for e in self.evidence.get('errors', []) if e.startswith('Ambiguous exact LDA identity link:')]
            self.errors.extend(e for e in self.evidence.get('errors', []) if e not in self.evidence_exceptions)
            self.static.nodes.update(self.evidence.get('nodes', {}))
            # Source-native people remain distinct unless the existing spine already
            # supplies an exact, unambiguous LDA identifier link.
            for episode in self.evidence.get('episodes', []):
                for key, kind in (('person_id', 'person'), ('employer_id', 'organization')):
                    ident = episode[key]
                    label = self.evidence.get('labels', {}).get(ident)
                    if ident not in self.static.nodes and label:
                        self.static.nodes[ident] = {'id': ident, 'kind': kind, 'label': label['label'],
                            'member_ids': [], 'committee_ids': [], 'policy_ids': [], 'routing_only': True,
                            'source_only_identity': True, 'review_status': 'REVIEW_REQUIRED',
                            'canonical_eligible': False, 'provenance': label.get('provenance', {})}
            for ident, label in self.evidence.get('labels', {}).items():
                if ident in self.static.nodes:
                    node = self.static.nodes[ident]
                    old = node.get('label')
                    node.update(label=label['label'], name_status=label.get('name_status', 'SOURCE_NAME'), label_provenance=label.get('provenance', {}))
                    if old and old != label['label'] and not old.startswith('Name unavailable'):
                        node.setdefault('aliases', []).append(old)
            for episode in self.evidence.get('episodes', []):
                node = self.static.nodes.get(episode['person_id'])
                observed = str(episode.get('as_of') or '')
                if node and observed >= node.get('last_reported_at', ''):
                    employer = self.static.nodes.get(episode['employer_id'], {})
                    label = episode.get('employer_label') or employer.get('label', 'Employer name unavailable')
                    node['last_reported_at'] = observed
                    node['source_summary'] = f"{label} · last reported {observed[:10] or 'date unknown'}"
                    if node.get('source_only_identity'):
                        node['identity_note'] = 'Separate source identity; not merged with similarly named payroll or lobbying records'
            self.stage = 'Indexing every verified source person'
            if CATALOG.is_file() and CATALOG.with_suffix('.receipt.json').is_file():
                self.source_catalog = SourceCatalog()
            if LDA_CATALOG.is_file() and LDA_CATALOG.with_suffix('.receipt.json').is_file():
                self.lda_catalog = LdaCatalog()
            self.inventory = load_inventory(self.source_root, self.static)
            for ident, assertions in getattr(self.static, 'pending_source_assertions', {}).items():
                for mapping in (self.inventory.get('nodes', {}), self.inventory.get('raw_nodes', {})):
                    if ident in mapping:
                        mapping[ident]['review_assertions'] = assertions
            if self.source_catalog:
                report = self.source_catalog.receipt
                self.inventory['sourcecoverage']['littlesis'] = {
                    'source_ids': report['counts'].get('LittleSis_person', 0),
                    'organizations': report['counts'].get('LittleSis_organization', 0),
                    'relationship_assertions': report['counts']['relationships'],
                    'missing_endpoint_assertions': report['counts']['relationships_with_missing_endpoint'],
                    'scope': report['scope']}
                self.inventory['sourcecoverage']['quorum'] = {'source_ids': report['counts']['Quorum_person'],
                    'scope': 'Every sealed directory ID, retained as source evidence with office and title; cross-source identity unresolved.'}
            self.errors.extend(self.inventory.get('exceptions', []))
            if self.lda_catalog:
                self.inventory.setdefault('sourcecoverage', {}).setdefault('lda', {}).update(
                    source_catalog=self.lda_catalog.receipt)
            self._source_nodes = {**self.static.nodes, **self.inventory.get('raw_nodes', {})}
            self._source_edges = list(self.static.edges)
            self._source_episodes = list(self.evidence.get('episodes', []))
            self._cache_source_inventory()
            self._apply_inventory()
            self._index_identity_candidates()
            self.identity_web = IdentityWebIndex(self.source_root)
            self.stage = 'Consolidating source identities from references'
            self._resolve_automatic_identities()
            self.metrics['names'] = dict(Counter('unresolved' if 'UNRESOLVED' in n.get('name_status', '') else 'named' for n in self.static.nodes.values()))
            self.metrics['ambiguous_identity_links_kept_separate'] = len(set(self.evidence_exceptions))
            self.metrics['evidence_and_names_ms'] = round((time.perf_counter() - mark) * 1000, 2)
            self.stage = 'Checking the complete lobbying activity index'
            activity_path = self.source_root / 'audits/staffer_live_runtime/lda_activity_v1.sqlite'
            activity_pin = self.source_root / 'audits/staffer_live_runtime/ecosystem_activity_pin.json'
            if activity_pin.is_file():
                selected_activity = json.loads(activity_pin.read_text(encoding='utf-8-sig'))
                if selected_activity.get('status') != 'PASS' or selected_activity.get('database') != 'lda_activity_v2.sqlite':
                    raise ValueError('Ecosystem activity pin not approved')
                activity_path = activity_pin.parent / selected_activity['database']
            if activity_path.is_file():
                mark = time.perf_counter()
                self.activity_provider = ActivityIndex(activity_path, self.static.nodes)
                if activity_pin.is_file() and self.activity_provider.receipt['sha256'] != selected_activity.get('sha256'):
                    raise ValueError('Ecosystem activity pin hash mismatch')
                self.metrics['activity_index_load_ms'] = round((time.perf_counter() - mark) * 1000, 2)
                self.inventory.setdefault('sourcecoverage', {}).setdefault('lda', {}).update(
                    routing_activity_source_ids=self.activity_provider.receipt['counts']['source_person_ids'])
            else:
                self.errors.append('Complete LDA activity routing index unavailable; only indexed evidence is routable')
            self.stage = 'Indexing date-overlap routes'
            self._refresh_profiles(force=True)
            self.ready = True
            self.stage = 'Ready'
            self.metrics['initial_ready_ms'] = round((time.perf_counter() - started) * 1000, 2)
            self._start_prewarm()
            threading.Thread(target=self.run_identity_reconciliation_loop, daemon=True, name='staffer-reference-resolver').start()
            threading.Thread(target=self.run_identity_apply_loop, daemon=True, name='staffer-profile-verifier').start()
        except Exception as exc:
            self.startup_error = str(exc)
            self.errors.append(self.startup_error)
            self.stage = 'Verified model unavailable'
        while not self._stop.wait(POLL_SECONDS):
            try:
                self._refresh_profiles()
            except Exception as exc:
                # Bounded on purpose -- this loop runs forever, so the refresh
                # failure REPLACES the previous poll's copy of itself rather than
                # accumulating. It must not replace the startup cause as well:
                # when startup failed there is no model to refresh, so the
                # refresh error is that failure's SYMPTOM, and overwriting the
                # list left the operator reading the symptom with the cause
                # (e.g. the missing artifact) gone. Keep the cause first.
                self.errors = ([self.startup_error] if self.startup_error else []) + [str(exc)]

    def _cache_source_inventory(self):
        """Retain only verified source metadata, not mutable identity overlays."""
        self._source_inventory = deepcopy({key: value for key, value in self.inventory.items()
            if key not in ('nodes', 'raw_nodes', 'identity_aliases', 'identity_groups')})
        cached = self._source_inventory
        cached['sources'] = [s for s in cached.get('sources', [])
                             if s.get('role') not in ('user_confirmed_identity_overlay', 'automatic_identity_overlay')]
        cached.setdefault('counts', {}).pop('user_confirmed_identity_groups', None)
        # load_inventory annotates named-example coverage with its current user
        # overlay. Restore those display annotations to the source identity.
        for source in cached.get('sourcecoverage', {}).values():
            for value in source.values():
                if not isinstance(value, list):
                    continue
                for record in value:
                    if not isinstance(record, dict) or not record.get('source_person_id'):
                        continue
                    native = 'person:' + record['source_person_id']
                    if native in (self._source_nodes or {}):
                        record['node_id'] = native
                    record.pop('identity_status', None)
                    record['identity_merge'] = False

    def _inventory_for_rebuild(self, graph):
        """Re-read only user decisions; sealed source verification remains cached."""
        if self._source_inventory is None:
            return load_inventory(self.source_root, graph)
        result = deepcopy(self._source_inventory)
        result.update(nodes={}, raw_nodes={})
        apply_identity_overlay(result, graph.nodes)
        result.setdefault('counts', {})['user_confirmed_identity_groups'] = len(result.get('identity_groups', []))
        return result

    def _apply_inventory(self):
        self.static.nodes.update(self.inventory.get('nodes', {}))
        self.identity_aliases = dict(self.inventory.get('identity_aliases', {}))
        for alias, primary in self.identity_aliases.items():
            if primary not in self.static.nodes:
                raise ValueError('User-confirmed identity target unavailable: ' + primary)
            self.static.nodes.pop(alias, None)
        # Remap only the display copy. The pinned source artifacts and operator
        # weights stay intact, with original endpoints attached as provenance.
        if self.identity_aliases:
            edges, adjacency = [], defaultdict(list)
            for original in self._source_edges or self.static.edges:
                source, target = self._resolve_id(original['source']), self._resolve_id(original['target'])
                if source == target:
                    continue
                edge = original
                if source != original['source'] or target != original['target']:
                    edge = {**original, 'source': source, 'target': target,
                        'identity_overlay': True, 'provenance': {**original.get('provenance', {}),
                        'original_endpoints': [original['source'], original['target']]}}
                adjacency[source].append(len(edges))
                adjacency[target].append(len(edges))
                edges.append(edge)
            self.static.edges, self.static.adjacency = edges, adjacency
        self.evidence['episodes'] = [dict(e) for e in (self._source_episodes or self.evidence.get('episodes', []))]
        episodes_by_identity = defaultdict(list)
        for episode in self.evidence['episodes']:
            episodes_by_identity[self._resolve_id(episode['person_id'])].append(episode)
        for group in self.inventory.get('identity_groups', []):
            primary = group['primary_id']
            node = self.static.nodes[primary]
            basis = group_identity_basis(group)
            node['identity_note'] = IDENTITY_NOTES.get(basis, 'Identity matched (' + basis + '); source IDs and dated evidence are retained')
            node['identity_basis'] = basis
            history = list(node.get('employment_history', []))
            node['employment_history'] = history
            for episode in episodes_by_identity.get(primary, []):
                original = episode['person_id']
                episode['person_id'] = primary
                episode['identity_status'] = group.get('status', 'USER_CONFIRMED_SAME_PERSON')
                episode['provenance'] = {**episode.get('provenance', {}),
                    'identity_resolution': node.get('identity_provenance'), 'original_person_id': original}
                node['employment_history'].append({
                    'source_person_id': original.removeprefix('person:'),
                    'organization': self.static.nodes.get(episode['employer_id'], {}).get('label', episode['employer_id']),
                    'reported_years': episode.get('filing_years'), 'last_reported_at': episode.get('as_of'),
                    'evidence_class': 'Reported firm association; continuous employment not established',
                    'provenance': episode['provenance']})
        self.static.reindex()

    def _index_identity_candidates(self):
        self.identity_resolution = load_identity_resolution(self.source_root, self.static, self.evidence.get('episodes', []))
        candidates = defaultdict(list)
        for group in self.identity_resolution.get('candidate_groups', []):
            for ident in {i for pair in group.get('candidate_pairs', []) for i in pair}:
                candidates[self._resolve_id(ident)].append(group)
        self.candidates_by_person = dict(candidates)
        for ident, node in self.static.nodes.items():
            if node.get('kind') == 'person':
                node['identity_candidate_count'] = len(candidates.get(ident, []))
        self.metrics['identity_review'] = self.identity_resolution.get('counts', {})

    def _capture_probe(self):
        """Refresh the reader, which runs every integrity check on its receipt and rows, and note what names the
        snapshot it accepted. Call with profile_lock held.

        'key' is set only for a reader that exposes a content revision, a prefix hash and a receipt digest: together
        they name the accepted snapshot, so an unchanged key means the graph built from it is unchanged.
        """
        summary = self.profiles.refresh()
        revision = getattr(self.profiles, 'profile_revision', None)
        memoizable = bool(revision)
        if not revision:
            revision = digest([r for r in self.profiles.accepted if self.profiles.eligible(r)])
        prefix, receipt = self.profiles.accepted_prefix_sha256, self.profiles.accepted_receipt_digest
        return {'summary': summary, 'revision': revision, 'prefix_sha256': prefix, 'receipt_sha256': receipt,
                'key': (revision, prefix, receipt) if memoizable and prefix is not None and receipt is not None else None}

    def _capture_graph(self, probe):
        """Bind graph, content digests and receipts to the snapshot `probe` refreshed. Call with profile_lock held."""
        rows = [r for r in self.profiles.accepted if self.profiles.eligible(r)]
        graph = self.profiles.graph('', max(1, len(self.profiles.accepted)))
        revision = probe['revision']
        content = getattr(self.profiles, 'profile_digests', {})
        rows_by_id = {'politico:' + str(r.get('profile_uuid')): r for r in rows if r.get('profile_uuid')}
        prefix = probe['prefix_sha256']
        receipt = probe['receipt_sha256']
        digests, captured = {}, []
        for original in graph['nodes']:
            node = dict(original)
            if node.get('kind') == 'person':
                ident = node['id']
                uuid = node.get('source_profile_uuid') or ident.removeprefix('politico:')
                fingerprint = node.get('profile_content_sha256') or content.get(uuid) or digest(rows_by_id.get(ident, original))
                proof = {**node.get('provenance', {}), 'path': str(getattr(self.profiles, 'path', self.profiles.raw_dir)),
                         'sha256': fingerprint, 'profile_uuid': uuid,
                         'receipt_sha256': receipt, 'accepted_prefix_sha256': prefix}
                node.update(source_profile_uuid=uuid, profile_content_sha256=fingerprint,
                            profile_pending=False, profile_status='RECEIPT_VERIFIED', provenance=proof)
                node['employment_history'] = [{**entry, 'provenance': proof}
                    for entry in node.get('employment_history', []) if isinstance(entry, dict)]
                source_row = rows_by_id.get(ident, {})
                node['profile_records'] = [{**{key: source_row.get(key, []) for key in
                    ('current_header_positions', 'employment_history', 'education', 'profile_issue_areas',
                     'explicit_side_labels', 'partition', 'observed_at')},
                    'profile_uuid': uuid, 'provenance': proof}]
                node['education'] = [{**entry, 'provenance': proof}
                    for entry in node.get('education', []) if isinstance(entry, dict)]
                assertions = getattr(self.static, 'pending_source_assertions', {}).get(ident, [])
                if assertions:
                    node['review_assertions'] = list({entry['assertion_id']: entry for entry in
                        [*node.get('review_assertions', []), *assertions]}.values())
                digests[ident] = fingerprint
            captured.append(node)
        return {'summary': probe['summary'], 'revision': revision, 'digests': digests,
                'graph': {**graph, 'nodes': captured}, 'prefix_sha256': prefix, 'receipt_sha256': receipt}

    def _capture_profiles(self):
        """Bind graph, content digests and receipts to one accepted reader snapshot."""
        with self.profile_lock:
            return self._capture_graph(self._capture_probe())

    @staticmethod
    def _merge_profile_node(prior, profile, ident):
        """Keep source identities and dated model evidence alongside the validated name."""
        merged = {**prior, **profile, 'id': ident, 'profile_pending': False}
        for key in ('member_ids', 'committee_ids', 'policy_ids'):
            merged[key] = sorted(set(prior.get(key, [])) | set(profile.get(key, [])))
        if prior.get('identity_status') in ('USER_CONFIRMED_SAME_PERSON', 'REFERENCE_RESOLVED_SAME_PERSON'):
            for key in ('identity_status', 'identity_basis', 'identity_note', 'identity_provenance',
                        'identity_references', 'source_person_ids', 'source_identity_records',
                        'identity_alias_ids', 'canonical_identity_promoted', 'provenance'):
                if key in prior:
                    merged[key] = prior[key]
        merged['aliases'] = sorted({str(v) for v in [*prior.get('aliases', []), *profile.get('aliases', []),
                                                    prior.get('label')] if v and v != merged.get('label')})
        uuid = profile.get('source_profile_uuid')
        history = [h for h in prior.get('employment_history', [])
                   if not uuid or h.get('provenance', {}).get('profile_uuid') != uuid]
        history.extend(profile.get('employment_history', []))
        merged['employment_history'] = list({digest(h): h for h in history}.values())
        for field in ('education', 'covered_positions', 'review_assertions', 'profile_records', 'reconciled_career_evidence'):
            combined = [*prior.get(field, []), *profile.get(field, [])]
            if combined:
                merged[field] = list({digest(entry): entry for entry in combined}.values())
        if ident != profile['id']:
            merged['profile_evidence'] = [p for p in prior.get('profile_evidence', []) if p.get('id') != profile['id']] + [profile]
        return merged

    def _resolve_automatic_identities(self):
        if not self.identity_web:
            return
        started = time.perf_counter()
        captured = self._capture_profiles()
        # New UUIDs and newly validated names enter the same inventory as the
        # original directory, including when the directory predates the profile.
        for profile in captured['graph']['nodes']:
            if profile.get('kind') != 'person':
                continue
            ident = self._resolve_id(profile['id'])
            node = self._merge_profile_node(self.static.nodes.get(ident, {}), profile, ident)
            self.static.nodes[ident] = node
            self.inventory.setdefault('nodes', {})[ident] = node
        self.static.reindex()
        nodes = {i: dict(n) for i, n in self.static.nodes.items()}
        lda_proof = next((p for p in self.identity_resolution.get('sources', []) if 'lobbyist' in p.get('path', '')), {})
        model_proof = next((p for p in self.static.sources if p.get('sha256') and p.get('path')), {})
        for ident, record in self.identity_resolution.get('records', {}).items():
            if ident in nodes and record.get('covered_positions'):
                nodes[ident]['covered_positions'] = record['covered_positions']
                nodes[ident]['covered_position_source_ids'] = record.get('covered_position_source_ids', [])
                nodes[ident]['provenance'] = {**nodes[ident].get('provenance', {}), **lda_proof}
        episodes = list(self.evidence.get('episodes', []))
        if not self._payroll_identity_loaded:
            self.payroll_identity = load_payroll_identity(self.source_root)
            self._payroll_identity_loaded = True
        if self.payroll_identity is not None:
            episodes.extend({**ep, 'person_id': self._resolve_id(ep['person_id'])}
                            for ep in self.payroll_identity['episodes'])
            self.metrics['payroll_identity'] = self.payroll_identity['counts']
        else:
            # Compatibility for graph fixtures with no installed payroll bundle.
            for ident, node in nodes.items():
                for stint in node.get('stints', []):
                    if stint.get('container_kind') == 'unresolved':
                        continue
                    episodes.append({'person_id': ident, 'employer_id': stint.get('container_id'),
                        'title': stint.get('title_last'), 'start': stint.get('core_start'),
                        'end_exclusive': stint.get('core_end_exclusive'), 'employment_verified': True,
                        'provenance': {**model_proof, 'stint': stint}})
        episodes.extend(identity_profile_episodes(nodes))
        try:
            from .staffer_identity_resolution import USER_ASSERTIONS
        except ImportError:  # direct script execution: no package context
            import os as _bootstrap_os, sys as _bootstrap_sys
            _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
                _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
            if _bootstrap_root not in _bootstrap_sys.path:
                _bootstrap_sys.path.insert(0, _bootstrap_root)
            from fec.surfaces.staffer_identity_resolution import USER_ASSERTIONS
        assertions = json.loads(USER_ASSERTIONS.read_text(encoding='utf-8'))
        self.metrics['identity_prepare_ms'] = round((time.perf_counter() - started) * 1000, 2)
        mark = time.perf_counter()
        reference_revision = self.identity_web.revision() if hasattr(self.identity_web, 'revision') else None
        resolved = resolve_identities(nodes, episodes, self.identity_web.references(), self.identity_aliases,
                                      assertions.get('keep_separate_pairs', []))
        if self.payroll_identity is not None:
            resolved = consolidate_record_pairs(resolved, nodes, self.payroll_identity['record_pairs'],
                self.payroll_identity['proof'], assertions.get('keep_separate_pairs', []),
                self.inventory.get('identity_groups', []))
        self.metrics['identity_resolve_ms'] = round((time.perf_counter() - mark) * 1000, 2)
        mark = time.perf_counter()
        from fec.surfaces.staffer_reconciled_evidence import load_release, merge_bindings
        release = load_release(self.source_root / 'audits/staffer_live_runtime')
        resolved = merge_bindings(resolved, nodes, self.identity_aliases,
            self.inventory.get('identity_groups', []), release, assertions.get('keep_separate_pairs', []))
        self.automatic_resolution = resolved
        if release:
            self.metrics['reconciled_evidence'] = resolved.get('reconciled_evidence', {})
        self.identity_web.enqueue_people(nodes, source_revision=digest([self.static.sources, self.evidence.get('sources'),
            self.inventory.get('sources'), captured['revision']]))
        if hasattr(self.identity_web, 'prioritize_people'):
            self.identity_web.prioritize_people({n['id']: n for n in captured['graph']['nodes'] if n.get('kind') == 'person'})
        self.identity_web.enqueue(resolved['unresolved_candidates'])
        # Seeded people can also gain references after a source-only match.
        self.identity_web.enqueue([{'display_name': s.get('name'), 'node_ids': []} for s in self.identity_web.seeds if s.get('name')])
        self.metrics['identity_queue_ms'] = round((time.perf_counter() - mark) * 1000, 2)
        groups = resolved['identity_groups']
        if groups:
            aliases = {**self.identity_aliases, **resolved['identity_aliases']}
            def canonical(ident):
                seen = set()
                while ident in aliases and ident not in seen:
                    seen.add(ident)
                    ident = aliases[ident]
                return ident
            aliases = {a: canonical(a) for a in aliases}
            aliases_by_primary = defaultdict(list)
            for alias, primary in aliases.items():
                aliases_by_primary[primary].append(alias)
            auto_primaries = {g['primary_id'] for g in groups}
            retained = [g for g in self.inventory.get('identity_groups', []) if canonical(g['primary_id']) not in auto_primaries]
            for group in groups:
                group['alias_ids'] = sorted(aliases_by_primary[group['primary_id']])
            overlay = {'nodes': {}, 'sources': []}
            apply_identity_overlay(overlay, self.static.nodes, [*retained, *groups])
            self.inventory['nodes'].update(overlay['nodes'])
            self.inventory['identity_groups'] = overlay['identity_groups']
            self.inventory['identity_aliases'] = aliases
            self.inventory['sources'] = [s for s in self.inventory['sources'] if s.get('role') != 'automatic_identity_overlay']
            self.inventory['sources'].append({'role': 'automatic_identity_overlay', 'sha256': digest(groups),
                'path': str(self.identity_web.path), 'evidence_version': resolved['evidence_version']})
            self._apply_inventory()
            self._index_identity_candidates()
        # Plain-words lobbying status, ruled 2026-09-06 (D-022 item 3). Attached
        # after the identity overlay so a person who carries several LDA records
        # is labelled from all of them at once.
        self.metrics['lda_status'] = lda_status_labels.attach(self.static.nodes, self._load_lda_sidecar())
        self.identity_progress = {**self.identity_web.status(),
            'automatic_groups': len(groups), 'automatic_aliases': sum(len(g['alias_ids']) for g in groups),
            'unresolved_groups': len({tuple(sorted(c['node_ids'])) for c in resolved['unresolved_candidates']})}
        self._identity_profile_revision = captured['revision']
        self._identity_profile_digests = captured['digests']
        self._identity_reference_revision = reference_revision
        self._update_profile_progress(captured)
        self.metrics['automatic_identity_ms'] = round((time.perf_counter() - started) * 1000, 2)
        report = {k: v for k, v in resolved.items() if k != 'unresolved_candidates'}
        report.update(generated_at=identity_now(), progress=self.identity_progress)
        target = self.identity_web.path.with_name('identity_resolution_receipt.json')
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(target)

    def _load_lda_sidecar(self):
        """Load the pinned tiered-corroboration sidecar once, byte-bound.

        Registrant display names come from the sealed LDA catalog's own
        organisation labels where it has them, so the surface never invents a
        name for an employer.
        """
        if self._lda_sidecar_loaded:
            return self.lda_sidecar
        self._lda_sidecar_loaded = True
        labels = {}
        if self.lda_catalog is not None:
            try:
                from contextlib import closing
                with closing(self.lda_catalog.connect()) as database:
                    for ident, label in database.execute(
                            "SELECT id,label FROM entities WHERE kind='organization' AND id LIKE 'lda:registrant:%'"):
                        if label:
                            labels['organization:' + ident] = label
            except Exception as exc:
                self.errors.append('Lobbying employer names unavailable: ' + str(exc))
        try:
            self.lda_sidecar = lda_status_labels.load_sidecar(
                lda_status_labels.sidecar_dir(self.source_root), labels,
                employment=self._load_lda_employment())
        except Exception as exc:
            self.lda_sidecar = None
            self.errors.append('Lobbying status labels unavailable: ' + str(exc))
        return self.lda_sidecar

    def _load_lda_employment(self):
        """The 2026-09-07 employment ruling, read back from the static artifact.

        The ruling itself is a join over ~540 MB of LDA corpus and takes about a
        minute, which is not something a web request can do, so this reads the
        organisations artifact the producer already wrote.  Cached for the life of
        the service and FAIL-OPEN by design: a missing or half-written artifact
        leaves ``employer_line`` on the sidecar's own year-ordered registrant,
        which is what it has always been, rather than taking the Groups tab down.
        Anything that goes wrong is surfaced in ``self.errors``, never swallowed.
        """
        if self._lda_employment_loaded:
            return self.lda_employment
        self._lda_employment_loaded = True
        try:
            from ..staffer_network import lda_employment as ruling
        except ImportError:
            try:
                from fec.staffer_network import lda_employment as ruling
            except ImportError as exc:
                self.errors.append('Employment ruling module unavailable: ' + str(exc))
                return None
        # TWO artifacts since the 2026-09-07 client normalization: the employer
        # half is still on the registrant cards, and the client ties -- all 41,203
        # organisations and all 132,738 of them -- are in the normalized sibling.
        # The loader merges them; naming the sibling here rather than letting it
        # default keeps this service's paths in one place.
        engine = self.source_root / 'data' / 'engine'
        path = str(engine / 'staffer_organisations_section.json')
        clients_path = str(engine / getattr(ruling, 'CLIENTS_FILENAME',
                                            'staffer_clients_section.json'))
        try:
            self.lda_employment = ruling.load_from_organisations_artifact(
                path, clients_path=clients_path)
        except Exception as exc:                            # noqa: BLE001 - reported below
            self.lda_employment = None
            self.errors.append('Employment ruling unavailable: ' + str(exc))
            return None
        if self.lda_employment is None:
            self.errors.append('Employment ruling not applied: no organisations artifact at '
                               + path)
        return self.lda_employment

    def run_identity_reconciliation_loop(self):
        """Resume web lookups without blocking incoming profile reconciliation."""
        status_due = 0.0
        while not self._stop.is_set():
            try:
                before = self.identity_web.revision()
                worked = self.identity_web.process_one()
                changed = self.identity_web.revision() != before
                if changed:
                    self._identity_wakeup.set()
                # status() reads the reference database (a second or more as it grows): it runs before the
                # lock is taken, and the lock only covers the dictionary update, the epoch bump and the cache clear.
                # Between refreshes only the heartbeat moves (IDENTITY_STATUS_SECONDS).
                status = {}
                if changed or time.monotonic() >= status_due:
                    status = self.identity_web.status()
                    status_due = time.monotonic() + IDENTITY_STATUS_SECONDS
                heartbeat = identity_now()
                with self.lock:
                    self.identity_progress.update(status, loop_heartbeat_at=heartbeat)
                    self.metadata_epoch += 1
                    self.bytes_cache.clear()
                if self._stop.wait(IDENTITY_WORKED_WAIT_SECONDS if worked else 30):
                    break
            except Exception as exc:
                status_due = 0.0  # the next pass replaces the attention status with a fresh one
                with self.lock:
                    self.identity_progress.update(status='reference_check_attention', errors=[str(exc)], loop_heartbeat_at=identity_now())
                if self._stop.wait(30):
                    break

    def run_identity_apply_loop(self):
        """Apply new validated profiles independently of slow network requests."""
        last_apply = time.monotonic()
        while not self._stop.is_set():
            self._identity_wakeup.wait(30)
            self._identity_wakeup.clear()
            if self._stop.is_set():
                break
            try:
                with self.lock:
                    profiles_pending = self._identity_profile_revision != getattr(self, '_profile_revision', None)
                references_pending = self.identity_web.revision() != self._identity_reference_revision
                if profiles_pending or (references_pending and time.monotonic() - last_apply >= 90):
                    self._rebuild_identity()
                    last_apply = time.monotonic()
            except Exception as exc:
                with self.lock:
                    self.identity_progress.update(status='reference_check_attention', errors=[str(exc)])
                    self.metadata_epoch += 1
                    self.bytes_cache.clear()
                if self._stop.wait(30):
                    break

    def assertion_decision(self, payload):
        with self.lock:
            if not self.ready or self._assertion_update_pending:
                raise ValueError('Wait for the current update to finish')
            result = record_coworker_decision(payload.get('assertion_id'), payload.get('action'),
                payload.get('effective_date'), payload.get('statement'))
            self._assertion_update_pending = True
            self.ready, self.stage = False, 'Applying your relationship statement'
            self.bytes_cache.clear()
        threading.Thread(target=self._apply_assertion_update, daemon=True, name='staffer-assertion-update').start()
        return {'ok': True, 'status': 'reindexing', 'decision': result}

    def _apply_assertion_update(self):
        try:
            self._refresh_profiles(force=True)
            with self.lock:
                self._assertion_update_pending = False
                self.ready, self.stage = True, 'Ready'
                self.bytes_cache.clear()
            self._start_prewarm()
        except Exception as exc:
            with self.lock:
                self.errors.append('Relationship update failed: ' + str(exc))
                self.stage = 'Relationship update needs attention'

    def identity_decision(self, primary_id, alias_ids, decision):
        with self.lock:
            if not self.ready or self._assertion_update_pending:
                raise ValueError('An identity update is already running; retry when the view is ready')
            if not isinstance(alias_ids, list) or not 1 <= len(alias_ids) <= 50:
                raise ValueError('Choose one or more matching source records')
            primary = self._resolve_id(primary_id)
            aliases = sorted({self._resolve_id(i) for i in alias_ids} - {primary})
            ids = {primary, *aliases}
            if not aliases or any(self.static.nodes.get(i, {}).get('kind') != 'person' for i in ids):
                raise ValueError('Identity decisions require distinct known people')
            if not any(ids <= {self._resolve_id(i) for i in group['node_ids']}
                       for group in self.candidates_by_person.get(primary, [])):
                raise ValueError('These records are not in the displayed identity review group')
            result = record_identity_decision(primary, aliases, decision, display_name=self.static.nodes[primary]['label'])
            self.ready, self.stage = False, 'Applying your identity decision'
            self.bytes_cache.clear()
            threading.Thread(target=self._rebuild_identity, daemon=True, name='staffer-identity-review').start()
            return {'ok': True, 'status': 'reindexing', 'decision': result['decision']}

    def _rebuild_identity(self):
        with self.refresh_lock:
            try:
                # Build a replacement off-request. Searches and paths keep using
                # the last complete graph until the replacement is ready to swap.
                graph = copy(self.static)
                graph.nodes, graph.edges = {i: dict(n) for i, n in self._source_nodes.items()}, list(self._source_edges)
                graph.adjacency = defaultdict(list)
                graph._prefix_nodes, graph._search_text, graph._by_member = defaultdict(set), {}, defaultdict(set)
                graph._view_cache = {}
                graph._lock = threading.Lock()
                for index, edge in enumerate(graph.edges):
                    graph.adjacency[edge['source']].append(index)
                    graph.adjacency[edge['target']].append(index)
                working = LiveService(self.source_root, graph)
                working.profiles, working.profile_lock = self.profiles, self.profile_lock
                working.identity_web = self.identity_web
                working.payroll_identity = self.payroll_identity
                working._payroll_identity_loaded = self._payroll_identity_loaded
                working.lda_catalog = self.lda_catalog
                working.source_catalog = self.source_catalog
                working.lda_sidecar = self.lda_sidecar
                working._lda_sidecar_loaded = self._lda_sidecar_loaded
                working.lda_employment = self.lda_employment
                working._lda_employment_loaded = self._lda_employment_loaded
                working._source_nodes, working._source_edges, working._source_episodes = self._source_nodes, self._source_edges, self._source_episodes
                working._source_inventory = self._source_inventory
                working.evidence = {**self.evidence, 'episodes': list(self._source_episodes)}
                working.evidence_exceptions = self.evidence_exceptions
                working.inventory = self._inventory_for_rebuild(graph)
                working._apply_inventory()
                working._index_identity_candidates()
                working._resolve_automatic_identities()
                if self.activity_provider:
                    working.activity_provider = ActivityIndex(self.activity_provider.database, graph.nodes)
                working._refresh_profiles(force=True)
                swapped = ('static', 'evidence', 'inventory', 'identity_aliases', 'activity_provider',
                           'identity_resolution', 'candidates_by_person', 'automatic_resolution', 'identity_progress',
                           'router', 'profile_nodes', 'profile_edges', 'profile_by_person', 'profile_search',
                           'revision', '_profile_revision', '_assertion_revision', 'metadata', '_identity_profile_revision',
                           '_identity_profile_digests', '_identity_reference_revision',
                           'payroll_identity', '_payroll_identity_loaded',
                           'lda_sidecar', '_lda_sidecar_loaded',
                           'lda_employment', '_lda_employment_loaded')
                with self.lock:
                    # The replaced structures are released after the lock is dropped: freeing them is not instant.
                    retired = tuple(getattr(self, field) for field in swapped) + (self._view, self._refresh_memo)
                    for field in swapped:
                        setattr(self, field, getattr(working, field))
                    self.metrics.update(working.metrics)
                    self.metadata_epoch += 1
                    self.ready, self.stage = True, 'Ready'
                    # One assignment hands lock-free readers the replacement's view: they see the old state or the
                    # new one, never a mixture of the two. (None, if it has none, makes the next read compose one.)
                    self._view = working._view
                    self._refresh_memo = None
                    self._clear_view_caches()
                    self.bytes_cache.clear()
                    if self._identity_profile_revision != self._profile_revision:
                        self._identity_wakeup.set()
                replaced, retired = retired[swapped.index('router')], None
                self._release_replaced_router(replaced, self.router)
                replaced = None
                self._start_prewarm()
            except Exception as exc:
                with self.lock:
                    self.identity_progress.update(status='reference_check_attention', errors=['Identity update failed: ' + str(exc)])
                    self.ready, self.stage = bool(self.router), 'Ready' if self.router else 'Identity update needs attention'
                raise

    def _resolve_id(self, value):
        # The same rules as resolve_id (which the lock-free readers apply to their view), kept in place here so a
        # service that holds only some of this state still resolves what it can: the static graph and the activity
        # provider are touched only for an LDA id. test_staffer_route_latency pins the two together.
        value = str(value).strip()
        if value.startswith('lda:lobbyist:'):
            value = getattr(self.activity_provider, 'to_node', {}).get(value, 'person:' + value)
        elif value.startswith('person:lda:lobbyist:') and value not in self.static.nodes:
            # The node-id form of an LDA id folded into a spine person resolves like the bare source id.
            value = getattr(self.activity_provider, 'to_node', {}).get(value.removeprefix('person:'), value)
        seen = set()
        while value in self.identity_aliases and value not in seen:
            seen.add(value)
            value = self.identity_aliases[value]
        return value

    def _user_episodes(self):
        episodes = []
        for assertion in self.evidence.get('user_assertions', []):
            hub = 'routing:user-stated-current-team:' + digest(assertion.get('person_ids', []))[:16]
            self.static.nodes.setdefault(hub, {'id': hub, 'kind': 'organization',
                'label': 'Shared current workplace (user statement)', 'member_ids': [], 'committee_ids': [],
                'policy_ids': [], 'routing_only': True, 'name_status': 'USER_ASSERTED_GROUP', 'canonical_eligible': False})
            as_of = assertion.get('as_of')
            if not as_of:
                continue
            for person in assertion.get('person_ids', []):
                episodes.append({'person_id': self._resolve_id(person), 'employer_id': hub, 'as_of': as_of,
                    'start': as_of, 'end_exclusive': (date.fromisoformat(as_of) + timedelta(days=1)).isoformat(),
                    'current_evidence': True, 'user_asserted': True, 'review_only': True,
                    'confidence': 'UNKNOWN', 'date_precision': 'USER_STATEMENT_AS_OF_DAY',
                    'assertion_end_exclusive': assertion.get('effective_until'),
                    'provenance': assertion})
        return episodes

    def _refresh_profiles(self, force=False):
        with self.refresh_lock:
            return self._refresh_profiles_locked(force)

    def _check_review_assertion_coverage(self, nodes):
        spec = next((entry for entry in self.static.receipt.get('files', [])
                     if entry.get('role') == 'staffer_social_politico_review_layers'), None)
        if spec is None:
            return
        assertions = {row['assertion_id']: row for node in nodes.values()
                      for row in node.get('review_assertions', [])}
        bad_flags = sum(bool(row.get('operator_included') or row.get('canonical_eligible'))
                        for row in assertions.values())
        if len(assertions) != spec['records'] or bad_flags:
            raise ValueError(f"Review assertion coverage mismatch: {len(assertions)} served / {spec['records']} declared; {bad_flags} promoted")
        self.metrics['review_assertion_coverage'] = {'status': 'PASS', 'declared': spec['records'],
            'served_unique': len(assertions), 'promoted': bad_flags,
            'layers': dict(Counter(row['layer'] for row in assertions.values()))}

    def _update_profile_progress(self, captured):
        digests = captured['digests']
        checked = sum(self._identity_profile_digests.get(ident) == value for ident, value in digests.items())
        pending = len(digests) - checked
        # Membership in the last identity pass is not a per-profile match outcome; report the outcomes too.
        links = sum(any(str(i).startswith('politico:') for i in (g.get('primary_id'), *g.get('alias_ids', [])))
                    for g in self.automatic_resolution.get('identity_groups', []))
        self.identity_progress['politico_ingestion'] = {
            'status': 'identity_check_pending' if pending or captured['revision'] != self._identity_profile_revision else 'identity_checks_current',
            'validated_profiles': len(digests), 'identity_checked_profiles': checked, 'pending_profiles': pending,
            'politico_links_found': links,
            'identity_checked_semantics': 'identity_checked_profiles counts validated profiles whose content digest was compared in the last identity pass; it is not a per-profile match outcome (politico_links_found = automatic identity groups that include a Politico profile)',
            'accepted_revision': captured['revision'], 'identity_applied_revision': self._identity_profile_revision,
            'receipt_sha256': captured['receipt_sha256'], 'accepted_prefix_sha256': captured['prefix_sha256'],
            'note': 'Checked against available identity evidence; ambiguous matches continue through web verification.'}
        self.inventory.setdefault('sourcecoverage', {}).setdefault('politico', {}).update(
            validated_profiles=len(digests), identity_checked_profiles=checked, pending_identity_profiles=pending)

    def _memo_environment(self):
        """The objects a refresh derives its profile structures from, besides the accepted snapshot itself."""
        return (self.static, getattr(self.static, 'pending_source_assertions', None), self.identity_aliases,
                self.activity_provider, self.profiles, self.contacts, self.router)

    def _compose_view(self, *, revision, router, profile_nodes, profile_search):
        """The immutable view of this state, its typeahead index built. Call where the state is stable (under the
        refresh lock) and before taking the service lock: building the index is the slow part."""
        excluded = getattr(getattr(router, 'person_facts', None), 'network_excluded', ())
        return ServiceView(revision, self.static, profile_nodes, profile_search, self.identity_aliases, self.activity_provider,
                           router, getattr(router, 'office_index', {}), excluded,
                           ProfileSearchIndex(profile_nodes, profile_search, excluded))

    def _current_view(self):
        view = self._view
        if view is None:
            # No refresh published one (the state was set by hand): compose it from what the service holds.
            with self.lock:
                view = self._view
                if view is None:
                    view = self._view = self._compose_view(revision=self.revision, router=self.router,
                                                          profile_nodes=self.profile_nodes, profile_search=self.profile_search)
        return view

    def _clear_view_caches(self):
        """Empty the map and typeahead caches. Each has its own lock, taken here inside the service lock."""
        with self._view_cache_lock:
            self.view_cache.clear()
        with self._search_lock:
            self.search_cache.clear()

    def _refresh_profiles_locked(self, force=False):
        started = time.perf_counter()
        as_of = local_date()
        assertions = load_coworker_assertions()
        assertion_revision = digest(assertions)
        self.evidence['user_assertions'] = assertions
        with self.profile_lock:
            # The reader refreshes on every cycle, and with it runs every integrity check on its receipt and rows.
            # When the accepted snapshot and everything the derived structures were built from are unchanged, those
            # structures are not rebuilt (see _RefreshMemo).
            probe = self._capture_probe()
            summary = probe['summary']
            # Receipt-only exception/count changes do not rebuild the route graph.
            profile_revision = probe['revision']
            from fec.staffer_network.member_context import load_member_context
            member_context = load_member_context(self.source_root)
            member_context_revision = digest(member_context.provenance)
            changed = force or member_context_revision != getattr(self, '_member_context_revision', None) or assertion_revision != getattr(self, '_assertion_revision', None) or profile_revision != getattr(self, '_profile_revision', None) or getattr(self.router, 'as_of', None) != as_of
            memo = self._refresh_memo
            reuse = not changed and memo is not None and probe['key'] is not None and memo.matches(self, probe['key'])
            if reuse:
                captured = {'summary': summary, 'revision': profile_revision, 'digests': memo.digests, 'graph': None,
                            'prefix_sha256': probe['prefix_sha256'], 'receipt_sha256': probe['receipt_sha256']}
            else:
                captured = self._capture_graph(probe)
        if reuse:
            profile_nodes, profile_edges, by_person, search, nodes = memo.profile_nodes, memo.profile_edges, memo.by_person, memo.search, memo.nodes
        else:
            full = captured['graph']
            profile_nodes = {}
            for node in full['nodes']:
                original, ident = node['id'], self._resolve_id(node['id'])
                if ident != original or self.static.nodes.get(ident, {}).get('identity_status') in ('USER_CONFIRMED_SAME_PERSON', 'REFERENCE_RESOLVED_SAME_PERSON'):
                    prior = profile_nodes.get(ident) or self.static.nodes.get(ident, {})
                    profile_nodes[ident] = self._merge_profile_node(prior, node, ident)
                else:
                    profile_nodes[ident] = node
            nodes = {**self.static.nodes, **profile_nodes}
        # An integrity check: it runs on every cycle, over the nodes about to be (or already) served.
        self._check_review_assertion_coverage(nodes)
        if not reuse:
            profile_edges = [{**edge, 'source': self._resolve_id(edge['source']),
                              'target': self._resolve_id(edge['target'])} for edge in full['edges']]
            by_person = {}
            for edge in profile_edges:
                by_person.setdefault(edge['source'], []).append(edge)
            # Typeahead reads what a person reads: the id is not part of the text, so "co" does not match `politico:`.
            search = [(n['id'], ProfileSearchIndex.haystack(n)) for n in profile_nodes.values() if n['kind'] == 'person']
        self.metrics['profile_refresh_reused'] = reuse
        retired = None
        if changed:
            # Politico source identities remain visibly separate and unrouted until
            # their employment dates and employer identities meet routing gates.
            user_episodes = self._user_episodes()
            # The user-statement hubs were just added to the static nodes: merge again so the router has them.
            nodes = {**self.static.nodes, **profile_nodes}
            revision = digest([self.static.verification.get('primary'), self.static.sources,
                               self.evidence.get('sources'), self.inventory.get('sources'),
                               getattr(self.activity_provider, 'revision', None), profile_revision, as_of, assertion_revision,
                               OVERLAP_VERSION, AFFINITY_VERSION, WORK_ROUTING_VERSION, IDENTITY_VERSION, NAME_STANDARD_VERSION,
                               self.contacts.revision, 'network-scope-no-interns-v1', member_context_revision])[:20]
            mark = time.perf_counter()
            profile_work_episodes = identity_profile_episodes(nodes)
            router = AdvocacyRouter.from_static(self.static,
                extra_episodes=[*self.evidence.get('episodes', []), *profile_work_episodes, *user_episodes],
                revision=revision, as_of=as_of, activity_provider=self.activity_provider, nodes=nodes)
            router.nodes = nodes
            router.nodes_revision, router.routing_signature = 0, routing_signature(nodes)
            router.member_context = member_context
            router.person_facts = PersonFacts(nodes, [*self.evidence.get('episodes', []), *profile_work_episodes, *user_episodes], as_of)
            router.operational_staffer_ids = set(self.inventory.get('operational_staffer_ids', ())) or None
            contacts = copy(self.contacts)
            contacts.bind(nodes, router.person_facts)
            # bind() attaches a person's contacts by their node id; ContactIndex.get also accepts the person:
            # spelling of a source id. The first poll after a rebuild used to re-attach every node's contacts through
            # get(), and that is the state people saw from then on. It is attached here, once, so the next poll can
            # reuse what this refresh built instead of building it a second time.
            for ident, node in nodes.items():
                if node.get('kind') == 'person':
                    node['contacts'] = contacts.get(ident)
            self.metrics['contacts'] = contacts.coverage
            router.overlap_routes = OverlapRoutes(router, router.person_facts)
            router.closeness()  # created before the router is published, so no request has to create it (or race to)
            router.office_index = office_index(nodes, router.person_facts)
            self.metrics['person_facts'] = router.person_facts.coverage
            self.metrics['searchable_person_nodes'] = sum(n.get('kind') == 'person' for n in nodes.values())
            self.metrics['routing_index_ms'] = round((time.perf_counter() - mark) * 1000, 2)
            view = self._compose_view(revision=revision, router=router, profile_nodes=profile_nodes, profile_search=search)
            with self.lock:
                # Everything was built above; this only swaps it in. The replaced structures are released after the
                # lock is dropped, because freeing them is not instant.
                retired = (self.router, self.contacts, self.profile_nodes, self.profile_edges, self.profile_by_person,
                           self.profile_search, self._view, self._refresh_memo)
                self.router = router
                self._member_context_revision = member_context_revision
                self.contacts = contacts
                quorum_coverage = self.inventory.setdefault('sourcecoverage', {}).setdefault('quorum', {})
                quorum_coverage.update(directory_source_ids=(self.source_catalog.receipt.get('counts', {}).get('Quorum_person') if self.source_catalog else None),
                    source_ids=len(contacts.records), contacts_matched_to_network_profiles=contacts.coverage.get('matched_people'),
                    scope='Distinct source IDs across the held directory and contact exports; contact matches enrich existing profiles without merging identities.')
                self._assertion_revision = assertion_revision
                self.profile_nodes, self.profile_edges = profile_nodes, profile_edges
                self.profile_by_person, self.profile_search = by_person, search
                self.revision = revision
                self._profile_revision = profile_revision
                self._view = view
                # What this refresh built is what an unchanged next cycle would build again: record it.
                self._refresh_memo = (_RefreshMemo(probe['key'], self._memo_environment(), profile_nodes, profile_edges,
                                                   by_person, search, nodes, captured['digests'])
                                      if probe['key'] is not None else None)
                self._clear_view_caches()
            replaced, retired = retired[0], None
            self._release_replaced_router(replaced, router)
            replaced = None
            self._start_prewarm()
            if self.identity_web and hasattr(self.identity_web, 'prioritize_people'):
                self.identity_web.prioritize_people({n['id']: n for n in full['nodes'] if n.get('kind') == 'person'})
        elif not reuse:
            # Equal routing content, but the accepted snapshot (a new byte binding or receipt) or something it was
            # built from moved: the profile structures are rebuilt (above) and the route index is kept. The merged
            # mapping, its fingerprint, the contacts on the new profile nodes and the view are all built here,
            # before the lock is taken.
            signature = routing_signature(nodes)
            for ident, node in nodes.items():
                if node.get('kind') == 'person':
                    node['contacts'] = self.contacts.get(ident)
            view = self._compose_view(revision=self.revision, router=self.router, profile_nodes=profile_nodes, profile_search=search)
        with self.lock:
            if not changed and not reuse:
                retired = (self.profile_nodes, self.profile_edges, self.profile_by_person, self.profile_search,
                           self.router.nodes, self._view, self._refresh_memo)
                self.profile_nodes, self.profile_edges = profile_nodes, profile_edges
                self.profile_by_person, self.profile_search = by_person, search
                self.router.nodes = nodes
                # The same-name index holds the mapping it was built from: dropped, the replaced mapping is freed with the
                # structures retired below, and the next request that needs the index builds it from this one.
                self.router.forget_contact_groups()
                if signature != getattr(self.router, 'routing_signature', None):
                    # Routing reads these fields: only a real difference empties the route caches.
                    self.router.routing_signature = signature
                    self.router.nodes_revision = getattr(self.router, 'nodes_revision', 0) + 1
                self._view = view
                self._refresh_memo = (_RefreshMemo(probe['key'], self._memo_environment(), profile_nodes, profile_edges,
                                                   by_person, search, nodes, captured['digests'])
                                      if probe['key'] is not None else None)
            self.metadata = summary
            self._update_profile_progress(captured)
            if self.identity_web and self._identity_profile_revision != profile_revision:
                self._identity_wakeup.set()
            self.metadata_epoch += 1
            self.bytes_cache.clear()
            if self._full_network_revision != (self._profile_revision, self.revision):
                # The cached full network belongs to one (profile revision, source revision) pair: keep it while
                # that pair is current, drop it when either moved.
                self._full_network_revision = None
                self._full_network_payload = None
        retired = None
        self.metrics['source_refresh_ms'] = round((time.perf_counter() - started) * 1000, 2)

    # ------------------------------------------------------------------ prewarm
    # After a router is published and the service is ready (startup, the daily as_of rebuild, an evidence or identity
    # rebuild), a background thread builds what the first search or page load would otherwise build while a person
    # waits: the work index for the page's default options (about forty seconds on the live records), then the
    # people index. The answers are the ones a lazy build gives, since it is the same build: a request that arrives
    # during a prewarm waits for that build (ClosenessRoutes.index, network_index) instead of starting another.
    # Memory is the constraint (D-087): a publish releases the replaced router's work state at once
    # (_release_replaced_router), and the prewarm that follows a replacement runs one collection before its first build (a prewarm at a
    # start follows none, so it runs none), which is meant to keep the old indexes and the new ones from being resident together. It is not
    # guaranteed: a request that was already running on the replaced router keeps the index it holds until it ends, and a long one can
    # outlast the collection and the new router's build, so the two can be resident together for that long.
    PREWARM_STAGES = ('work_index', 'network_index')

    def _release_replaced_router(self, replaced, current):
        """Let go of what the router the service has just replaced kept for its searches (its work indexes, hop-row caches and
        name index), so that they are not held while the new router's are built. A search still running on it keeps what it
        holds, one that asks afterwards builds its own (the replaced router stores no work index or hop-row cache from then on; a
        distinct pass or avoid_people request can still store its name index, freed with it), and no answer changes
        (AdvocacyRouter.release_search_state). The router itself is freed by the collection that the next prewarm runs, which this
        call asks for. Never raises, never takes the service lock."""
        if replaced is None or replaced is current:
            return
        with self._prewarm_lock:
            self._router_replaced = True
        try:
            replaced.release_search_state()
        except Exception as exc:
            self.metrics['router_release_error'] = type(exc).__name__ + ': ' + str(exc)

    def _start_prewarm(self):
        """Queue a prewarm of the router now published. Never raises, and never takes the service lock."""
        if not self.prewarm_enabled:
            return
        record = None
        try:
            router = self.router
            if router is None or not self.ready or self._assertion_update_pending:
                return
            with self._prewarm_lock:
                self._prewarm_seq += 1
                record = {'seq': self._prewarm_seq, 'status': 'queued', 'router_revision': router.revision, 'as_of': router.as_of,
                          'queued_utc': utcnow(), 'stages': {}}
                self._publish_prewarm_locked(record['seq'], record)
            threading.Thread(target=self._prewarm, args=(record, router), daemon=True, name='staffer-prewarm').start()
        except Exception as exc:
            if record is not None:
                self._publish_prewarm(record['seq'], {**record, 'status': 'error', 'error': type(exc).__name__ + ': ' + str(exc)})

    def _publish_prewarm(self, seq, record):
        with self._prewarm_lock:
            self._publish_prewarm_locked(seq, record)

    def _publish_prewarm_locked(self, seq, record):
        """Take `record` as the newest prewarm record if it is, and publish metrics['prewarm'], the entry of
        /api/staffer-performance: the newest record and the finished ones before it. It is a fresh dict every time and
        never edited after it is published, so a reader serializing the metrics sees one complete entry."""
        if seq == self._prewarm_seq:
            self._prewarm_latest = {**record, 'stages': {name: dict(stage) for name, stage in record['stages'].items()}}
        latest = self._prewarm_latest
        if latest is not None:
            self.metrics['prewarm'] = {**latest, 'enabled': True,
                                       'earlier': [dict(old) for old in self._prewarm_finished if old['seq'] != latest['seq']]}

    def _prewarm(self, queued, router):
        """Build the stages for `router`, one prewarm at a time, and stop as soon as the router has been replaced.

        A newer router's thread waits on the serial lock until the older thread's current stage ends (a build cannot
        be interrupted), and the older thread then abandons its remaining stages: it checks before each stage. What an
        abandoned prewarm already built belongs to its own router (the work index) or carries its revision (the people
        index), so a replacement never serves it. The service lock is never taken here except inside the stages' own
        brief snapshots.
        """
        seq, record, waiting = queued['seq'], {**queued, 'stages': {}}, time.perf_counter()
        with self._prewarm_serial:
            started = time.perf_counter()
            record.update(status='running', waited_ms=round((started - waiting) * 1000, 2), started_utc=utcnow())
            self._publish_prewarm(seq, record)
            failed = False
            checked = False
            for name in self.PREWARM_STAGES:
                if self.router is not router or self._stop.is_set():
                    record.update(status='abandoned', abandoned_before=name)
                    break
                if not checked:
                    checked = True
                    # The flag is consumed only while `router` is still current, checked under the same lock: a publish swaps
                    # the router before it sets the flag under this lock, so a publish landing after the check above leaves
                    # the flag to the next router's prewarm instead of having this one collect while it still holds `router`.
                    with self._prewarm_lock:
                        current = self.router is router
                        replaced = current and self._router_replaced
                        if current:
                            self._router_replaced = False
                    if not current:
                        record.update(status='abandoned', abandoned_before=name)
                        break
                    if replaced:
                        # One full collection, before this prewarm builds anything, and only because a publish replaced a router since
                        # the last one (a start replaced none: there was nothing to free, and the collection held every request thread
                        # for ten seconds on the live heap). What the router the service replaced left behind (it is only reachable
                        # from itself, so only a collection frees it) is gone before the new indexes are built, so the old indexes and
                        # the new ones are not resident together, unless a request that was already running on the replaced router
                        # still holds them: that request keeps its index until it ends, and no collection frees what it still refers to.
                        mark = time.perf_counter()
                        gc.collect()
                        record['gc_ms'] = round((time.perf_counter() - mark) * 1000, 2)
                mark = time.perf_counter()
                try:
                    stage = {'status': getattr(self, '_prewarm_' + name)(router)}
                except Exception as exc:
                    failed = True
                    stage = {'status': 'error', 'error': type(exc).__name__ + ': ' + str(exc)}
                stage['ms'] = round((time.perf_counter() - mark) * 1000, 2)
                record['stages'][name] = stage
                self._publish_prewarm(seq, record)
            else:
                record['status'] = 'error' if failed else 'done'
                if self.router is not router:
                    record.update(status='abandoned', abandoned_after=self.PREWARM_STAGES[-1])
            record.update(total_ms=round((time.perf_counter() - started) * 1000, 2), finished_utc=utcnow())
            with self._prewarm_lock:
                self._prewarm_finished.append({**record, 'stages': {n: dict(s) for n, s in record['stages'].items()}})
                self._publish_prewarm_locked(seq, record)
        return record

    def _prewarm_work_index(self, router):
        # The options a person gets without touching a control: the Staffer model on payroll and directory history,
        # dated work, no unresolved people (the page's defaults, which are also AdvocacyRouter._options').
        options = router._options({'objective': 'work_time', 'work_sources': 'payroll_and_directory', 'include_historical': True,
                                   'include_review': True, 'include_unresolved': False})
        router.closeness().index(options)
        return 'done'

    def _prewarm_network_index(self, router):
        if not self.ready or self._assertion_update_pending:
            return 'skipped: the service is not ready'
        self.network_index(compressed=False)
        return 'done'

    def network_index(self, compressed=True, scope='sources'):
        """Cache the complete people inventory independently of changing coverage."""
        if not self.ready or self._assertion_update_pending:
            raw = json.dumps({'ready': False, 'stage': self.stage}).encode()
            return (gzip.compress(raw) if compressed else raw), None
        cached = self._explorer_index
        if cached is None or cached['revision'] != self.revision:
            with self._explorer_lock:
                cached = self._explorer_index
                if cached is None or cached['revision'] != self.revision:
                    started = time.perf_counter()
                    with self.lock:
                        revision = self.revision
                        nodes = {**self.static.nodes, **self.profile_nodes}
                        aliases = dict(self.identity_aliases)
                        facts = getattr(self.router, 'person_facts', None)
                        contacts = self.contacts
                        inventory = deepcopy({k: getattr(self, 'inventory', {}).get(k, {})
                                              for k in ('sourcecoverage', 'counts', 'verified_source_links',
                                                        'operational_staffer_ids')})
                    cached = build_index(nodes, aliases, revision, self.source_catalog, self.lda_catalog,
                                         facts, contacts, inventory)
                    # Publish the whole immutable generation at once. A concurrent
                    # source change is picked up on the next request.
                    self._explorer_index = cached
                    self.metrics['network_index_build_ms'] = round((time.perf_counter() - started) * 1000, 2)
                    self.metrics['network_index_bytes'] = len(cached['gzip'])
                    self.metrics['network_index_people'] = cached['payload']['total']
        prefix = 'network_' if scope == 'network' else ''
        return cached[prefix + ('gzip' if compressed else 'raw')], cached[prefix + 'etag']

    def groups_index(self, compressed=True):
        """Offices and organisations, cached beside the people index it derives from."""
        if not self.ready or self._assertion_update_pending:
            raw = json.dumps({'ready': False, 'stage': self.stage}).encode()
            return (gzip.compress(raw) if compressed else raw), None
        cached = self._groups_index
        if cached is None or cached['revision'] != self.revision:
            with self._groups_lock:
                cached = self._groups_index
                if cached is None or cached['revision'] != self.revision:
                    started = time.perf_counter()
                    # The people index is the population contract: a person the
                    # people list dropped can never appear inside a group.
                    self.network_index(compressed=False)
                    index = self._explorer_index
                    if index is None:
                        raw = json.dumps({'ready': False, 'stage': self.stage}).encode()
                        return (gzip.compress(raw) if compressed else raw), None
                    # Take the revision from the index that supplied the people, so
                    # a source change mid-build never stamps a mismatched revision.
                    revision = index['revision']
                    people = index['payload']['people']
                    with self.lock:
                        if revision != self.revision:
                            # A refresh won the interval after the people index was captured.
                            # Defer, rather than joining that population to newer source facts.
                            raw = json.dumps({'ready': False, 'stage': 'groups_source_revision_changed'}).encode()
                            return (gzip.compress(raw) if compressed else raw), None
                        nodes = {**self.static.nodes, **self.profile_nodes}
                        facts = getattr(self.router, 'person_facts', None)
                        catalog = self.source_catalog
                    # The employment ruling reaches the Groups tab here, through the
                    # same fail-open loader the status labels use: without an
                    # organisations artifact on disk this is None and the tab is
                    # exactly what it was before the ruling.
                    cached = build_groups_index(people, nodes, revision, facts, catalog,
                                                lda_employment=self._load_lda_employment())
                    self._groups_index = cached
                    self.metrics['groups_index_build_ms'] = round((time.perf_counter() - started) * 1000, 2)
                    self.metrics['groups_index_bytes'] = len(cached['gzip'])
                    self.metrics['groups_index_counts'] = cached['payload']['counts']
        return cached['gzip' if compressed else 'raw'], cached['etag']

    def _current_metadata(self):
        """Stored poll metadata with the freshness age and stale flag evaluated now, not at the last poll."""
        metadata = self.metadata
        if 'freshness' not in metadata:
            return metadata
        return {**metadata, 'freshness': freshness_now(metadata.get('freshness'))}

    def _model_totals(self, static=None):
        """People-only totals for the pinned model; institution vertices and their links keep their own keys."""
        static = self.static if static is None else static
        counts = static.receipt.get('edge_counts', {})
        institution_links = sum(counts.get(key, 0) for key in INSTITUTION_EDGE_COUNT_KEYS)
        people = static.receipt.get('node_universe', {}).get('person')
        if people is None:
            people = sum(n.get('kind') == 'person' for n in static.nodes.values())
        return {'nodes': people, 'edges': counts.get('total_rows_emitted', 0) - institution_links,
                'scope': 'people and person-person edges only',
                'source_nodes_including_institutions': getattr(static, 'source_node_count', 0) or len(static.nodes),
                'source_edges_including_institution_links': counts.get('total_rows_emitted', 0)}

    def overview_status(self):
        """Small live feed: coverage polling never serializes graph edges."""
        return {**self._current_metadata(), 'ready': self.ready, 'stage': self.stage,
                **self._completion_state(), 'revision': self.revision,
                'generated_at': utcnow(), 'source_coverage': self.inventory.get('sourcecoverage', {}),
                'identity_resolution': self.identity_progress, 'errors': self.errors,
                # Surfaced separately so 'Verified model unavailable' always
                # carries the reason it is unavailable, whatever else the rolling
                # error list has been replaced with since.
                'startup_error': self.startup_error,
                'inventory': {'searchable_person_nodes': self.metrics.get('searchable_person_nodes')},
                'totals': self._model_totals()}

    def source_record(self, ident, offset=0):
        if self.source_catalog:
            payload = self.source_catalog.record(ident, offset)
            if payload.get('record'):
                payload['node']['contacts'] = self.contacts.get(ident)
                return payload
        contact_record = self.contacts.source_record(ident)
        if contact_record:
            return contact_record
        with self.lock:
            node = self.static.nodes.get(ident)
            if node and node.get('kind') == 'payroll_record':
                return {'status': 'unresolved_source_record', 'node': {**node, 'source_system': 'Payroll'},
                        'record': node, 'relationships': [], 'total': 0, 'offset': 0, 'limit': 50,
                        'has_more': False, 'provenance': node.get('provenance'),
                        'note': 'Unresolved payroll observation. This source row does not assert a distinct staffer identity.'}
            context = graph_context_record(self.static, ident, offset)
            if context.get('record'):
                return context
        if self.lda_catalog:
            return self.lda_catalog.record(ident, offset)
        return context

    def staffer_person_link(self, source_id):
        """Resolve one directory source row through the existing guarded match."""
        with self.lock:
            result = self.contacts.resolve_source_person(source_id)
            bound_id = self._resolve_id(source_id)
            bound_node = self.router.nodes.get(bound_id) if self.router else None
            # A source-native LDA id gains the ``person:`` prefix when it enters
            # the ecosystem graph.  If that exact node is already present, it is
            # itself the routable identity even though no alias rewrite occurs.
            lda_node_identity = (str(source_id).startswith('person:lda:lobbyist:')
                                 and bound_node
                                 and bound_node.get('kind', bound_node.get('node_kind')) == 'person')
            reconciled = bound_id != source_id or bool(lda_node_identity)
            person_id = bound_id if reconciled else result.get('person_id')
            if person_id:
                person_id = self._resolve_id(person_id)
                node = self.router.nodes.get(person_id) if self.router else None
                if (not node or node.get('kind', node.get('node_kind')) != 'person'
                        or person_id in getattr(getattr(self.router, 'person_facts', None), 'network_excluded', ())):
                    person_id = None
            return {**result, 'person_id': person_id,
                    'route_available': person_id is not None,
                    'match_basis': ('Accepted source identity binding; contact availability is evaluated separately'
                                    if reconciled and person_id else result.get('match_basis')),
                    'status': ('matched_source_identity' if reconciled and person_id else
                               'matched_current_office' if person_id else
                               'unknown_source' if result.get('status') == 'unknown_source'
                               else 'source_only')}

    def _completion_state(self, static=None):
        """Separate service readiness, source collection and model verification."""
        static = self.static if static is None else static
        metadata = self._current_metadata()
        collection = metadata.get('coverage', {}).get('completion_status') or metadata.get('freshness', {}).get('collection_status', 'INCOMPLETE')
        graph = static.verification.get('status', 'UNVERIFIED')
        errors = self.errors or metadata.get('errors') or static.errors
        ready = self.ready and not self._assertion_update_pending
        status = 'IN_PROGRESS'
        if ready:
            status = 'READY_WITH_ERRORS' if errors else 'READY_EXPLORATORY'
        return {'status': status, 'terminal_verified': False,
                'graph_verification_status': graph, 'collection_completion_status': collection,
                'verification_scope': 'Graph artifact integrity and numerical checks; source exceptions and review-only identities retain their own status.'}

    def full_network(self):
        """Return the complete verified Politico people graph.

        The ordinary map is intentionally a bounded neighborhood.  This view is
        the full profile inventory instead: every eligible verified profile is
        present, with review-only edges for exact shared current employer or
        historical employer strings.  Policy and committee affiliations remain
        attached as node metadata and are not silently converted into person
        ties.  The payload is cached by the receipt-bound profile revision.
        """
        # Do not wait behind the long-running identity overlay lock.  The
        # profile dictionary is replaced atomically by the refresh worker; a
        # shallow snapshot here is safe and keeps the full-network endpoint
        # responsive while reconciliation continues.
        if not self.ready or self._assertion_update_pending:
            return {'ready': False, 'stage': self.stage, 'status': 'IN_PROGRESS',
                    'terminal_verified': False, 'revision': 'loading', 'nodes': [], 'edges': [],
                    'errors': list(self.errors)}
        revision = (self._profile_revision, self.revision)
        # The cache survives polls now, so a poll can drop it between two reads here: take both once, revision first.
        # The writer below stores the payload before the revision, so a matching revision always has its payload.
        cached_revision, payload = self._full_network_revision, self._full_network_payload
        if payload is not None and cached_revision == revision:
            # The payload is built once per revision, but each answer is stamped when it is served, as when every poll
            # rebuilt it: the cached copy carries the time it was built, not the time of the request.
            return {**payload, **self._current_metadata(), **self._completion_state(), 'generated_at': utcnow()}
        excluded = getattr(getattr(self.router, 'person_facts', None), 'network_excluded', ())
        people = [dict(node) for node in list(self.profile_nodes.values())
                  if node.get('kind') == 'person' and node['id'] not in excluded]
        metadata = deepcopy(self.metadata)
        source_revision = self.revision

        def normalize(value):
            return re.sub(r'\s+', ' ', str(value or '').casefold()).strip()

        full_node_fields = ('id', 'label', 'kind', 'name_status', 'review_status', 'identity_tier',
                            'canonical_eligible', 'source', 'verification', 'source_profile_uuid',
                            'source_summary', 'last_reported_at', 'member_ids', 'committee_ids',
                            'policy_ids', 'identity_status', 'identity_basis', 'source_only_identity',
                            'routing_only', 'profile_status', 'profile_pending', 'lda_source_person_id')
        nodes = [{key: node[key] for key in full_node_fields if key in node}
                 for node in sorted(people, key=lambda value: (str(value.get('label', '')).casefold(), value.get('id', '')))]
        groups = defaultdict(set)
        labels = {}
        for node in people:
            person = node['id']
            for position in node.get('current_positions', []) or []:
                text = str(position)
                employer = re.split(r' for ', text, flags=re.I)[-1] if ' for ' in text.casefold() else text
                key = ('shared_current_employer', normalize(employer))
                if key[1]:
                    groups[key].add(person)
                    labels[key] = employer
            for stint in node.get('employment_history', []) or []:
                employer = stint.get('employer') if isinstance(stint, dict) else None
                key = ('shared_historical_employer', normalize(employer))
                if key[1]:
                    groups[key].add(person)
                    labels[key] = employer

        # Prefer one edge per pair, retaining the strongest current evidence.
        edge_by_pair = {}
        large_groups_skipped = 0
        rank = {'shared_current_employer': 0, 'shared_historical_employer': 1}
        for (layer, group), members in groups.items():
            # A generic source label can otherwise create a quadratic burst.
            # Keep the complete people inventory and affiliation metadata, but
            # leave very large groups for on-demand person inspection.
            if len(members) > 250:
                large_groups_skipped += 1
                continue
            ordered = sorted(members)
            for index, source in enumerate(ordered):
                for target in ordered[index + 1:]:
                    pair = (source, target)
                    candidate = (rank[layer], layer, group)
                    prior = edge_by_pair.get(pair)
                    if prior is not None and prior['_rank'] <= candidate[0]:
                        continue
                    edge_by_pair[pair] = {'_rank': candidate[0], 'layer': layer, 'group': group,
                                          'label': labels.get((layer, group), group)}
        edges = []
        for (source, target), value in sorted(edge_by_pair.items()):
            layer = value['layer']
            edges.append({'id': 'politico-full:' + digest([source, target, layer, value['group']])[:24],
                          'source': source, 'target': target, 'layer': layer,
                          'relationship': layer, 'weight': 0, 'routing_eligible': False,
                          'temporal_overlap_established': False,
                          'employer_label': value['label'], 'review_status': 'REVIEW_REQUIRED',
                          'evidence_class': 'source_profile_shared_employer',
                          'canonical_eligible': False, 'operator_included': False,
                          'provenance_available_on_selection': True})
        payload = {**metadata, 'schema': 'melange-staffer-full-network-v1', 'ready': True,
                   'stage': 'Ready', **self._completion_state(),
                   'generated_at': utcnow(), 'revision': digest([source_revision, revision, len(nodes), len(edges)])[:20],
                   'source_revision': source_revision, 'nodes': nodes, 'edges': edges,
                   'totals': {'nodes': len(nodes), 'edges': len(edges)},
                   'layers': [{'id': 'shared_current_employer', 'label': 'Same employer in source current-position fields', 'kind': 'evidence', 'default_enabled': True},
                              {'id': 'shared_historical_employer', 'label': 'Same employer in source history; overlap unconfirmed', 'kind': 'evidence', 'default_enabled': True}],
                   'bounds': {'node_limit': len(nodes), 'edge_limit': len(edges), 'queried_people': len(nodes),
                              'search_limited': bool(large_groups_skipped), 'people_only': True,
                              'complete_people_inventory': True, 'complete_network': False,
                              'selection_method': 'All receipt-verified eligible Politico profiles; exact shared source employer groups',
                              'policy_edges': 'Retained as node policy_ids; not converted into person ties'},
                   'full_network': {'nodes_complete': True, 'edges_complete_for_shared_employer_layers': not bool(large_groups_skipped),
                                    'profile_nodes': len(nodes), 'shared_employer_edges': len(edges),
                                    'affiliation_groups': len(groups), 'large_groups_skipped': large_groups_skipped,
                                    'source_revision': source_revision}}
        if (self._profile_revision, self.revision) == revision:
            self._full_network_payload = payload
            self._full_network_revision = revision
        return payload

    def search(self, query, limit=20):
        """People matching a typed name, best first. Reads the one published view and takes no service lock."""
        if not self.ready or self._assertion_update_pending:
            return {'nodes': [], 'ready': False, 'stage': self.stage}
        view = self._current_view()
        query = resolve_id(query, view.static.nodes, view.aliases, view.activity_provider)
        key = (view.revision, query, limit)
        with self._search_lock:
            cached = self.search_cache.get(key)
        if cached is not None:
            return cached
        nodes = search_people(view.static, view.profile_nodes, view.index, view.excluded, query, limit)
        result = {'nodes': [compact_node(n) for n in nodes], 'ready': True, 'revision': view.revision}
        with self._search_lock:
            bounded_put(self.search_cache, key, result, 128)
        return result

    def _graph(self, query, limit, view=None):
        view = view or self._current_view()
        query = resolve_id(query, view.static.nodes, view.aliases, view.activity_provider)
        limit = max(1, min(int(limit), NODE_LIMIT))  # one projection per effective node limit
        key = (view.revision, query, limit)
        with self._view_cache_lock:
            if key in self.view_cache:
                return self.view_cache[key]
        base = project_people(view.static, view.router, query=query, limit=limit,
                              profile_nodes=view.profile_nodes, identity_aliases=view.aliases)
        # Keep affiliation identifiers on people as grouping metadata, never as vertices.
        nodes = []
        for node in base['nodes']:
            if node['id'] in view.excluded:
                continue
            full = view.profile_nodes.get(node['id']) or view.static.nodes.get(node['id'], {})
            nodes.append({**compact_node(full), **node})
        present = {n['id'] for n in nodes}
        base = {**base, 'edges': [e for e in base.get('edges', [])
                                 if e['source'] in present and e['target'] in present]}
        # The revision names the projected content, so a truncated map never shares one with the complete map.
        result = {**base, 'nodes': nodes, 'revision': digest([view.revision, query, limit, 'people-v1',
                      [n.get('id') for n in nodes], [e.get('id') for e in base.get('edges', [])]])[:20],
                  'totals': self._model_totals(view.static),
                  'verification_status': view.static.verification.get('status')}
        if base.get('bounds', {}).get('time_limited'):
            return result  # a wall-clock cut is transient; the next request projects again instead of pinning it
        with self._view_cache_lock:
            return bounded_put(self.view_cache, key, result)

    def snapshot(self, query='', limit=700, since=None):
        return self._snapshot(query, limit, since)

    def _snapshot(self, query='', limit=700, since=None):
        started = time.perf_counter()
        if not self.ready or self._assertion_update_pending:
            return {'schema': 'melange-staffer-live-v2', 'ready': False, 'stage': self.stage,
                    'status': 'IN_PROGRESS', 'terminal_verified': False, 'revision': 'loading',
                    'nodes': [], 'edges': [], 'errors': self.errors, 'generated_at': utcnow()}
        # One short hold of the service lock takes the view together with every field of the response that other threads
        # update in place (metadata, source coverage, identity progress and review, inventory, evidence sources, errors,
        # metrics), so the response describes one moment: none of those can be a refresh newer than the view that is
        # projected. The projection, the slow part, then reads the captured view and holds no lock.
        with self.lock:
            view = self._current_view()
            live = self._snapshot_state(view)
        graph = self._graph(query, limit, view)
        metadata, current = live['metadata'], live['current']
        result = {**graph, **current, 'schema': 'melange-staffer-live-v2', 'ready': True, 'stage': live['stage'],
                  'source_revision': view.revision,
                  **live['completion'], 'generated_at': utcnow(),
                  'source_coverage': live['source_coverage'],
                  'identity_aliases': view.aliases,
                  'identity_review': live['identity_review'],
                  'identity_resolution': live['identity_progress'],
                  'identity_review_errors': live['identity_review_errors'],
                  'inventory': live['inventory'],
                  'errors': graph.get('errors', []) + live['errors'] + metadata.get('errors', [])
                            + ['Identity review: ' + str(e) for e in live['identity_review_errors']],
                  'sources': live['sources'],
                  'routing': live['routing'],
                  'people_only_notice': graph.get('notice'),
                  'notice': ' '.join(filter(None, [graph.get('notice'), 'Exploratory introduction routes use dated evidence and do not establish familiarity, access, or influence.']))}
        result['metadata_revision'] = live['metadata_revision']
        result['unchanged'] = since == result['revision']
        if result['unchanged']:
            result['nodes'], result['edges'] = [], []
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        self.metrics['snapshot_ms'] = elapsed
        result['performance'] = {**live['metrics'], 'snapshot_ms': elapsed}
        return result

    def _snapshot_state(self, view):
        """What a snapshot reports from the live service, copied where other threads update it in place.

        Call with self.lock held, in the same hold that took `view`: the fields are then the service's state at the
        moment the view was taken, not at the moment the projection finished. One gap remains, in the refresh and not
        here: a refresh that rebuilds the router publishes its view in one lock hold and the poll's metadata in the
        next, so a snapshot taken between the two pairs the new view with the previous poll's metadata.
        """
        metadata, current, stage = self.metadata, self._current_metadata(), self.stage
        errors, identity_progress = list(self.errors), deepcopy(self.identity_progress)
        identity_errors = list(self.identity_resolution.get('errors', []))
        return {'metadata': metadata, 'current': current, 'stage': stage,
                'completion': self._completion_state(view.static),
                'source_coverage': deepcopy(self.inventory.get('sourcecoverage', {})),
                'identity_review': deepcopy(self.identity_resolution.get('counts', {})),
                'identity_progress': identity_progress,
                'identity_review_errors': identity_errors,
                'inventory': {'counts': deepcopy(self.inventory.get('counts', {})),
                              'headcount_semantics': self.inventory.get('headcount_semantics'),
                              'searchable_person_nodes': self.metrics.get('searchable_person_nodes')},
                'errors': errors,
                'sources': view.static.sources + self.evidence.get('sources', []) + self.inventory.get('sources', []) + [{'label': 'Politico crawl verification',
                    'path': str(self.profiles.raw_dir / 'crawl_verification.json'), 'sha256': self.profiles.accepted_receipt_digest}],
                'routing': {'algorithm': 'Bidirectional Dijkstra', 'heuristic': 0, 'as_of': view.router.as_of,
                            'lda_activity_index': view.activity_provider.receipt if view.activity_provider else None,
                            'ambiguous_identity_links_kept_separate': len(set(self.evidence_exceptions)),
                            'cost_overlay_only': True, 'named_examples': {k: {f: v.get(f) for f in ('person_id', 'lda_source_person_id', 'current_employment_verified', 'identity_note')}
                                                                         for k, v in self.evidence.get('named_examples', {}).items()}},
                'metadata_revision': digest([metadata, errors, stage, identity_progress.get('politico_ingestion'),
                                             current.get('freshness', {}).get('stale')])[:16],
                'metrics': dict(self.metrics)}

    def encoded_snapshot(self, query='', limit=700, since=None, compressed=True):
        # A metadata refresh clears this cache; stale/fresh states still update.
        stale = self._current_metadata().get('freshness', {}).get('stale')  # flips on the clock, not on a poll
        key = (self.revision, self.metadata_epoch, query, limit, since, compressed, self.ready, self.stage, stale)
        with self.lock:
            cached = self.bytes_cache.get(key)
        if cached is not None:
            return cached
        started = time.perf_counter()
        payload = self.snapshot(query, limit, since)
        raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
        body = gzip.compress(raw, compresslevel=3) if compressed else raw
        etag = '"' + hashlib.sha256(body).hexdigest()[:24] + '"'
        self.metrics['serialize_compress_ms'] = round((time.perf_counter() - started) * 1000, 2)
        self.metrics['snapshot_bytes'] = len(body)
        self.metrics['snapshot_uncompressed_bytes'] = len(raw)
        result = body, etag
        if payload.get('bounds', {}).get('time_limited'):
            return result
        with self.lock:
            return bounded_put(self.bytes_cache, key, result)

    def person(self, node_id, options=None):
        with self.lock:
            if not self.ready or self._assertion_update_pending:
                return {'ready': False, 'stage': self.stage}
            node_id = self._resolve_id(node_id)
            router = self.router
            node = self.profile_nodes.get(node_id) or self.static.nodes.get(node_id)
            profile_links = self.profile_by_person.get(node_id, [])
            links = [self.static.edges[i] for i in self.static.adjacency.get(node_id, [])]
            groups = [{**group, 'records': [
                {**record, 'source_summary': self.static.nodes.get(self._resolve_id(record['node_id']), {}).get('source_summary')}
                for record in group['records']]}
                for group in self.candidates_by_person.get(node_id, [])[:3]]
            assertions = self.evidence.get('user_assertions', [])
        if (not node or node.get('kind', node.get('node_kind')) != 'person'
                or node_id in getattr(getattr(router, 'person_facts', None), 'network_excluded', ())):
            # Institutions, activities and routing hubs are evidence, never people: refuse them as search does.
            return {'ready': True, 'status': 'unknown_person', 'node': None, 'coworkers': []}
        node = {**node, 'identity_candidates': groups,
                'person_facts': getattr(router, 'person_facts', None).people.get(node_id)
                if getattr(router, 'person_facts', None) else None}
        if node.get('stints'):
            node['stints'] = [{**stint, 'office_name': self.static.nodes.get(stint.get('container_id'), {}).get('label')}
                             for stint in node['stints']]
        options = dict(options or {})
        coworkers = router.coworkers(node_id, {**options, 'offset': options.get('coworker_offset', 0)})
        collaborators = router.reported_collaborators(node_id, {**options, 'offset': options.get('collaborator_offset', 0)})
        result = []
        for hop in coworkers.get('coworkers', []):
            other = router.nodes.get(hop['person_id'], {})
            result.append({**hop, 'node': compact_node(other), 'edge': hop,
                           'current': hop.get('temporal_status') == 'current'})
        links += profile_links
        affiliations = [edge for edge in links
                        if any(router.nodes.get(edge[key], {}).get('kind') != 'person' for key in ('source', 'target'))]
        node['affiliation_evidence'] = affiliations
        excluded = getattr(getattr(router, 'person_facts', None), 'network_excluded', ())
        links = [edge for edge in links if edge not in affiliations
                 and edge['source'] not in excluded and edge['target'] not in excluded][:100]
        reported = []
        for hop in collaborators.get('collaborators', []):
            reported.append({**hop, 'node': compact_node(router.nodes.get(hop['person_id'], {})),
                             'edge': hop, 'current': False})
        for hop in [*coworkers.get('coworkers', []), *collaborators.get('collaborators', [])]:
            links.append({**hop, 'layer': hop['relationship'],
                          'id': 'people:' + digest([hop['source'], hop['target'], hop.get('employer_id'),
                                                   hop.get('activity_id'), hop['relationship']])[:24]})
        return {'ready': True, 'node': node, 'edges': links, 'coworkers': result, 'reported_collaborators': reported,
                'pagination': {'coworkers': {k: coworkers.get(k) for k in ('offset', 'limit', 'total', 'has_more', 'search_limited')},
                               'reported_collaborators': {k: collaborators.get(k) for k in ('offset', 'limit', 'total', 'has_more', 'search_limited')}},
                'reported_collaborator_total': collaborators.get('total', 0),
                'reported_collaborators_truncated': collaborators.get('truncated', False),
                'reported_collaborators_search_limited': collaborators.get('search_limited', False),
                'coworker_status': coworkers.get('status'), 'coworker_total': coworkers.get('total', 0),
                'total_coworkers': coworkers.get('total', 0), 'truncated': coworkers.get('truncated', False),
                'search_limited': coworkers.get('search_limited'), 'revision': router.revision,
                'as_of': router.as_of, 'user_assertions': assertions}

    def start_route_search(self, origin, target, options=None, office=False, supersedes=None, holder=None):
        options = {'objective': 'overlap_balanced', 'include_policy': True, **(options or {})}
        requested = AdvocacyRouter._options(options)['route_count']  # Validate before enqueueing.
        holder = route_holder(holder)
        with self.lock:
            key = digest([self.revision, origin, target, options, office])[:32]
            if supersedes and supersedes != key:
                # The caller is replacing that search, so it lets go of it; a search another page still holds keeps
                # running. An identical request keeps its own job.
                self._release_route_holder(supersedes, holder, 'superseded')
            if not self.ready or self._assertion_update_pending:
                return {'ready': False, 'status': 'loading', 'stage': self.stage}
            job = self.route_jobs.get(key)
            if job is not None and job['cancelled']:
                # A cancelled search is never reused: the same request is computed again.
                del self.route_jobs[key]
                job = None
            reused = job is not None
            if not reused:
                # Completed results can be reclaimed; active jobs finish unless cancelled.
                for old in list(self.route_jobs):
                    if len(self.route_jobs) < 64:
                        break
                    if self.route_jobs[old]['future'].done():
                        del self.route_jobs[old]
                self.route_jobs[key] = self._submit_route_job(office, origin, target, options, requested)
            self.route_jobs[key]['holders'].add(holder)  # a repeat from the same page changes nothing
        result = self.route_search_result(key)
        if reused and result.get('optimal'):
            result = {**result, 'cache_hit': True}
        return result

    def _submit_route_job(self, office, origin, target, options, requested):
        """Queue one search with its own cancel event. Call with self.lock held. The caller adds the first holder."""
        progress = {'expanded': 0, 'neighbor_checks': 0, 'elapsed_ms': 0}
        job = {'progress': progress, 'cancel': threading.Event(), 'cancelled': None,
               'polled': self.route_clock(), 'partial': [], 'requested': requested, 'holders': set()}
        def publish(value):
            value = dict(value)
            route = value.pop('partial_route', None)
            with self.lock:
                progress.update(value)
                if route is not None:
                    job['partial'].append(route)
        function = self.office_route if office else self.route
        job['future'] = self.route_executor.submit(self._run_route_job, job, function, origin, target, options, publish)
        self._ensure_route_reaper()
        return job

    @staticmethod
    def _run_route_job(job, function, origin, target, options, publish):
        try:
            with cancel_scope(job['cancel']):
                check_cancelled()  # A search cancelled while it waited for a worker never starts.
                return function(origin, target, options, publish)
        finally:
            # The finished payload carries every route; the partial copies are no longer needed.
            job['partial'] = []

    def route_search_result(self, key):
        with self.lock:
            job = self.route_jobs.get(key)
            if job is None:
                return {'ready': True, 'status': 'search_not_found', 'search_id': key}
            # Asking about a search is what keeps it alive.
            job['polled'] = self.route_clock()
            if job['cancelled']:
                return {'ready': True, 'status': 'cancelled', 'search_id': key, 'cancel_reason': job['cancelled']}
            if not job['future'].done():
                response = {'ready': True, 'status': 'searching' if job['future'].running() else 'queued',
                            'search_id': key, 'progress': dict(job['progress']),
                            'routes_requested': job['requested']}
                if job['partial']:
                    response['partial_routes'] = list(job['partial'])
                return response
            try:
                result = job['future'].result()
            except RouteCancelled:
                return {'ready': True, 'status': 'cancelled', 'search_id': key, 'cancel_reason': 'requested'}
            except Exception as exc:
                return {'ready': True, 'status': 'search_error', 'search_id': key, 'error': str(exc)}
            return {**result, 'search_id': key}

    def cancel_route_search(self, key, holder=None):
        """One page lets go of a search. It is stopped when no page still holds it; a finished search keeps its result.

        Answers cancelled, released (other pages still hold it, so it keeps running), finished or search_not_found.
        """
        holder = route_holder(holder)
        with self.lock:
            return {'ready': True, 'search_id': key, 'status': self._release_route_holder(key, holder, 'requested')}

    def _release_route_holder(self, key, holder, reason):
        """Drop one holder's claim on a job, and cancel the job only when no holder is left. Call with self.lock held.

        Returns cancelled, released, finished or search_not_found. A holder that never held the job, or lets go twice,
        changes nothing. Only the last holder to leave cancels, so `reason` is the reason that holder gave.
        """
        job = self.route_jobs.get(key)
        if job is None:
            return 'search_not_found'
        if job['cancelled']:
            return 'cancelled'
        if job['future'].done():
            return 'finished'
        job['holders'].discard(holder)
        if job['holders']:
            return 'released'
        return self._cancel_route_job(key, reason)

    def _cancel_route_job(self, key, reason):
        """Flag one job cancelled and tell its worker. Call with self.lock held.

        Returns cancelled, finished or search_not_found. Setting an event and
        cancelling a queued future are the only work done here.
        """
        job = self.route_jobs.get(key)
        if job is None:
            return 'search_not_found'
        if job['cancelled']:
            return 'cancelled'
        if job['future'].done():
            return 'finished'
        job['cancelled'] = reason
        job['cancel'].set()
        job['future'].cancel()  # A search still waiting for a worker never starts.
        return 'cancelled'

    def reap_abandoned_route_jobs(self):
        """Cancel searches nobody has asked about for ROUTE_ABANDON_SECONDS; returns their ids."""
        with self.lock:
            return self._reap_route_jobs()

    def _reap_route_jobs(self):
        now, reaped = self.route_clock(), []
        for key, job in self.route_jobs.items():
            if not job['cancelled'] and not job['future'].done() and now - job['polled'] >= ROUTE_ABANDON_SECONDS:
                self._cancel_route_job(key, 'abandoned')
                reaped.append(key)
        return reaped

    def _ensure_route_reaper(self):
        """Start the abandonment sweeper if it is not running. Call with self.lock held."""
        thread = self._route_reaper
        if thread is None or not thread.is_alive():
            thread = self._route_reaper = threading.Thread(
                target=self._reap_route_jobs_loop, daemon=True, name='staffer-route-reaper')
            thread.start()

    def _reap_route_jobs_loop(self):
        while not self._stop.wait(self.route_reap_interval):
            with self.lock:
                self._reap_route_jobs()
                if all(job['future'].done() for job in self.route_jobs.values()):
                    # Nothing left to watch; the next submission starts a new sweeper.
                    self._route_reaper = None
                    return

    def _partial_progress(self, router, started, progress, finish=None):
        """Wrap a job's progress callback so each route distinct_routes accepts also
        arrives in the shape of a finished route. Without a callback nothing changes."""
        if progress is None:
            return None
        def publish(value):
            route = value.get('accepted_route')
            if route is not None:
                value = {key: item for key, item in value.items() if key != 'accepted_route'}
                shaped = self._route_response(router, route, started)
                value['partial_route'] = finish(shaped) if finish else shaped
            progress(value)
        return publish

    def offices(self, query, limit=20):
        """Offices matching a typed name. Reads the one published view and takes no service lock."""
        if not self.ready or self._assertion_update_pending:
            return {'ready': False, 'nodes': [], 'stage': self.stage}
        view = self._current_view()
        return {'ready': True, 'revision': view.router.revision,
                'nodes': search_offices(view.office_index, query, max(1, min(int(limit), 100)))}

    def office_route(self, origin, office, options=None, progress=None):
        started = time.perf_counter()
        with self.lock:
            if not self.ready or self._assertion_update_pending:
                return {'ready': False, 'found': False, 'status': 'loading', 'stage': self.stage}
            router = self.router
            origin = self._resolve_id(origin)
        destination = getattr(router, 'office_index', {}).get(office)
        if destination is None:
            return {'ready': True, 'found': False, 'status': 'unknown_office', 'office_id': office}
        options = {'objective': 'overlap_balanced', 'include_policy': True, **(options or {})}
        checked = router._options(options)
        candidates = sorted(p for p in destination['roster'] if router._person_allowed(p, checked)
                            and ((destination['roster'][p].get('basis') == 'current_profile' and checked['include_review'])
                                 or any(e['employer_id'] == office and router._allowed(e, checked)
                                        for e in router.by_person.get(p, []))))
        office_info = {k: v for k, v in destination.items() if k != 'roster'}
        if not candidates:
            return {'ready': True, 'found': False, 'status': 'no_office_staff', 'office': office_info,
                    'candidate_count': 0, 'message': 'No staff in the latest office records match these settings.'}
        def with_office(route):
            route.update(office=office_info, candidate_count=len(candidates),
                         recommended_contact=destination['roster'].get(route.get('target')))
            return route
        result = router.route_many(origin, candidates, {**options, '_target_office': office},
                                   self._partial_progress(router, started, progress, with_office))
        response = self._route_response(router, result, started)
        response.update(office=office_info, candidate_count=len(candidates),
                        recommended_contact=destination['roster'].get(result.get('target')))
        for route in response.get('routes', []):
            with_office(route)
        return response

    def route(self, origin, target, options=None, progress=None):
        started = time.perf_counter()
        with self.lock:
            if not self.ready or self._assertion_update_pending:
                return {'ready': False, 'found': False, 'status': 'loading', 'stage': self.stage}
            origin, target = self._resolve_id(origin), self._resolve_id(target)
            router = self.router
        if self.identity_web and hasattr(self.identity_web, 'prioritize_people'):
            self.identity_web.prioritize_people({i: router.nodes[i] for i in (origin, target) if i in router.nodes})
        result = router.route(origin, target, {"objective": "overlap_balanced", 'include_policy': True, **(options or {})},
                              self._partial_progress(router, started, progress))
        return self._route_response(router, result, started)

    def _route_response(self, router, result, started):
        if 'routes' in result:
            result = {**result, 'routes': [self._route_response(router, route, started)
                                          for route in result['routes']]}
        hops = []
        for hop in result.get('hops', []):
            hops.append({**hop, 'source_label': router.nodes[hop['source']]['label'],
                         'target_label': router.nodes[hop['target']]['label'],
                         'layer': 'advocacy_route', 'id': 'route:' + digest([hop['source'], hop['target'], hop.get('employer_id'), hop['relationship']])[:20]})
        nodes = [compact_node(router.nodes[i]) for i in result.get('node_ids', [])]
        warnings = ['Suggested evidence-based introduction chain; no guarantee of personal familiarity, access, or influence.']
        background_hops = sum(h.get('evidence_class') == 'shared_background_only' for h in hops)
        if background_hops:
            warnings.append(f'{background_hops} step(s) share background only; working together is not established for those steps.')
        if any(h.get('user_asserted') for h in hops):
            warnings.append('Uses your stated current coworker relationship; this current status is not independently verified.')
        if any(h.get('weak') for h in hops):
            warnings.append('Includes weak/shared-filing evidence; employment overlap is not established for those hops.')
        if any(n.get('identity_status') == 'USER_CONFIRMED_SAME_PERSON' for n in nodes):
            warnings.append('Uses an identity match confirmed by you; its original source records remain attached.')
        bases = {str(n.get('identity_basis') or 'REFERENCE_RESOLVED') for n in nodes if n.get('identity_status') == 'REFERENCE_RESOLVED_SAME_PERSON'}
        if any('RECORD' in basis for basis in bases):
            warnings.append('Uses an identity match supported by verified source records; original source records remain attached.')
        if any('REFERENCE' in basis for basis in bases):
            warnings.append('Uses an identity match supported by references; original source records and citations remain attached.')
        explanation = ('Search runs from both ends. Fewest steps comes first, then connection preferences.'
                       if result.get('objective') == 'fewest_steps' else
                       'Search runs from both ends and minimizes connection costs, then steps. Map positions do not affect routes.')
        if result.get('objective') == 'work_time':
            source_label = 'directory employment only' if result.get('work_sources') == 'directory' else 'payroll and directory employment intervals'
            explanation = (f'Ranks complete paths using {source_label}, concurrent staff size and divided work allocation. Payroll report windows can supply modeled exposure when enabled; trajectory titles remain stint summaries. '
                'Each extra introduction reduces route strength. Office recommendations also use direct office tenure and '
                'a member-network adjustment capped at 15%. Unverified access, older overlap and dependence on one workplace '
                'reduce scores. These penalties are assumptions, not measured chances of success or inferred dislike.')
        elif result.get('objective') == 'overlap_balanced':
            explanation = ('Balances shared-background scores with fewer steps. Each extra step adds one to the total score distance. The lowest combined score wins; fewer steps break ties. Scores are heuristic preferences, not measurements of familiarity or access.')
        elif result.get('objective') in ('overlap_strength', 'overlap_fewest_steps'):
            explanation = ('Fewest steps first, then shared-background scores.' if result['objective'] == 'overlap_fewest_steps' else
                'Minimizes distance derived from shared-background scores; fewer steps break ties. Scores do not measure personal familiarity or access.')
        # A Staffer answer that found no route says why (unreachable_reason, message): its explanation is that message, the text the page shows
        # for it, so no consumer is handed the generic sentence beside a reason it contradicts. An answer with no reason keeps the generic one.
        reasoned = result['status'] == 'unreachable' and bool(result.get('unreachable_reason') and result.get('message'))
        if result['status'] == 'unknown_person':
            explanation = 'No search was run: only known people with a resolved name can be route endpoints; institutions, activities and routing hubs are evidence, not endpoints.'
        elif result['status'] == 'search_limited':
            explanation = 'Search reached its effort limit; this does not establish that no route exists. No conclusion about reachability was reached.'
        elif result['status'] == 'unreachable':
            explanation = (result['message'] if reasoned else
                           'No supported path exists in the currently indexed evidence under these filters. This does not establish that no real-world introduction is possible.')
        elif result['status'] == 'insufficient_work_coverage':
            explanation = result['message']
        if not reasoned and result.get('alternative_strategy') in ('distinct', 'independent'):
            explanation += ' Alternatives are found by excluding contacts used in earlier choices, including records with the same displayed name. Each is best under those exclusions; the set is not globally optimized.'
            if result['alternative_strategy'] == 'independent':
                explanation += ' Connections whose selected evidence repeats a workplace or organization are also excluded; missing context identifiers cannot establish independence.'
        return {**result, 'ready': True, 'found': result['status'] == 'found', 'path': result.get('node_ids', []),
                'graph': {'nodes': nodes, 'edges': hops},
                'nodes': nodes, 'edges': hops, 'hops': hops, 'warnings': warnings,
                'explanation': explanation,
                'elapsed_ms': round((time.perf_counter() - started) * 1000, 2)}
