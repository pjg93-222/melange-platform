"""Negative controls for test_prewarm.py (and, with --integration, test_prewarm_integration.py).

Each control copies prewarm.py or glassbox_emit.py to a temporary folder, applies ONE deliberate
defect (the old text must occur exactly once), and runs the suite against the copy through
PREWARM_UNDER_TEST / GLASSBOX_UNDER_TEST. A control passes when every test it targets fails. The run
passes when the untouched suite passes and every test in the suite fails under at least one control.
Nothing outside the temporary folder is written. Run from mentat/tests:
    python prewarm_negative_controls.py [--integration]
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
APP = HERE.parent / 'app'

P, G = 'prewarm.py', 'glassbox_emit.py'
CONTROLS = [
    # (id, file, old, new, [tests that must fail])
    ('spark-cycle', P, "value': f'{_js_text(end - 1)}-{_js_text(end)}'", "value': f'{_js_text(end)}'",
     ['test_bodies_are_the_clients_bodies']),
    ('contract-blind', P, '            if snippet not in text:\n', '            if False:\n',
     ['test_contract_reports_client_drift']),
    ('hints-ignored-in-order', P, '    order = industry_order(wanted, usable_hints)\n', '    order = list(wanted)\n',
     ['test_kind_order_then_newest_hint_first']),
    ('companies-reversed', P, '    for company in companies or []:\n', '    for company in reversed(companies or []):\n',
     ['test_neighbours_follow_the_client_select_then_the_other_bases']),
    ('stale-options-accepted', P,
     "        companies = cached.get('companies') if cached.get('source_fp') == source.get('source_fp') else None\n",
     "        companies = cached.get('companies')\n", ['test_unknown_companies_wait_for_the_hint_scope_result']),
    ('unverified-planned', P,
     "    unverified = [dataset for dataset in order if by_dataset[dataset].get('verified') is False]\n",
     '    unverified = []\n', ['test_unverified_sources_are_skipped_and_missing_indexes_blocked']),
    ('no-dedupe', P,
     "        identity = ('query' if task['kind'] == 'query_view' else 'insights', task['dataset'], canonical(task['body']))\n",
     "        identity = task['id']\n", ['test_a_request_is_planned_once']),
    ('hints-unsorted', P, "    hints.sort(key=lambda hint: (hint['seen_utc'] or '', -hint['position']), reverse=True)\n",
     '', ['test_hints_file_is_read_defensively']),
    ('options-forgotten', P, "        if isinstance(info.get('companies_list'), list):\n            self.remember_options(task, info['companies_list'])\n",
     '', ['test_run_warms_every_task_once_and_records_it']),
    ('never-warm', P, '    """True when the ledger\'s last success still describes the cache the console would read."""\n',
     '    """True when the ledger\'s last success still describes the cache the console would read."""\n    return False\n',
     ['test_already_warm_tasks_are_not_rerun']),
    ('busy-ignored', P, '            if busy:\n', '            if False:\n',
     ['test_low_memory_and_a_busy_console_pause_without_counting_as_failures']),
    ('memory-guard-off', P, '        if free < MIN_FREE_BYTES:\n', '        if free < 0:\n',
     ['test_low_memory_and_a_busy_console_pause_without_counting_as_failures', 'test_memory_threshold_is_one_and_a_half_gigabytes']),
    ('threshold-1gb', P, 'MIN_FREE_BYTES = int(1.5 * 1024 ** 3)\n', 'MIN_FREE_BYTES = int(1.0 * 1024 ** 3)\n',
     ['test_memory_threshold_is_one_and_a_half_gigabytes']),
    ('parent-ignored', P, '        if not self.parent_ok():\n', '        if False:\n',
     ['test_parent_gone_exits_cleanly_and_releases_the_lock']),
    ('code-change-ignored', P, '        if not self.code_ok():\n', '        if False:\n',
     ['test_code_change_exits_after_the_current_task']),
    ('code-message-unclassified', P,
     "_CODE_CHANGED = re.compile(r'code changed|definitions changed|restart (the app|mentat|the console)', re.I)\n",
     "_CODE_CHANGED = re.compile(r'NEVER MATCHES', re.I)\n",
     ['test_code_changed_raised_by_a_task_ends_the_run', 'test_root_cause_table']),
    ('four-strikes', P, 'MAX_CONSECUTIVE_FAILURES = 3\n', 'MAX_CONSECUTIVE_FAILURES = 4\n',
     ['test_three_consecutive_failures_quarantine_until_the_fingerprint_changes']),
    ('code-does-not-release', P,
     "        if entry and (entry.get('source_fp') != task['source_fp'] or entry.get('code_fp') != code_fp):\n",
     "        if entry and (entry.get('source_fp') != task['source_fp']):\n",
     ['test_a_code_change_releases_the_quarantine_too']),
    ('as-of-ignored', P, '        if success.get(\'as_of\') != wanted:\n            return False\n', '',
     ['test_date_rollover_rewarms_insights_but_not_query_views']),
    ('date-trigger-off', P, "        if self.today() != plan['as_of']:\n            return 'date_changed'\n", '',
     ['test_the_date_changing_mid_run_triggers_a_replan']),
    ('hints-trigger-off', P, "                return 'hints_changed'\n", '                pass\n',
     ['test_a_new_newest_hint_triggers_a_replan']),
    ('sources-never-change', P, '        sources = self.backend.sources()\n        return sources_signature(sources)',
     '        sources = self.backend.sources()\n        return False and sources_signature(sources)',
     ['test_a_source_change_is_seen']),
    ('source-change-unclassified', P,
     "_SOURCE_CHANGED = re.compile(r'changed (while|before|during)|source (has )?changed|active (uncheckpointed )?writes'",
     "_SOURCE_CHANGED = re.compile(r'NEVER MATCHES'", ['test_root_cause_table', 'test_every_observation_has_the_contract_shape']),
    ('no-retry-once', P, "        return ('retry once now' if consecutive <= 1 else 'retry after 60 s'), quarantine\n",
     "        return 'retry after 60 s', quarantine\n", ['test_every_hint_has_a_safe_retry_and_a_stop']),
    ('observation-without-stop', P, "            'root_cause_hint': hint, 'retry': retry, 'stop': stop, 'error': error,\n",
     "            'root_cause_hint': hint, 'retry': retry, 'error': error,\n",
     ['test_every_observation_has_the_contract_shape']),
    ('no-rotation', P, '                if self.state.ledger.stat().st_size + len(line) > self.limit:\n',
     '                if False:\n', ['test_ledger_rotates_at_its_limit']),
    ('release-looks-stale', P,
     "        stale = previous if previous and previous.get('pid') != info.get('pid') and not previous.get('released_utc') else None\n",
     "        stale = previous if previous and previous.get('pid') != info.get('pid') else None\n",
     ['test_single_instance_and_clean_release']),
    ('stale-unreported', P,
     "        stale = previous if previous and previous.get('pid') != info.get('pid') and not previous.get('released_utc') else None\n",
     '        stale = None\n', ['test_a_dead_holder_is_stale_and_taken_over']),
    ('no-os-lock', P, '                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)\n', '                pass\n',
     ['test_a_live_holder_in_another_process_keeps_the_lock_until_it_dies', 'test_single_instance_and_clean_release']),
    ('orphan-not-awaited', P, '            if self._orphaned(holder):\n', '            if False:\n',
     ['test_a_new_console_waits_for_the_orphaned_warmer_of_the_old_one']),
    ('completion-miscounted', P, "        'completion_rate': round(warm / total, 4) if total else None,\n",
     "        'completion_rate': round(warm / (total + 1), 4) if total else None,\n", ['test_status_metrics']),
    ('two-json-lines', P, "    real_stdout.write(json.dumps(report, ensure_ascii=False, default=str) + '\\n')\n",
     "    real_stdout.write(json.dumps(report, ensure_ascii=False, default=str) + '\\n')\n    real_stdout.write('{}\\n')\n",
     ['test_every_op_prints_one_json_object']),
    ('bom', G, "encoding='utf-8', newline='\\n')", "encoding='utf-8-sig', newline='\\n')",
     ['test_event_files_match_the_stream_format']),
    ('emission-raises', G, '    except Exception:\n        # Observation must never break the observed process.\n',
     '    except ZeroDivisionError:\n        # Observation must never break the observed process.\n',
     ['test_emission_failure_never_raises']),
    ('startup-not-idempotent', P, "            if _startup['thread'] is not None:\n                return _startup['thread']\n", '',
     ['test_startup_warm_is_idempotent_never_raises_and_emits']),
    ('read-only-launches', P, '    if read_only:\n        return None\n    try:\n        parent = os.getpid()\n',
     '    try:\n        parent = os.getpid()\n', ['test_launch_warmer_is_never_used_by_a_read_only_console']),
    ('normal-priority', P, "        flags = (BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW) if os.name == 'nt' else 0\n",
     "        flags = CREATE_NO_WINDOW if os.name == 'nt' else 0\n",
     ['test_launch_warmer_uses_real_file_handles_and_below_normal_priority']),
    ('sha-unchecked', P, "    if hashlib.sha256(payload).hexdigest() != envelope['sha256']:\n", '    if False:\n',
     ['test_a_tampered_cached_payload_is_refused']),
    ('list-difference-missed', P, "            if index >= len(left) or index >= len(right):\n                return f'{path}[{index}]'\n",
     '            if index >= len(left) or index >= len(right):\n                return None\n',
     ['test_first_difference_names_the_json_path']),
    ('history-compared', P,
     "VOLATILE_PATHS = (('history', 'inspected_at'), ('history', 'newly_inspected_versions'), ('source_history',))\n",
     "VOLATILE_PATHS = (('history', 'inspected_at'), ('history', 'newly_inspected_versions'))\n",
     ['test_only_the_journals_own_clock_and_counters_are_ignored']),
    ('stale-samples', P, "        if row.get('as_of') != today or not row.get('response_path') or not row.get('key'):\n",
     "        if not row.get('response_path') or not row.get('key'):\n",
     ['test_samples_are_todays_warmed_insights_round_robin_over_industries']),
]

INTEGRATION_CONTROLS = [
    ('journal-clock-compared', P,
     "VOLATILE_PATHS = (('history', 'inspected_at'), ('history', 'newly_inspected_versions'), ('source_history',))\n",
     "VOLATILE_PATHS = ()\n", ['test_plan_warm_ledger_status_verify_on_scratch_state']),
    ('warmer-body-drifts', P, "            'limit': 100000, 'offset': 0}\n", "            'limit': 99999, 'offset': 0}\n",
     ['test_plan_warm_ledger_status_verify_on_scratch_state']),
    ('warmer-outlives-console', P, '        if not self.parent_ok():\n', '        if False:\n',
     ['test_a_launched_warmer_exits_when_its_console_is_gone']),
]


def run_bounded(arguments, timeout, cwd=None, env=None):
    """A child with no pipes (DE-026): both streams on real temporary files, wait(timeout), and on an
    overrun the child's whole tree is killed before TimeoutExpired propagates."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        child = subprocess.Popen(arguments, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        try:
            code = child.wait(timeout=timeout)
        except BaseException:
            kill_tree(child)
            raise
        out.seek(0)
        err.seek(0)
        return code, out.read().decode('utf-8', 'replace'), err.read().decode('utf-8', 'replace')


def kill_tree(child):
    if child.poll() is not None:
        return
    if os.name == 'nt':
        killer = subprocess.Popen(['taskkill', '/F', '/T', '/PID', str(child.pid)], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            killer.wait(timeout=30)
        except subprocess.TimeoutExpired:
            killer.kill()
    else:
        child.kill()
    try:
        child.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


IMPORT_FAILURE = re.compile(r'ModuleNotFoundError|ImportError|No module named')
# A copy of prewarm.py in a temporary folder must still find the console's modules: its APP line is
# pinned to the real app folder, so the copy differs from the original only by the defect.
APP_LINE = 'APP = Path(__file__).resolve().parent\n'


def run_suite(module, env, timeout):
    """(failing test names, all test names, returncode, seconds, output)."""
    started = time.perf_counter()
    code, out, err = run_bounded([sys.executable, '-m', 'unittest', '-v', module], timeout, cwd=str(HERE), env=env)
    output = out + err
    names = set(re.findall(r'^(test_\w+) \(', output, re.M))
    failing = set(re.findall(r'^(?:FAIL|ERROR): (test_\w+) \(', output, re.M))
    return failing, names, code, time.perf_counter() - started, output


def mutate(directory, name, old, new):
    source = (APP / name).read_text(encoding='utf-8')
    if old is not None:
        count = source.count(old)
        if count != 1:
            raise SystemExit(f'control text occurs {count} times in {name}: {old!r}')
        source = source.replace(old, new)
    if name == P:
        if source.count(APP_LINE) != 1:
            raise SystemExit('prewarm.py APP line not found once')
        source = source.replace(APP_LINE, f'APP = Path({str(APP.resolve())!r})\n')
    target = Path(directory) / name
    target.write_text(source, encoding='utf-8', newline='\n')
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--integration', action='store_true', help='also run the integration suite controls')
    parser.add_argument('--only', choices=('unit', 'integration'), default=None)
    options = parser.parse_args()
    suites = []
    if options.only in (None, 'unit'):
        suites.append(('test_prewarm', CONTROLS, 300))
    if options.only == 'integration' or (options.only is None and options.integration):
        suites.append(('test_prewarm_integration', INTEGRATION_CONTROLS, 1800))
    ok = True
    for module, controls, timeout in suites:
        base_env = {**os.environ}
        if module == 'test_prewarm_integration':
            base_env['MENTAT_PREWARM_INTEGRATION'] = '1'
        failing, names, code, seconds, _ = run_suite(module, base_env, timeout)
        print(f'{module}: untouched suite {"PASS" if code == 0 and not failing else "FAIL"} '
              f'({len(names)} tests, {seconds:.1f} s)', flush=True)
        ok &= code == 0 and not failing
        # The relocated copy with no defect must pass too, or a failure below could be the relocation.
        with tempfile.TemporaryDirectory() as directory:
            target = mutate(directory, P, None, None)
            failing, _, code, seconds, _ = run_suite(module, {**base_env, 'PREWARM_UNDER_TEST': str(target)}, timeout)
        print(f'{module}: relocated copy without a defect {"PASS" if code == 0 and not failing else "FAIL"} '
              f'({seconds:.1f} s)', flush=True)
        ok &= code == 0 and not failing
        broken = set()
        for control, name, old, new, expected in controls:
            with tempfile.TemporaryDirectory() as directory:
                target = mutate(directory, name, old, new)
                env = {**base_env, ('PREWARM_UNDER_TEST' if name == P else 'GLASSBOX_UNDER_TEST'): str(target)}
                failing, _, code, seconds, output = run_suite(module, env, timeout)
            imported = bool(IMPORT_FAILURE.search(output))
            caught = set(expected) <= failing and not imported
            ok &= caught
            broken |= failing if not imported else set()
            print(f'  {"caught" if caught else "MISSED"}  {control:28} expected {",".join(expected)}; '
                  f'failed {len(failing)}: {",".join(sorted(failing))} ({seconds:.1f} s)'
                  + ('; INVALID: an import failed' if imported else ''), flush=True)
        uncovered = sorted(names - broken)
        ok &= not uncovered
        print(f'{module}: {len(names) - len(uncovered)}/{len(names)} tests fail under at least one control'
              + (f'; never broken: {", ".join(uncovered)}' if uncovered else ''))
    print('NEGATIVE CONTROLS ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
