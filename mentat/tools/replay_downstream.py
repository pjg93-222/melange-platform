"""Offline replay of the downstream collector job (D075; compute.replay.yaml, never the production compose file).

    python replay_downstream.py --cassette DIR --dummy-keys FILE --archive-root DIR [--workdir DIR] -- python compute_job.py run collect-downstream --run-id ID -- <job arguments>

The downstream collector never calls the provider itself: the host broker does. So this is not a proxy in front of the job. It starts the SAME broker (api_host_pool.EvidenceBroker) in
this process, on 127.0.0.1, with dummy keys, a scratch quota ledger and the cassette (archive_cassette.py, built from archived responses) standing in for the provider; writes the
descriptor with reach `loopback` where MENTAT_REQUEST_POOL_CONFIG points; runs the job as a child; then stops the broker and writes REPLAY_REPORT (cassette entries, served, misses,
unused). The child's exit code wins; otherwise a MISS is fatal (exit 3). No network is possible or used: the container has network_mode none.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.append(str(HERE.parent / 'app'))


def job_arguments(command):
    """The job's own arguments: everything after the LAST `--` of the command that follows this tool's own `--`."""
    if '--' not in command:
        raise SystemExit('replay_downstream: the command must carry the job arguments after a `--`')
    return command[len(command) - command[::-1].index('--'):]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if '--' not in argv:
        raise SystemExit('replay_downstream: give the command after `--`')
    split = argv.index('--')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--cassette', type=Path, required=True)
    parser.add_argument('--dummy-keys', type=Path, required=True)
    parser.add_argument('--archive-root', type=Path, required=True)
    parser.add_argument('--workdir', type=Path, default=Path('/tmp/broker'))
    args = parser.parse_args(argv[:split])
    command = argv[split + 1:]
    import compute_job
    import api_host_pool
    import stored_paths
    from archive_cassette import Cassette, CassetteOpener
    plan = compute_job.downstream_plan('collect-downstream', job_arguments(command))
    keys = [line.strip() for line in args.dummy_keys.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not keys or any(not key.startswith('replay-dummy-') for key in keys):
        raise SystemExit('replay_downstream: only dummy keys (replay-dummy-...) may stand in for the provider here')
    args.workdir.mkdir(parents=True, exist_ok=True)
    cassette = Cassette.load(args.cassette)
    broker = api_host_pool.EvidenceBroker(keys=keys, ledger=args.workdir / 'budget.sqlite', archive_root=args.archive_root, run_id=plan['collector_run_id'],
                                          families=api_host_pool.parse_families(plan['families']), max_attempts=plan['max_requests'], opener=CassetteOpener(cassette), cap=10 ** 9,
                                          stored_root=stored_paths.to_stored(args.archive_root))   # the spelling the collector's MENTAT_STORED_PATH_MAP reads back
    server, thread, url = api_host_pool.evidence_http_server(broker)
    descriptor = args.workdir / 'request-pool.json'
    try:
        text = api_host_pool.evidence_descriptor_text(broker, url, reach='loopback')
        broker.descriptor_sha256 = __import__('hashlib').sha256(text.encode()).hexdigest()
        descriptor.write_text(text, encoding='utf-8')
        environment = dict(os.environ, MENTAT_REQUEST_POOL_CONFIG=str(descriptor))
        code = subprocess.run(command, env=environment).returncode
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        broker.shutdown()
        descriptor.unlink(missing_ok=True)
    report = {'schema_version': 1, 'purpose': 'downstream-replay-report', 'run_id': plan['collector_run_id'], **cassette.report(), 'child_exit_code': code,
              'broker_receipt': str(broker.receipt_path) if broker.receipt_path else None}
    target = os.environ.get('REPLAY_REPORT')
    if target:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if code:
        return code
    return 3 if report['misses'] else 0


if __name__ == '__main__':
    sys.exit(main())
