"""Run the generated election sequence near the start of an election window."""
import json
import subprocess
import sys
import time

from readme_workflows import CASES, root as case_root

case = sys.argv[1]
root = case_root(case)


def console(command):
    result = subprocess.run(['docker', 'exec', 'evernode-' + CASES[case]['name'], 'console',
                             '-C', '/ever-node/node_cfg/console.json', '-jc', command],
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


deadline = time.monotonic() + 650
while time.monotonic() < deadline:
    timing = console('getconfig 15')['p15']
    validators = console('getconfig 34')['p34']
    now = console('getstatsnew')['masterchainblocktime']
    opens = validators['utime_until'] - timing['elections_start_before']
    closes = validators['utime_until'] - timing['elections_end_before']
    if opens + 4 <= now < closes - 90:
        print(json.dumps({'chain_time': now, 'opens': opens, 'closes': closes}), flush=True)
        with (root / 'logs/validator/election-cron.log').open('a') as log:
            result = subprocess.run(['bash', str(root / 'run-elections.sh')], stdout=log, stderr=log)
        raise SystemExit(result.returncode)
    time.sleep(3)
raise SystemExit('No early election window observed')
