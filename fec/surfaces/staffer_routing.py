"""Bounded introduction routing over exact-identity, dated employment evidence.

Costs are a UI routing overlay; no source or canonical graph weight is modified.
Employer hubs index possible neighbors but are never traversable route nodes.
"""
from __future__ import annotations

from collections import defaultdict, OrderedDict
from copy import deepcopy
from datetime import date, timedelta
import heapq
import itertools
import json
import math
import threading
import time

try:
    from .staffer_activity_index import ActivitySearchLimited
    from .staffer_payroll_identity import employment_overlap
    from .staffer_route_cancel import check_cancelled
except ImportError:  # direct script execution: no package context
    import os as _bootstrap_os, sys as _bootstrap_sys
    _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
    if _bootstrap_root not in _bootstrap_sys.path:
        _bootstrap_sys.path.insert(0, _bootstrap_root)
    from fec.surfaces.staffer_activity_index import ActivitySearchLimited
    from fec.surfaces.staffer_payroll_identity import employment_overlap
    from fec.surfaces.staffer_route_cancel import check_cancelled


VERSION = 'employment-routing-v7-overlap-evidence-audit'
_CLOSENESS_LOCK = threading.Lock()


class _Limit(Exception):
    pass


def _date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


class AdvocacyRouter:
    def __init__(self, nodes: dict, episodes: list, revision: str, as_of: str, activity_provider=None, clock=None):
        self._clock = clock or time.monotonic
        self.nodes = nodes
        self.revision = str(revision)
        self.as_of = str(as_of)
        self.activity_provider = activity_provider
        if _date(self.as_of) is None:
            raise ValueError("as_of must be an ISO date")
        self.by_person = defaultdict(list)
        self.by_employer = defaultdict(list)
        self.filing_by_person = defaultdict(list)
        self.filing_by_employer_year = defaultdict(list)
        self.activity_by_person = defaultdict(list)
        self.activity_by_hub = defaultdict(list)
        self.policy_people = defaultdict(list)
        self.rejected_episodes = 0
        self._cache = OrderedDict()
        route_as_of = _date(self.as_of)
        standing = defaultdict(list)
        for node_id, node in nodes.items():
            if node.get("kind", node.get("node_kind")) == "person":
                for policy in node.get("policy_ids", []):
                    self.policy_people[policy].append(node_id)
        for original in episodes:
            episode = dict(original)
            person = episode.get("person_id")
            employer = episode.get("employer_id")
            if person not in nodes or nodes[person].get("kind", nodes[person].get("node_kind")) != "person" or not employer:
                self.rejected_episodes += 1
                continue
            reported_activity = episode.get("relationship") == "co_reported_lobbying_activity"
            if not reported_activity and (episode.get("continuous_employment_asserted") is False or episode.get("date_precision") == "filing_year"):
                years = sorted({int(year) for year in episode.get("filing_years", []) if str(year).isdigit() and 1 <= int(year) <= 9999})
                if not years:
                    self.rejected_episodes += 1
                    continue
                episode.update(employer_id=str(employer), filing_years=years)
                self.filing_by_person[person].append(episode)
                for year in years:
                    self.filing_by_employer_year[str(employer), year].append(episode)
                continue
            start, end = _date(episode.get("start")), _date(episode.get("end_exclusive"))
            observed = _date(episode.get("as_of"))
            if episode.get("current_evidence") and observed:
                # An observed dated current snapshot supports only its observed day.
                # A user statement of a present fact is not an observation window: it
                # stands from its date until a later dated statement supersedes it, so
                # it must not lapse merely because the calendar advanced past that date.
                start = start or observed
                end = end or observed + timedelta(days=1)
                if self._standing(episode):
                    end = max(end, route_as_of + timedelta(days=1))
                    assertion_end = _date(episode.get('assertion_end_exclusive'))
                    if assertion_end:
                        end = min(end, assertion_end)
            if start is None or end is None or end <= start:
                self.rejected_episodes += 1
                continue
            episode.update(person_id=person, employer_id=str(employer), _start=start, _end=end,
                           start=start.isoformat(), end_exclusive=end.isoformat())
            if reported_activity:
                if not str(employer).startswith("activity:"):
                    self.rejected_episodes += 1
                    continue
                self.activity_by_person[person].append(episode)
                self.activity_by_hub[str(employer)].append(episode)
                continue
            self.by_person[person].append(episode)
            self.by_employer[str(employer)].append(episode)
            if observed and self._standing(episode):
                standing[person, str(employer)].append(episode)
        self._supersede_standing(standing)

    @staticmethod
    def _supersede_standing(standing):
        """A later dated statement about the same people and workplace ends the earlier one."""
        for group in standing.values():
            group.sort(key=lambda episode: _date(episode["as_of"]))
            for earlier, later in zip(group, group[1:]):
                stop = max(earlier["_start"], min(earlier["_end"], _date(later["as_of"])))
                earlier.update(_end=stop, end_exclusive=stop.isoformat())

    @classmethod
    def from_static(cls, graph, extra_episodes=(), revision=None, as_of=None, activity_provider=None, nodes=None):
        graph.load()
        episodes = []
        nodes = graph.nodes if nodes is None else nodes
        for node_id, node in nodes.items():
            if node.get("kind") != "person":
                continue
            for stint in node.get("stints", []):
                episodes.append({"person_id": node_id, "employer_id": stint["container_id"],
                    "start": stint.get("core_start"), "end_exclusive": stint.get("core_end_exclusive"),
                    "current_evidence": False, "as_of": None,
                    "confidence": {"S1": .75, "S2": .5, "S3": .25}.get(node.get("identity_tier")),
                    "confidence_semantics": "declared identity tier strength; not a calibrated probability",
                    "review_only": True, "unresolved": stint.get("container_kind") == "unresolved",
                    "date_precision": stint.get("date_precision"),
                    "provenance": {"source": "verified_static_trajectory", "node": node.get("provenance"), "stint": stint}})
        episodes.extend(extra_episodes)
        return cls(nodes, episodes, revision or getattr(graph, "revision", None) or "verified-static-v4",
                   as_of or date.today().isoformat(), activity_provider=activity_provider)

    @staticmethod
    def _options(options):
        options = dict(options or {})
        defaults = {"include_review": True, "include_unresolved": False, "include_policy": False,
                    "include_user_asserted": False, "include_historical": True, "prefer_current": True,
                    "include_weak": False, "filing_cost": 5.0, "reported_activity_cost": 2.0,
                    "current_cost": 1.0, "historical_cost": 3.0,
                    "policy_cost": 8.0, "max_expansions": 2000, "max_neighbor_checks": 100000,
                    "timeout_ms": 1500, "limit": 100, "offset": 0, "objective": "evidence_cost",
                    "work_sources": "payroll_and_directory", "route_count": 1, "uncertainty_weight": 1.0, "alternative_strategy": "distinct", "avoid_people": (),
                    "_excluded_nodes": (), "_excluded_edges": (), "_excluded_contexts": (), "_target_office": None,
                    "_avoided_targets": ()}
        defaults.update({k: v for k, v in options.items() if k in defaults})
        count = float(defaults['route_count'])
        if not math.isfinite(count) or not count.is_integer() or not 1 <= count <= 5:
            raise ValueError('route_count must be an integer from 1 to 5')
        defaults['route_count'] = int(count)
        if defaults['work_sources'] not in ('directory', 'payroll_and_directory'):
            raise ValueError('work_sources must be directory or payroll_and_directory')
        if defaults['alternative_strategy'] not in ('distinct', 'independent', 'ranked'):
            raise ValueError('alternative_strategy must be distinct, independent or ranked')
        if defaults['alternative_strategy'] == 'independent' and defaults['objective'] not in ('work_time', 'evidence_cost', 'fewest_steps'):
            raise ValueError('Separate workplace evidence requires the staffer or ecosystem model')
        if isinstance(defaults['avoid_people'], str):
            defaults['avoid_people'] = tuple(filter(None, defaults['avoid_people'].split(',')))
        defaults['avoid_people'] = tuple(sorted(set(defaults['avoid_people'])))
        if len(defaults['avoid_people']) > 50:
            raise ValueError('Choose at most 50 people to avoid')
        defaults['uncertainty_weight'] = float(defaults['uncertainty_weight'])
        if not math.isfinite(defaults['uncertainty_weight']) or not 0 <= defaults['uncertainty_weight'] <= 3:
            raise ValueError('uncertainty_weight must be finite and between 0 and 3')
        if defaults['objective'] not in ('evidence_cost', 'fewest_steps', 'work_time', 'overlap_strength', 'overlap_fewest_steps', 'overlap_balanced'):
            raise ValueError('unknown route objective')
        for key in ("current_cost", "historical_cost", "policy_cost", "filing_cost", "reported_activity_cost"):
            defaults[key] = float(defaults[key])
            if not math.isfinite(defaults[key]) or defaults[key] < 0:
                raise ValueError(key + " must be finite and nonnegative")
        if defaults["reported_activity_cost"] == 0:
            raise ValueError("reported_activity_cost must be positive")
        defaults["filing_cost"] = max(defaults["filing_cost"], defaults["historical_cost"])
        for key, maximum in (("max_expansions", 20000), ("max_neighbor_checks", 1000000), ("timeout_ms", 10000), ("limit", 1000)):
            defaults[key] = max(1, min(int(defaults[key]), maximum))
        defaults['offset'] = max(0, min(int(defaults['offset']), 10000000))
        return defaults

    @staticmethod
    def _allowed(episode, options):
        return not ((episode.get("review_only") and not options["include_review"]) or
                    (episode.get("unresolved") and not options["include_unresolved"]) or
                    (episode.get("user_asserted") and not options["include_user_asserted"]))

    @staticmethod
    def _standing(episode):
        """A user statement of a present fact, not a dated observation snapshot."""
        return bool(episode.get("user_asserted") and episode.get("current_evidence"))

    @classmethod
    def _current_as_of(cls, episode, as_of):
        """Observations support their observed day; user statements stand from theirs."""
        observed = _date(episode.get("as_of"))
        if not episode.get("current_evidence") or observed is None or as_of is None:
            return False
        return observed <= as_of if cls._standing(episode) else observed == as_of

    @staticmethod
    def _confidence(value):
        if isinstance(value, str) and value.upper() in {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}:
            return None
        try:
            number = float(value)
            return max(0.0, min(1.0, number)) if math.isfinite(number) else None
        except (ValueError, TypeError):
            return None

    def _person_allowed(self, person, options):
        if person in getattr(getattr(self, 'person_facts', None), 'network_excluded', ()):
            return False
        node = self.nodes.get(person, {})
        label = str(node.get("label") or "").strip()
        if node.get("kind", node.get("node_kind")) != "person" or not label or label == person:
            return False
        if "UNRESOLVED" in str(node.get("name_status", "")).upper() and not options["include_unresolved"]:
            return False
        return True

    def _tick(self, budget, options):
        budget["neighbor_checks"] += 1
        if budget["neighbor_checks"] > options["max_neighbor_checks"] or self._clock() > budget["deadline"]:
            raise _Limit()

    @staticmethod
    def _prefer_current_effect(options, budget):
        """Whether prefer_current changed any candidate cost in this search, or was a no-op."""
        if not budget.get("current_hops"):
            return "no_effect"
        return "applied" if options["prefer_current"] else "off"

    def _neighbors(self, person, options, budget, policy=True, activity=True, tenure=True):
        best = {}
        as_of = _date(self.as_of)
        for left in self.by_person.get(person, []) if tenure else ():
            if not self._allowed(left, options):
                continue
            if options.get('work_time_only') and left.get('provenance', {}).get('work_route_source') != 'politico_profile_history':
                continue
            for right in self.by_employer[left["employer_id"]]:
                self._tick(budget, options)
                other = right["person_id"]
                if other == person or not self._allowed(right, options):
                    continue
                if options.get('work_time_only') and right.get('provenance', {}).get('work_route_source') != 'politico_profile_history':
                    continue
                if not self._person_allowed(other, options):
                    continue
                overlap = employment_overlap(left, right)
                if overlap is None:
                    continue
                start, end = map(_date, overlap)
                uncertain = any(ep.get('date_evidence') == 'presence_within_report' for ep in (left, right))
                report_core = any(ep.get('date_precision') == 'REPORT_PERIOD' for ep in (left, right))
                current = bool(not uncertain and not report_core and self._current_as_of(left, as_of) and self._current_as_of(right, as_of)
                               and start <= as_of < end)
                if current:
                    budget["current_hops"] = budget.get("current_hops", 0) + 1
                temporal_status = "current" if current else ("historical" if end <= as_of else "unknown_current_status")
                if not current and not options["include_historical"]:
                    continue
                cost = options["current_cost"] if current and options["prefer_current"] else options["historical_cost"]
                hop = {"source": person, "target": other, "relationship": "current_coworker" if current else "overlapped_tenure",
                    "evidence_class": "observed_report_core_overlap" if report_core else "work_presence_within_report" if uncertain else "dated_work_overlap",
                    "temporal_overlap_established": not report_core, "modeled_overlap": report_core,
                    "personal_familiarity_established": False,
                    "overlap_duration_days": None if uncertain or report_core else (end - start).days,
                    "model_interval_overlap_days": (end - start).days if report_core and not uncertain else None,
                    "date_semantics": "uncertain presence window covered by observed-report-core model; actual overlap and duration unverified" if uncertain and report_core else
                        "presence somewhere within this report window; duration unknown" if uncertain else
                        "overlap under observed-report-core model; actual daily service and duration unverified" if report_core else
                        "intersection of supplied employment intervals",
                    "temporal_status": temporal_status, "employer_id": left["employer_id"],
                    "employer_label": self.nodes.get(left["employer_id"], {}).get("label", left.get("employer_label", left["employer_id"])),
                    "start": start.isoformat(), "end_exclusive": end.isoformat(), "as_of": self.as_of,
                    "evidence_as_of": [left.get("as_of"), right.get("as_of")],
                    "date_precision": [left.get("date_precision"), right.get("date_precision")],
                    "confidence": (min(self._confidence(left.get("confidence")), self._confidence(right.get("confidence")))
                                   if self._confidence(left.get("confidence")) is not None and self._confidence(right.get("confidence")) is not None else None),
                    "confidence_label": [str(left.get("confidence_semantics") or left.get("confidence") or "unknown"),
                                         str(right.get("confidence_semantics") or right.get("confidence") or "unknown")],
                    "review_only": bool(left.get("review_only") or right.get("review_only")),
                    "user_asserted": bool(left.get("user_asserted") or right.get("user_asserted")),
                    "cost": cost, "cost_overlay_only": True, "provenance": [left.get("provenance", {}), right.get("provenance", {})]}
                if other not in best or (cost, -(hop["confidence"] or 0)) < (best[other]["cost"], -(best[other]["confidence"] or 0)):
                    best[other] = hop
        if activity and getattr(self.activity_provider, 'receipt', {}).get('schema') != 'staffer-general-activity-index-v2':
            for left in self.activity_by_person.get(person, []):
                if not self._allowed(left, options):
                    continue
                for right in self.activity_by_hub[left["employer_id"]]:
                    self._tick(budget, options)
                    other = right["person_id"]
                    if other == person or not self._allowed(right, options) or not self._person_allowed(other, options):
                        continue
                    # The exact source activity ID is the association. Dates are
                    # its reporting period, never a continuous employment claim.
                    start, end = max(left["_start"], right["_start"]), min(left["_end"], right["_end"])
                    if start >= end:
                        continue
                    cost = options["reported_activity_cost"]
                    if (other not in best or cost < best[other]["cost"] or
                        (cost == best[other]["cost"] and best[other]["relationship"] == "co_reported_lobbying_activity" and end.isoformat() > best[other]["end_exclusive"])):
                        best[other] = {"source": person, "target": other, "relationship": "co_reported_lobbying_activity",
                            "temporal_status": "reported_activity_period", "activity_id": left["employer_id"],
                            "employer_id": left["employer_id"], "employer_label": left.get("employer_label", left["employer_id"]),
                            "firm_id": left.get("firm_id"),
                            "start": start.isoformat(), "end_exclusive": end.isoformat(), "as_of": self.as_of,
                            "report_period": {"start": start.isoformat(), "end_exclusive": end.isoformat(),
                                              "labels": [left.get("report_period"), right.get("report_period")]},
                            "evidence_as_of": [left.get("as_of"), right.get("as_of")],
                            "continuous_employment_asserted": False, "current_evidence": False,
                            "source_relation": True, "confidence": None, "confidence_label": "EXACT_SOURCE_ACTIVITY_ASSOCIATION",
                            "confidence_scope": "shared reported lobbying activity; employment overlap and personal familiarity not established",
                            "review_only": bool(left.get("review_only") or right.get("review_only")),
                            "cost": cost, "cost_overlay_only": True,
                            "provenance": [left.get("provenance", {}), right.get("provenance", {})]}
        if activity and self.activity_provider is not None:
            try:
                budget["as_of"] = self.as_of
                supplied = self.activity_provider.neighbors(person, options, budget)
            except ActivitySearchLimited as exc:
                raise _Limit() from exc
            for other, hop in supplied.items():
                if self._person_allowed(other, options) and (other not in best or hop["cost"] < best[other]["cost"] or
                    (hop["cost"] == best[other]["cost"] and hop.get("end_exclusive", "") > (best[other].get("end_exclusive") or ""))):
                    best[other] = hop
        if policy and options["include_weak"]:
            for left in self.filing_by_person.get(person, []):
                if not self._allowed(left, options):
                    continue
                seen = set()
                for year in left["filing_years"]:
                    for right in self.filing_by_employer_year[left["employer_id"], year]:
                        self._tick(budget, options)
                        other = right["person_id"]
                        if other == person or id(right) in seen or not self._allowed(right, options) or not self._person_allowed(other, options):
                            continue
                        seen.add(id(right))
                        years = sorted(set(left["filing_years"]) & set(right["filing_years"]))
                        cost = options["filing_cost"]
                        if other not in best or cost < best[other]["cost"]:
                            best[other] = {"source": person, "target": other, "relationship": "co_reported_firm_association",
                                "temporal_status": "filing_year_association_only", "employer_id": left["employer_id"],
                                "employer_label": self.nodes.get(left["employer_id"], {}).get("label", left.get("employer_label", left["employer_id"])),
                                "filing_years": years, "start": None, "end_exclusive": None, "as_of": self.as_of,
                                "evidence_as_of": [left.get("as_of"), right.get("as_of")], "weak": True,
                                "continuous_employment_asserted": False, "confidence": None,
                                "confidence_label": "SOURCE_NATIVE_FILING_ASSOCIATION",
                                "confidence_scope": "shared filing-year evidence; employment overlap not established",
                                "review_only": bool(left.get("review_only") or right.get("review_only")),
                                "cost": cost, "cost_overlay_only": True,
                                "provenance": [left.get("provenance", {}), right.get("provenance", {})]}
        if policy and options["include_policy"]:
            for policy_id in self.nodes[person].get("policy_ids", []):
                for other in self.policy_people[policy_id]:
                    self._tick(budget, options)
                    if other == person or not options["include_review"] or not self._person_allowed(other, options):
                        continue
                    if other not in best or options["policy_cost"] < best[other]["cost"]:
                        best[other] = {"source": person, "target": other, "relationship": "shared_policy_weak",
                            "temporal_status": "undated", "policy_id": policy_id, "start": None, "end_exclusive": None,
                            "as_of": self.as_of, "confidence": None, "confidence_label": "weak undated policy association", "weak": True,
                            "review_only": True, "cost": options["policy_cost"],
                            "cost_overlay_only": True, "provenance": [self.nodes[person].get("provenance", {}), self.nodes[other].get("provenance", {})]}
        return best

    def _base(self):
        return {"revision": self.revision, "as_of": self.as_of, "algorithm": "Bidirectional Dijkstra", "heuristic": 0,
                "cost_overlay_only": True, "canonical_weights_changed": False, "rejected_episodes": self.rejected_episodes}

    @staticmethod
    def _reverse_hop(hop):
        hop = deepcopy(hop)
        hop['source'], hop['target'] = hop['target'], hop['source']
        for field in ('provenance', 'evidence_as_of', 'date_precision', 'confidence_label'):
            if isinstance(hop.get(field), list):
                hop[field].reverse()
        if isinstance(hop.get('provenance'), dict):
            for field in ('edge_ids', 'source_person_ids'):
                if isinstance(hop['provenance'].get(field), list):
                    hop['provenance'][field].reverse()
        return hop

    def route(self, origin, target, options=None, progress=None):
        return self.route_many(origin, [target], options, progress)

    def _route_alternatives(self, origin, targets, options, progress):
        """Yen's loopless paths, ranked by the selected routing objective.

        Exclusions belong to individual searches, never the shared graph. Each
        option differs in its people sequence, rather than repeating evidence
        variants of the same introduction chain.
        """
        started = self._clock()
        count = options['route_count']
        single = {**options, 'route_count': 1}
        first = self.route_many(origin, targets, single, progress)
        accepted = [first] if first['status'] == 'found' else []
        pending, seen, serial = [], {tuple(first['node_ids'])}, itertools.count()

        def rank(route):
            cost = (sum(h['distance_units'] for h in route['hops'])
                    if route.get('distance_scale') else route['cost'])
            if options['objective'] in ('fewest_steps', 'overlap_fewest_steps'):
                return (route['steps'], cost)
            if route.get('balance_step_penalty'):
                cost += round(route['balance_step_penalty'] * route['distance_scale']) * route['steps']
            return (cost, route['steps'])

        while accepted and len(accepted) < count:
            check_cancelled()
            previous = accepted[-1]
            for index in range(len(previous['node_ids']) - 1):
                root = previous['node_ids'][:index + 1]
                excluded = tuple(sorted({tuple(sorted((r['node_ids'][index], r['node_ids'][index + 1])))
                    for r in accepted if len(r['node_ids']) > index + 1 and r['node_ids'][:index + 1] == root}))
                if progress:
                    progress({'routes_found': len(accepted), 'routes_requested': count})
                check_cancelled()
                spur = self.route_many(root[-1], [p for p in targets if p not in root[:-1]],
                    {**single, '_excluded_nodes': tuple(sorted(set(single['_excluded_nodes']) | set(root[:-1]))),
                     '_excluded_edges': tuple(sorted(set(single['_excluded_edges']) | set(excluded)))}, progress)
                if spur['status'] != 'found':
                    continue
                ids = root[:-1] + spur['node_ids']
                signature = tuple(ids)
                if signature in seen:
                    continue
                seen.add(signature)
                hops = previous['hops'][:index] + spur['hops']
                cost = (sum(h['distance_units'] for h in hops) / spur['distance_scale']
                        if spur.get('distance_scale') else sum(h['cost'] for h in hops))
                candidate = {**spur, 'origin': origin, 'node_ids': ids, 'hops': hops, 'edges': hops,
                    'steps': len(hops), 'cost': cost, 'target_count': len(targets), 'cache_hit': False,
                    'graph': {'nodes': [self.nodes[p] for p in ids], 'edges': hops}}
                if 'balanced_score' in candidate:
                    candidate['balanced_score'] = cost + candidate['balance_step_penalty'] * len(hops)
                heapq.heappush(pending, (rank(candidate), next(serial), candidate))
            if not pending:
                break
            accepted.append(heapq.heappop(pending)[2])
        routes = [{**route, 'route_rank': index + 1} for index, route in enumerate(accepted)]
        return {**first, 'routes': routes, 'routes_requested': count,
                'routes_returned': len(routes), 'alternatives_complete': True,
                'search_elapsed_ms': round((self._clock() - started) * 1000, 2)}

    def closeness(self):
        """This router's work-time routes: its indexes and hop-row caches, created once.

        Creation is serialized, so two first requests share one set instead of building two work indexes, and the
        service calls this before it publishes a router, so no request has to create it.
        """
        routes = getattr(self, 'closeness_routes', None)
        if routes is None:
            with _CLOSENESS_LOCK:
                routes = getattr(self, 'closeness_routes', None)
                if routes is None:
                    from fec.surfaces.staffer_closeness_routes import ClosenessRoutes
                    routes = self.closeness_routes = ClosenessRoutes(self)
        return routes

    def forget_contact_groups(self):
        """Drop the same-displayed-name index (staffer_distinct_routes._contact_groups) kept for this router's nodes mapping.

        The index holds the mapping it was built from, so a mapping the router no longer has stays alive for as long as the
        index does. The service calls this when it installs a different mapping on this router; the next request that needs
        the index builds it again from the new mapping.
        """
        self._contact_groups = None

    def release_search_state(self):
        """Let go of what this router's searches kept: the work indexes and hop-row caches, and the name index.

        For a router the service has replaced, so that its indexes are not held while the next router's are built. A search
        still running keeps what it already holds (a request with several passes keeps the one index it took first, whatever
        happens to the router between its passes), and one that asks afterwards builds its own and answers exactly as before:
        from here on the router stores no work index or hop-row cache it builds (ClosenessRoutes.release); a distinct pass or
        avoid_people request can still store its name index (staffer_distinct_routes), which goes with the router. A router that has not searched yet gets its search
        object now, so that it is released too.
        """
        self.closeness().release()
        self.forget_contact_groups()

    @staticmethod
    def _one_work_index():
        """The scope of one search request (staffer_closeness_routes.request_work).

        A request that searches once per pass (distinct and independent alternatives, the Yen loop) takes the work index at its first pass
        and uses that one to its last, so a publish that releases this router between two passes costs no rebuild. Imported here, as the
        closeness search is.
        """
        from fec.surfaces.staffer_closeness_routes import request_work
        return request_work()

    def route_many(self, origin, targets, options=None, progress=None):
        """Find the best person path to a target set in one symmetric search.

        Every reverse step uses the same dated person-to-person edge as the
        forward search. Institutions are indexes, never shortcut vertices.
        Lexicographic distances support either fewest steps then evidence cost,
        or evidence cost then fewest steps. A met frontier is an upper bound;
        optimality is declared only after the remaining lower bound catches it.
        """
        started = self._clock()
        options = self._options(options)
        targets = tuple(sorted(set(targets)))
        if options['avoid_people']:
            from fec.surfaces.staffer_distinct_routes import contact_group
            if any(p not in self.nodes for p in options['avoid_people']):
                raise ValueError('A person to avoid is not in the indexed records')
            avoided = contact_group(self, options['avoid_people'])
            if origin in avoided or (len(targets) == 1 and not options['_target_office'] and targets[0] in avoided):
                raise ValueError('A route endpoint cannot also be a person to avoid')
            options['_excluded_nodes'] = tuple(sorted(set(options['_excluded_nodes']) | avoided))
        named = targets
        targets = tuple(p for p in named if p not in options['_excluded_nodes'])
        if options['objective'] == 'work_time' and options['_target_office'] and len(targets) < len(named):
            # The office's staff this request keeps out of the search (people the reader avoids, and people who share an avoided
            # person's displayed name). The Staffer search names them when it explains an office it found no work records at
            # (ClosenessRoutes.route), and distinct_routes hands the options on to every pass, so they are recorded once, here.
            options['_avoided_targets'] = tuple(sorted(set(options['_avoided_targets']) | (set(named) - set(targets))))
        if options['route_count'] > 1 and options['alternative_strategy'] != 'ranked':
            from fec.surfaces.staffer_distinct_routes import distinct_routes
            with self._one_work_index():
                return distinct_routes(self, origin, targets, options, progress)
        if options['objective'] == 'work_time':
            # One call, so one index for all of its Yen loop: ClosenessRoutes.route takes it once.
            return self.closeness().route(origin, targets, options, progress)
        if options['route_count'] > 1:
            with self._one_work_index():
                return self._route_alternatives(origin, targets, options, progress)
        excluded_nodes = set(options['_excluded_nodes'])
        excluded_edges = set(options['_excluded_edges'])
        if options['objective'] in ('work_time', 'overlap_strength', 'overlap_fewest_steps', 'overlap_balanced'):
            if not hasattr(self, 'overlap_routes'):
                try:
                    from .staffer_connection_facts import PersonFacts
                    from .staffer_overlap_routes import OverlapRoutes
                except ImportError:  # direct script execution: no package context
                    import os as _bootstrap_os, sys as _bootstrap_sys
                    _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
                        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
                    if _bootstrap_root not in _bootstrap_sys.path:
                        _bootstrap_sys.path.insert(0, _bootstrap_root)
                    from fec.surfaces.staffer_connection_facts import PersonFacts
                    from fec.surfaces.staffer_overlap_routes import OverlapRoutes
                self.person_facts = PersonFacts(self.nodes, [e for rows in self.by_person.values() for e in rows], self.as_of)
                self.overlap_routes = OverlapRoutes(self, self.person_facts)
            return self.overlap_routes.route(origin, targets, options, progress)
        key = (self.revision, getattr(self.activity_provider, 'revision', None), self.as_of,
               origin, targets, json.dumps(options, sort_keys=True))
        if key in self._cache:
            return {**deepcopy(self._cache[key]), 'cache_hit': True,
                    'cache_elapsed_ms': round((self._clock() - started) * 1000, 2)}
        result = {**self._base(), 'origin': origin, 'target': targets[0] if len(targets) == 1 else None,
                  'target_count': len(targets), 'objective': options['objective'], 'optimal': False,
                  'cost': None, 'steps': None, 'node_ids': [], 'hops': [], 'edges': [],
                  'graph': {'nodes': [], 'edges': []}, 'timeout_ms': None, 'complete_search': True,
                  'prefer_current': 'no_effect', 'cache_hit': False}
        if not targets or any(not self._person_allowed(p, options) for p in (origin, *targets)):
            return {**result, 'status': 'unknown_person', 'search_elapsed_ms': round((self._clock() - started) * 1000, 2)}
        # Route searches run to proof. Paging budgets still apply to coworkers,
        # but neither a wall clock nor a work counter may cut off a route.
        options = {**options, 'max_neighbor_checks': math.inf, 'max_expansions': math.inf}
        budget = {'neighbor_checks': 0, 'current_hops': 0, 'deadline': math.inf}
        fewest = options['objective'] == 'fewest_steps'
        weight = lambda cost: (1, cost) if fewest else (cost, 1)
        add = lambda a, b: (a[0] + b[0], a[1] + b[1])
        zero, infinity = (0, 0), (math.inf, math.inf)
        counter = itertools.count()
        distances = [{origin: zero}, dict.fromkeys(targets, zero)]
        previous = [{}, {}]
        queues = [[(zero, next(counter), origin)], [(zero, next(counter), p) for p in targets]]
        settled = [set(), set()]
        best, meeting = (zero, origin) if origin in targets else (infinity, None)
        expanded, last_progress = 0, started
        enabled_costs = [options['reported_activity_cost']]
        if options['prefer_current']:
            enabled_costs.append(options['current_cost'])
        if options['include_historical'] or not options['prefer_current']:
            enabled_costs.append(options['historical_cost'])
        if options['include_weak']:
            enabled_costs.append(options['filing_cost'])
        if options['include_policy']:
            enabled_costs.append(options['policy_cost'])
        min_hop = weight(min(enabled_costs))

        def trim(side):
            queue = queues[side]
            while queue and (queue[0][0] != distances[side][queue[0][2]] or queue[0][2] in settled[side]):
                heapq.heappop(queue)
            return queue[0][0] if queue else infinity

        while queues[0] and queues[1]:
            lower = [trim(0), trim(1)]
            if meeting is not None:
                # The second bound also saves a reverse expansion for a
                # direct route whose cheapest possible alternative is tied.
                if add(*lower) >= best or any(add(lower[i], min_hop) >= best for i in (0, 1)):
                    break
            if not queues[0] or not queues[1]:
                break
            side = min((0, 1), key=lambda i: (len(queues[i]), lower[i], i))
            distance, _, person = heapq.heappop(queues[side])
            settled[side].add(person)
            expanded += 1
            if not expanded % 64:
                check_cancelled()
            for other, hop in self._neighbors(person, options, budget).items():
                if other in excluded_nodes or tuple(sorted((person, other))) in excluded_edges:
                    continue
                if options['_excluded_contexts']:
                    from fec.surfaces.staffer_distinct_routes import evidence_contexts
                    if evidence_contexts(hop) & set(options['_excluded_contexts']):
                        continue
                candidate = add(distance, weight(hop['cost']))
                if candidate < distances[side].get(other, infinity):
                    distances[side][other] = candidate
                    previous[side][other] = hop
                    heapq.heappush(queues[side], (candidate, next(counter), other))
                if other in distances[1-side]:
                    joined = add(distances[side][other], distances[1-side][other])
                    if joined < best:
                        best, meeting = joined, other
            now = self._clock()
            if progress and now - last_progress >= .25:
                progress({'expanded': expanded, 'neighbor_checks': budget['neighbor_checks'],
                          'elapsed_ms': round((now - started) * 1000),
                          'best_steps_so_far': (best[0] if fewest else best[1]) if meeting is not None else None})
                last_progress = now
        status = 'found' if meeting is not None else 'unreachable'
        result.update(status=status, search_limited=False, optimal=True,
                      expanded=expanded, neighbor_checks=budget['neighbor_checks'],
                      search_elapsed_ms=round((self._clock() - started) * 1000, 2),
                      prefer_current=self._prefer_current_effect(options, budget))
        if meeting is not None:
            hops, person = [], meeting
            while person != origin:
                hop = previous[0][person]
                hops.append(hop)
                person = hop['source']
            hops.reverse()
            person = meeting
            while person not in targets:
                hop = self._reverse_hop(previous[1][person])
                hops.append(hop)
                person = hop['target']
            node_ids = [origin] + [hop['target'] for hop in hops]
            result.update(target=node_ids[-1], cost=sum(hop['cost'] for hop in hops), steps=len(hops),
                          node_ids=node_ids, hops=hops, edges=hops,
                          graph={'nodes': [{**self.nodes[p], 'id': p, 'route_highlight': True} for p in node_ids],
                                 'edges': [{**hop, 'route_highlight': True} for hop in hops]})
        self._cache[key] = deepcopy(result)
        while len(self._cache) > 64:
            self._cache.popitem(last=False)
        return result

    def coworkers(self, person, options=None):
        options = self._options(options)
        result = {**self._base(), "person_id": person, "coworkers": [], "search_limited": False, "prefer_current": "no_effect"}
        if not self._person_allowed(person, options):
            return {**result, "status": "unknown_person"}
        budget = {"neighbor_checks": 0, "deadline": self._clock() + options["timeout_ms"] / 1000}
        try:
            neighbors = self._neighbors(person, options, budget, policy=False, activity=False)
        except _Limit:
            return {**result, "status": "search_limited", "search_limited": True, "neighbor_checks": budget["neighbor_checks"], "prefer_current": self._prefer_current_effect(options, budget)}
        ordered = sorted(neighbors.values(), key=lambda hop: (hop["relationship"] != "current_coworker", hop["cost"], self.nodes[hop["target"]].get("label", hop["target"]), hop['target']))
        offset, limit = options['offset'], options['limit']
        return {**result, "status": "ok", "coworkers": [{**hop, "person_id": hop["target"], "label": self.nodes[hop["target"]].get("label", hop["target"])} for hop in ordered[offset:offset+limit]],
                "total": len(ordered), "truncated": len(ordered) > limit, 'offset': offset, 'limit': limit,
                'has_more': offset + limit < len(ordered), "neighbor_checks": budget["neighbor_checks"],
                "prefer_current": self._prefer_current_effect(options, budget)}

    def reported_collaborators(self, person, options=None):
        """Exact source activity associations, separate from employment coworkers."""
        options = self._options(options)
        result = {**self._base(), "person_id": person, "collaborators": [], "search_limited": False}
        if not self._person_allowed(person, options):
            return {**result, "status": "unknown_person"}
        budget = {"neighbor_checks": 0, "deadline": self._clock() + options["timeout_ms"] / 1000}
        try:
            neighbors = self._neighbors(person, options, budget, policy=False, tenure=False)
        except _Limit:
            return {**result, "status": "search_limited", "search_limited": True, "neighbor_checks": budget["neighbor_checks"], "prefer_current": self._prefer_current_effect(options, budget)}
        ordered = sorted(neighbors.values(), key=lambda hop: (self.nodes[hop["target"]].get("label", hop["target"]), hop["target"]))
        offset, limit = options['offset'], options['limit']
        return {**result, "status": "ok", "collaborators": [{**hop, "person_id": hop["target"], "label": self.nodes[hop["target"]].get("label", hop["target"])} for hop in ordered[offset:offset+limit]],
                "total": len(ordered), "truncated": len(ordered) > limit, 'offset': offset, 'limit': limit,
                'has_more': offset + limit < len(ordered), "neighbor_checks": budget["neighbor_checks"]}
