"""Negative controls for test_prewarm_prime: each deliberately broken variant of prewarm.py or the
launcher must make its named tests FAIL (a load error does not count as caught).

    python prime_negative_controls.py [--app DIR]
Prints one line per control and NEGATIVE CONTROLS PASS / FAIL; exits 0 / 1.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
P, L = 'prewarm.py', 'Start-MentatApp.ps1'
# prewarm.py imports these from its own folder at load time, so each variant folder gets a copy too.
SIBLINGS = ('proclock.py',)
CONTROLS = [
    ('value_order ignores access', P, "    if not access_rows and not reprime:\n        return tasks\n", "    return tasks\n",
     ['test_access_promotes_the_most_used_scope', 'test_build_plan_ranks_by_access_value']),
    ('reprime not first', P, "reprimed = [index for index in ranked if index in hot and tasks[index]['dataset'] in set(reprime)]",
     "reprimed = []", ['test_a_changed_source_reprimes_its_hot_items_first']),
    ('hot set ignores max_items', P, "if len(hot) < max_items and spent + item['est_s'] <= budget_seconds:",
     "if spent + item['est_s'] <= budget_seconds:", ['test_budget_caps_items_and_seconds']),
    ('no recent-industry boost', P, "        weights[recent] += RECENT_INDUSTRY_BOOST\n", "        pass\n",
     ['test_most_recent_industry_ranks_first_hints_only']),
    ("an earlier day's hint counts", P, "if not name or not hint.get('key') or _local_day(hint.get('seen_utc')) != today:",
     "if not name or not hint.get('key'):", ['test_an_earlier_days_hint_is_not_hot']),
    ('primed ignores readiness', P, "'primed': phase in ('background', 'done') and ready == len(items),",
     "'primed': phase in ('background', 'done'),", ['test_ready_counts_and_primed', 'test_a_failed_hot_item_is_not_ready']),
    ('startup skips the hot set', P, "hot, background, estimate = select_hot_set(candidates, HOT_MAX_ITEMS, HOT_BUDGET_SECONDS)\n        _prime",
     "hot, background, estimate = [], candidates, 0.0\n        _prime", ['test_startup_primes_the_hot_set_before_anything_else']),
    ('recorder raises on a full queue', P, "            self._queue.put_nowait((kind, body, cost_s, size))\n        except Exception:",
     "            self._queue.put_nowait((kind, body, cost_s, size))\n        except ZeroDivisionError:",
     ['test_records_off_thread_and_never_raises']),
    ('access_stats import not tolerated', P, "    except ImportError:\n        return []\n    try:\n        rows = stats.top",
     "    except ZeroDivisionError:\n        return []\n    try:\n        rows = stats.top", ['test_load_access_top_falls_back_without_access_stats']),
    # Review of batch 1, round 3 (2026-09-24): the warmer ranks from the request kinds only.
    ('warmer window takes every kind', P, "    return load_access_top(kinds=ACCESS_REQUEST_KINDS, limit=limit, module=module)\n",
     "    return load_access_top(kinds=None, limit=limit, module=module)\n",
     ['test_the_warmer_ranks_from_every_request_and_no_cache_layer_row']),
    ('warmer window drops contribution queries', P,
     "ACCESS_REQUEST_KINDS = ACCESS_GRAPH_KINDS + ('comparison_flows', 'insights_query', 'contribution_query')\n",
     "ACCESS_REQUEST_KINDS = ACCESS_GRAPH_KINDS + ('comparison_flows', 'insights_query')\n",
     ['test_the_warmer_ranks_from_every_request_and_no_cache_layer_row']),
    ('run warmer bypasses the request kinds', P, "max_company_neighbors=options.max_neighbors, access_top=warmer_access_top)",
     "max_company_neighbors=options.max_neighbors, access_top=load_access_top)",
     ['test_the_warmer_ranks_from_every_request_and_no_cache_layer_row']),
    ('launcher ignores primed', L, "if ($status.primed -eq $true) {", "if ($false) {", ['test_waits_until_primed']),
    ('launcher never times out', L, "if ($left -le 0) {", "if ($false) {", ['test_times_out_and_opens_anyway']),
    ('launcher waits on an older console', L, "if ($null -ne $failed -and [int]$failed.StatusCode -eq 404) {", "if ($false) {",
     ['test_an_older_console_is_not_waited_for']),
    ('launcher waits on -NoBrowser restarts', L, "if (-not $NoBrowser) {\n  # Primed",
     "$null = Wait-MentatPrimed \"http://127.0.0.1:$Port\" 120 1000\nif (-not $NoBrowser) {\n  # Primed",
     ['test_only_the_window_path_waits']),
]


def run_suite(prewarm_path, launcher_path, folder, name):
    env = {**os.environ, 'PRIME_TEST_PREWARM': str(prewarm_path), 'PRIME_TEST_LAUNCHER': str(launcher_path)}
    out, err = folder / f'{name}.out.txt', folder / f'{name}.err.txt'
    with open(out, 'wb') as stdout, open(err, 'wb') as stderr:
        process = subprocess.Popen([sys.executable, '-m', 'unittest', '-v', 'test_prewarm_prime'], cwd=str(HERE),
                                   stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, env=env)
        try:
            process.wait(timeout=240)
        except subprocess.TimeoutExpired:
            subprocess.call(['taskkill', '/T', '/F', '/PID', str(process.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            process.wait(timeout=10)
            return None, 'timeout'
    text = err.read_text(errors='replace')
    # Only unittest's own summary lines count (a per-line verdict regex can span lines and over-count).
    failed = set(re.findall(r'^(?:FAIL|ERROR): (test_\w+)', text, re.M))
    ran = re.search(r'^Ran (\d+) tests?', text, re.M)
    return (failed, int(ran.group(1)) if ran else 0), text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', default=str(HERE.parent / 'app'))
    options = parser.parse_args()
    app = Path(options.app)
    root = Path(tempfile.mkdtemp(prefix='prime-controls-'))
    clean, _ = run_suite(app / P, app / L, root, 'clean')
    ok = clean is not None and not clean[0] and clean[1] > 0
    print(f'clean suite: {"OK" if ok else "FAILED"} ({clean[1] if clean else 0} tests)')
    for index, (name, target, old, new, expected) in enumerate(CONTROLS, 1):
        folder = root / f'c{index:02d}'
        folder.mkdir()
        for filename in (P, L) + SIBLINGS:
            shutil.copyfile(app / filename, folder / filename)
        path = folder / target
        source = path.read_bytes().decode('utf-8')
        if source.count(old) != 1:
            print(f'[{index:02d}] {name}: ANCHOR NOT UNIQUE ({source.count(old)}) - control invalid')
            ok = False
            continue
        path.write_bytes(source.replace(old, new).encode('utf-8'))
        result, text = run_suite(folder / P, folder / L, folder, 'run')
        if result is None:
            print(f'[{index:02d}] {name}: suite timed out - counted as MISSED')
            ok = False
            continue
        failed, ran = result
        load_error = ran == 0 or 'ImportError' in text.split('Traceback', 1)[0]
        caught = not load_error and set(expected) <= failed
        ok = ok and caught
        print(f'[{index:02d}] {name}: {"CAUGHT" if caught else "MISSED"} (failing: {sorted(failed)}; ran {ran})')
    print('NEGATIVE CONTROLS PASS' if ok else 'NEGATIVE CONTROLS FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
