"""Exercise the documented recovery from a half-signed Safe transaction.

Submitting a multisig transaction and signing it are separate on-chain
messages. A run that cannot gather the required signatures leaves a real
pending transaction, which blocks later DePool operations until it is
completed. This case induces that state by withholding the custodian keys this
node would otherwise sign with, then completes it with `depool confirm`.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from readme_workflows import BASE, CASES, command, record, root

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('case', choices=CASES)
parser.add_argument('--amount', type=int, default=5, help='Whole tokens for the induced operation')
args = parser.parse_args()
case = args.case
name = CASES[case]['name']
registry = BASE / CASES[case]['registry']
keys = root(case) / 'keys'
withheld = BASE / 'withheld-custodian-keys'


def info():
    return json.loads(command(case, 'depool', 'info', '-n', name))


def attempt(*extra):
    """Run an operation that is expected to stop without every signature."""
    argv = ['evernode', '--state-dir', str(registry), 'depool', *extra, '-n', name, '--yes']
    result = subprocess.run(argv, capture_output=True, text=True, timeout=900)
    record({'kind': 'confirm-case-attempt', 'case': case, 'argv': argv,
            'exit_code': result.returncode})
    return result


before = info()
assert before['pending_wallet_transactions'] == [], 'Resolve pending Safe transactions first'

# Withhold every custodian key but the one that submits, so the transaction can
# only reach one of its two required signatures.
withheld.mkdir(exist_ok=True, mode=0o700)
moved = []
for index in (2, 3):
    source = keys / f'{name}_{index}.keys.json'
    if source.exists():
        target = withheld / source.name
        source.rename(target)
        moved.append((target, source))
record({'kind': 'confirm-case-keys-withheld', 'case': case, 'count': len(moved)})

try:
    result = attempt('replenish', '--amount', str(args.amount))
    assert result.returncode != 0, 'An operation that cannot be signed must not report success'
    output = result.stdout + result.stderr
    found = re.search(r'transaction (\d{15,25})', output)
    assert found, 'The failure must name the transaction left pending:\n' + output[-2000:]
    transaction = found.group(1)
    assert 'signatures it needs' in output, output[-2000:]

    pending = info()['pending_wallet_transactions']
    assert [item['id'] for item in pending] == [transaction], pending

    # A retry must finish the transaction already in the wallet, not submit a
    # second transfer: submission and confirmation are separate messages.
    again = attempt('replenish', '--amount', str(args.amount))
    assert again.returncode != 0
    pending = info()['pending_wallet_transactions']
    assert [item['id'] for item in pending] == [transaction], pending
    record({'kind': 'confirm-case-retry-resumed', 'case': case, 'transaction': transaction})
finally:
    for target, source in moved:
        target.rename(source)

# An id the wallet does not hold is refused by name, without sending anything.
unknown = str(int(transaction) + 1)
refused = subprocess.run(['evernode', '--state-dir', str(registry), 'depool', 'confirm',
                          '-n', name, '--transaction-id', unknown, '--yes'],
                         capture_output=True, text=True, timeout=900)
assert refused.returncode != 0, 'An unknown transaction id must be refused'
assert unknown in refused.stdout + refused.stderr
assert info()['pending_wallet_transactions'][0]['id'] == transaction, 'The refusal must send nothing'

command(case, 'depool', 'confirm', '-n', name, '--transaction-id', transaction, '--yes')
deadline = time.monotonic() + 180
while time.monotonic() < deadline:
    after = info()
    if after['pending_wallet_transactions'] == []:
        break
    time.sleep(5)
else:
    raise SystemExit('The confirmed transaction stayed pending')
assert float(after['self_balance_tokens']) > float(before['self_balance_tokens']), after

record({'kind': 'confirm-case-passed', 'case': case, 'transaction': transaction,
        'self_balance_before': before['self_balance_tokens'],
        'self_balance_after': after['self_balance_tokens']})
print(json.dumps({'case': case, 'transaction': transaction,
                  'retry_resumed_same_transaction': True,
                  'unknown_id_refused': True,
                  'self_balance_before': before['self_balance_tokens'],
                  'self_balance_after': after['self_balance_tokens']}, indent=2))
