// The contract between the producer, ClosenessRoutes (fec/surfaces/staffer_closeness_routes.py), and the page (staffer_live/staffer-route.js)
// for a Staffer search that finds no route. Both suites import this one file, so neither can drift from the other in prose:
//   test_staffer_unreachable_hint.py builds each named scenario on a real router and asserts that the producer's answer carries exactly these
//     fields, through the router, the service and the HTTP handler, and it fails when a string, a reason or the suggested objective changes;
//   test_staffer_unreachable_hint.cjs feeds every payload to the real page and asserts what it shows and what its one button does.
// Everything after `module.exports =` is strict JSON, so the Python suite reads it without node. `mode` is the destination the payload answers.
module.exports = {
  "cases": [
    {
      "name": "office_without_work_records",
      "mode": "office",
      "payload": {
        "status": "unreachable",
        "objective": "work_time",
        "unreachable_reason": "office_without_work_records",
        "message": "No Staffer route: none of this office's current staff have dated employment at this office in the records this model uses.",
        "suggested_objective": "evidence_cost"
      }
    },
    {
      "name": "origin_without_work_connections",
      "mode": "person",
      "payload": {
        "status": "unreachable",
        "objective": "work_time",
        "unreachable_reason": "origin_without_work_connections",
        "message": "No Staffer route: the person you start with has no workplace connections this search can use.",
        "suggested_objective": "evidence_cost"
      }
    },
    {
      "name": "origin_without_work_connections_office",
      "mode": "office",
      "payload": {
        "status": "unreachable",
        "objective": "work_time",
        "unreachable_reason": "origin_without_work_connections",
        "message": "No Staffer route: the person you start with has no workplace connections this search can use.",
        "suggested_objective": "evidence_cost"
      }
    },
    {
      "name": "target_without_work_connections",
      "mode": "person",
      "payload": {
        "status": "unreachable",
        "objective": "work_time",
        "unreachable_reason": "target_without_work_connections",
        "message": "No Staffer route: the person you want to reach has no workplace connections this search can use.",
        "suggested_objective": "evidence_cost"
      }
    },
    {
      "name": "target_without_work_connections_office",
      "mode": "office",
      "payload": {
        "status": "unreachable",
        "objective": "work_time",
        "unreachable_reason": "target_without_work_connections",
        "message": "No Staffer route: none of this office's current staff with dated employment there has a workplace connection this search can use.",
        "suggested_objective": "evidence_cost"
      }
    },
    {
      "name": "no_path_in_work_records",
      "mode": "person",
      "payload": {
        "status": "unreachable",
        "objective": "work_time",
        "unreachable_reason": "no_path_in_work_records",
        "message": "No Staffer route between these people in the work records this model uses.",
        "suggested_objective": "evidence_cost"
      }
    },
    {
      "name": "no_path_in_work_records_excluded",
      "mode": "person",
      "payload": {
        "status": "unreachable",
        "objective": "work_time",
        "unreachable_reason": "no_path_in_work_records",
        "message": "No Staffer route between these people in the work records this model uses, leaving out the people you excluded.",
        "suggested_objective": "evidence_cost"
      }
    },
    {
      "name": "no_path_in_work_records_office",
      "mode": "office",
      "payload": {
        "status": "unreachable",
        "objective": "work_time",
        "unreachable_reason": "no_path_in_work_records",
        "message": "No Staffer route between this person and this office's current staff in the work records this model uses.",
        "suggested_objective": "evidence_cost"
      }
    },
    {
      "name": "no_path_in_work_records_office_excluded",
      "mode": "office",
      "payload": {
        "status": "unreachable",
        "objective": "work_time",
        "unreachable_reason": "no_path_in_work_records",
        "message": "No Staffer route between this person and this office's current staff in the work records this model uses, leaving out the people you excluded.",
        "suggested_objective": "evidence_cost"
      }
    },
    {
      "name": "insufficient_work_coverage",
      "mode": "person",
      "payload": {
        "status": "insufficient_work_coverage",
        "objective": "work_time",
        "message": "These people are known, but their records do not meet this model's authenticated staffer coverage requirements. Try Ecosystem for documented associations.",
        "suggested_objective": "evidence_cost"
      }
    }
  ]
};
