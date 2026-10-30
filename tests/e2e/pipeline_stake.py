"""Stake into two consecutive collecting rounds and check both carry it.

A DePool runs four rounds at a time. The round that is *collecting* takes new
stakes; at each election it becomes the round that bids and the next one starts
collecting, so a stake bids in the round that was collecting when it was made
and is then locked until that round finishes. Putting a stake into each round as
it collects should therefore fund consecutive bids.

A finished round's stake is paid back into whichever round is collecting at that
moment, on top of whatever is already there, so this case records what each
round holds rather than assuming the amounts stayed separate.
"""
import argparse
import json
import subprocess
import time

from readme_workflows import CASES, command, record

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('case', choices=CASES)
parser.add_argument('--amount', type=int, default=50000,
                    help='Whole tokens per round; must reach the validator assurance to bid')
parser.add_argument('--timeout', type=int, default=900)
args = parser.parse_args()
case = args.case
name = CASES[case]['name']


def retry(*arguments, timeout):
    """An election attempt holds the node's operation lock; wait it out."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return command(case, *arguments)
        except RuntimeError:
            if time.monotonic() > deadline:
                raise
            time.sleep(15)


def info():
    return json.loads(retry('depool', 'info', '-n', name, timeout=args.timeout))


def collecting(value):
    return next(r for r in value['rounds'] if str(r['step']) == '1')


first = collecting(info())
retry('depool', 'stake', '-n', name, '--amount', str(args.amount), '--yes',
      timeout=args.timeout)
after_first = info()
assert int(collecting(after_first)['stake']) >= args.amount * 10 ** 9, after_first['rounds']

deadline = time.monotonic() + args.timeout
while collecting(info())['id'] == first['id']:
    if time.monotonic() > deadline:
        raise SystemExit('The collecting round did not change within the deadline')
    time.sleep(10)

second = collecting(info())
retry('depool', 'stake', '-n', name, '--amount', str(args.amount), '--yes',
      timeout=args.timeout)
state = info()
held = {r['id']: int(r['stake'] or 0) for r in state['rounds']}
# Both rounds that were staked into must carry at least what was put in them.
assert held.get(second['id'], 0) >= args.amount * 10 ** 9, held
assert held.get(first['id'], 0) >= args.amount * 10 ** 9, held
record({'kind': 'consecutive-rounds-funded', 'case': case,
        'staked_rounds': [first['id'], second['id']], 'rounds_held': held})
print(json.dumps({'case': case, 'staked_rounds': [first['id'], second['id']],
                  'rounds_holding_stake': {k: v for k, v in held.items() if v},
                  'own_total_nanotokens': state['own_total_nanotokens']}, indent=2))
