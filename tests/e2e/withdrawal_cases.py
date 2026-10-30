"""Exercise the DePool stake-withdrawal commands against the live network.

A DePool does not return a stake immediately: withdrawPart and withdrawAll mark
the participant's stake, and the contract releases it when the round holding it
completes. These cases therefore assert on the contract's own pending
withdrawal value rather than on an immediate Safe balance change.
"""
import argparse
import json
import sys
import time

from readme_workflows import CASES, command, record


def info(case):
    name = CASES[case]['name']
    return json.loads(command(case, 'depool', 'info', '-n', name))


def wait_for(case, label, predicate, timeout=180):
    """Poll the DePool's own getters until the contract reflects the request."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = info(case)
        if predicate(value):
            record({'kind': 'withdrawal-observed', 'case': case, 'label': label,
                    'own_total_nanotokens': value['own_total_nanotokens'],
                    'own_pending_withdrawal_nanotokens': value['own_pending_withdrawal_nanotokens']})
            return value
        time.sleep(5)
    raise RuntimeError(f'{label}: the DePool did not reflect the request in time')


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('case', choices=CASES)
parser.add_argument('--amount', type=int, default=1000, help='Whole tokens for the partial withdrawal')
args = parser.parse_args()
case = args.case
name = CASES[case]['name']

before = info(case)
record({'kind': 'withdrawal-start', 'case': case, 'before': {
    'own_total_nanotokens': before['own_total_nanotokens'],
    'own_pending_withdrawal_nanotokens': before['own_pending_withdrawal_nanotokens'],
    'self_balance_tokens': before['self_balance_tokens']}})
assert before['own_total_nanotokens'], 'This node holds no DePool stake to withdraw'
assert before['pending_wallet_transactions'] == [], 'Resolve pending Safe transactions first'

# 1. Request a partial withdrawal from the next round.
command(case, 'depool', 'withdraw', '-n', name, '--amount', str(args.amount), '--yes')
requested = wait_for(case, 'withdrawPart recorded',
                     lambda v: int(v['own_pending_withdrawal_nanotokens'] or 0) == args.amount * 10**9)
assert requested['pending_wallet_transactions'] == [], 'Withdrawal left an unsigned Safe transaction'

# 2. Cancel it again, which also restores reinvestment.
command(case, 'depool', 'withdraw', '-n', name, '--cancel', '--yes')
cancelled = wait_for(case, 'withdrawal cancelled',
                     lambda v: int(v['own_pending_withdrawal_nanotokens'] or 0) == 0)
assert cancelled['own_reinvest'] is True, 'Cancelling a withdrawal must restore reinvestment'
assert cancelled['pending_wallet_transactions'] == [], 'Cancellation left an unsigned Safe transaction'
assert cancelled['own_total_nanotokens'] == before['own_total_nanotokens'], 'The stake itself must be unchanged'

# 3. Request the entire stake, which is how a participant leaves a DePool, then
#    cancel it again: withdrawing the stake that backs this node's validator
#    assurance would end its validation.
#    The two forms are not the same request. A partial withdrawal records a
#    value the contract pays out; the entire stake is withdrawn by clearing
#    reinvestment, so the whole stake is returned instead of rolled into the
#    next round and no withdrawal value is ever recorded.
command(case, 'depool', 'withdraw', '-n', name, '--all', '--yes')
entire = wait_for(case, 'reinvestment cleared', lambda v: v['own_reinvest'] is False)
assert int(entire['own_pending_withdrawal_nanotokens'] or 0) == 0, 'withdrawAll records no withdrawal value'
assert entire['own_total_nanotokens'] == before['own_total_nanotokens'], 'The stake stays until its round completes'
command(case, 'depool', 'withdraw', '-n', name, '--cancel', '--yes')
restored = wait_for(case, 'withdrawAll cancelled', lambda v: v['own_reinvest'] is True)
assert int(restored['own_pending_withdrawal_nanotokens'] or 0) == 0
assert restored['own_total_nanotokens'] == before['own_total_nanotokens'], 'The stake itself must be unchanged'

# 4. Replenish the DePool's own operating balance, the documented remedy for a
#    pool whose self balance has fallen towards its critical threshold.
command(case, 'depool', 'replenish', '-n', name, '--amount', '5', '--yes')
replenished = wait_for(case, 'self balance replenished',
                       lambda v: float(v['self_balance_tokens']) > float(before['self_balance_tokens']))

record({'kind': 'withdrawal-cases-passed', 'case': case,
        'partial_withdrawal_tokens': args.amount,
        'entire_withdrawal_clears_reinvest': entire['own_reinvest'],
        'self_balance_before': before['self_balance_tokens'],
        'self_balance_after': replenished['self_balance_tokens']})
print(json.dumps({
    'case': case,
    'partial_withdrawal_recorded_nanotokens': requested['own_pending_withdrawal_nanotokens'],
    'after_cancellation_nanotokens': cancelled['own_pending_withdrawal_nanotokens'],
    'entire_withdrawal_reinvest': entire['own_reinvest'],
    'after_entire_cancellation_reinvest': restored['own_reinvest'],
    'reinvest_restored': cancelled['own_reinvest'],
    'stake_unchanged': cancelled['own_total_nanotokens'] == before['own_total_nanotokens'],
    'self_balance_before': before['self_balance_tokens'],
    'self_balance_after': replenished['self_balance_tokens'],
}, indent=2))
