"""Observe each README case through its generated election runner to validation.

The runner, its scripts and the CLI are the production ones. Only the wait
inside the generated test sequence is shortened, because this local network
runs 300-second rounds with a 120-second election window while production
rounds last hours. Production timings make that wait comfortable.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import re
import subprocess
import sys
import time

from readme_workflows import BASE, CASES, command, console, record, root, snapshot

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('case', choices=CASES)
parser.add_argument('--sequence-delay', type=int, default=1,
                    help='Replaces the generated sequence sleep between preparation and participation')
parser.add_argument('--min-remaining', type=int, default=100,
                    help='Seconds of election window the runner needs to finish in')
parser.add_argument('--launch-offset', type=int, default=4,
                    help='Seconds after the on-chain opening before starting the runner')
parser.add_argument('--poll', type=float, default=3.0,
                    help='Seconds between chain polls; lower lands the runner nearer the opening')
parser.add_argument('--rounds', type=int, default=1,
                    help='Distinct validator-set periods to observe validating')
parser.add_argument('--timeout', type=int, default=1800)
parser.add_argument('--keep-schedule', action='store_true',
                    help='Leave the election schedule installed when finished')
args = parser.parse_args()
if args.sequence_delay < 0:
    parser.error('Test delays must be nonnegative')
if args.rounds < 1 or args.min_remaining < 0:
    parser.error('--rounds must be positive and --min-remaining nonnegative')
case = args.case
item = CASES[case]
sequence = root(case) / 'run-election-sequence.sh'
stock = sequence.read_text()
updated, count = re.subn(r'^sleep \d+$', f'sleep {args.sequence_delay}', stock, flags=re.M)
assert count == 1
sequence.write_text(updated)
sequence.chmod(0o700)
record({'kind': 'timing-adaptation', 'case': case, 'stock_sequence': stock,
        'test_sequence': sequence.read_text(),
        'reason': 'This local network has a 120-second election window; production windows last hours'})

# Signing needs no timing adaptation: each signature is added one at a time and
# checked against the wallet's own record of who has signed, so there is no
# fixed pause to shorten.

def validating(stats):
    """The chain's own account of this node validating right now."""
    return (stats.get('in_current_vset_p34') is True
            and stats.get('validation_status') == 'Active'
            and stats.get('timediff', 999) <= 10 and stats.get('shards_timediff', 999) <= 10
            and any(0 <= v <= 30 for v in stats.get('last_validation_ago_sec', {}).values()))


worker = None
attempted = set()
observed = []
log = (root(case) / 'logs/validator/readme-election.log').open('a')


def stop_worker():
    """Never leave a runner behind.

    An interrupted attempt leaves a half-signed transaction in the Safe, which
    the next election preparation deliberately refuses to work around.
    """
    global worker
    if worker and worker.poll() is None:
        os.killpg(worker.pid, signal.SIGTERM)
        try:
            worker.wait(timeout=120)
        except subprocess.TimeoutExpired:
            os.killpg(worker.pid, signal.SIGKILL)
    worker = None


def observe():
    global worker
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        stats = console(case, 'getstatsnew')
        if worker and worker.poll() is not None:
            record({'kind': 'election-runner-finish', 'case': case, 'exit_code': worker.returncode})
            worker = None
        timing = console(case, 'getconfig 15')['p15']
        validators = console(case, 'getconfig 34')['p34']
        now = stats['masterchainblocktime']
        period = validators['utime_since']
        if validating(stats) and period not in observed:
            observed.append(period)
            value = snapshot(case, f'active-validator-round{len(observed)}')
            record({'kind': 'round-validated', 'case': case, 'round': len(observed),
                    'validator_set_since': period, 'stats': value['stats']})
            print(f'[{case}] ACTIVE VALIDATION CONFIRMED for set {period} '
                  f'({len(observed)}/{args.rounds})', flush=True)
            if len(observed) >= args.rounds:
                return
        election = validators['utime_until']
        opens = election - timing['elections_start_before']
        closes = election - timing['elections_end_before']
        # Bid for the next election even while validating, exactly as cron would.
        if (not worker and not stats.get('in_next_vset_p36')
                and election not in attempted
                and opens + args.launch_offset <= now < closes - args.min_remaining
                and stats['timediff'] <= 10 and stats['shards_timediff'] <= 10):
            attempted.add(election)
            record({'kind': 'election-runner-start', 'case': case, 'chain_time': now,
                    'election': election, 'opens': opens, 'closes': closes})
            print(f'[{case}] starting generated runner for {election} '
                  f'({closes - now}s of window left)', flush=True)
            worker = subprocess.Popen(['bash', str(root(case) / 'run-elections.sh')],
                                      stdout=log, stderr=log, start_new_session=True)
        time.sleep(args.poll)
    record({'kind': 'case-timeout', 'case': case, 'rounds_validated': len(observed)})
    raise SystemExit(f'Only {len(observed)} of {args.rounds} rounds validated within the deadline')


try:
    observe()
    if worker and worker.poll() is None:
        worker.wait(timeout=300)
except BaseException:
    stop_worker()
    raise
record({'kind': 'case-passed', 'case': case, 'rounds_validated': len(observed),
        'validator_sets': observed})
if not args.keep_schedule:
    command(case, 'election', 'stop', '-n', item['name'])
