"""Exercise the real stake adapter against a simulated Safe/DePool boundary."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


# The wallet states who still owes a signature as a bitmask over custodian
# indices, so the fixture needs a real custodian set to match keys against.
CUSTODIAN_KEYS = ['8a' + '1d' * 31, 'b7' + '2e' * 31, 'c4' + '3f' * 31]
CUSTODIAN_CREATOR = str(int(CUSTODIAN_KEYS[0], 16))
CUSTODIAN_SET = json.dumps({
    'custodians': [
        {'index': str(index), 'pubkey': str(int(public, 16))}
        for index, public in enumerate(CUSTODIAN_KEYS)
    ]
})


@unittest.skipUnless(shutil.which('bash') and shutil.which('jq') and shutil.which('bc'), 'Requires bash, jq and bc')
class InitialStakeTests(unittest.TestCase):
    def run_stake(self, *, pending=False, unrelated=False, executed=False, bounce=False,
                  required=2, duplicate=False, flags='3', initial=True):
        # A plausible id: SafeMultisigWallet builds it as (now << 32) | lt, so
        # it carries the creation time the deadline is derived from.
        transaction_id = str((int(time.time()) << 32) | 1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'validator.addr').write_text('0:wallet\n')
            # The submitting custodian, whose share of the wallet queue is
            # checked before anything is sent.
            # One key file per custodian: the wallet names who still owes a
            # signature by custodian index, and each file is matched to its
            # index by its own public key.
            for index, public in enumerate(CUSTODIAN_KEYS, 1):
                (root / f'validator_{index}.keys.json').write_text(
                    json.dumps({'public': public, 'secret': f'{index:02d}' * 32})
                )
            transactions = [{
                'id': '123', 'creator': CUSTODIAN_CREATOR, 'dest': '0:depool',
                'payload': 'other' if unrelated else 'payload',
                'value': '50000500000000', 'bounce': True, 'sendFlags': flags,
            }] if pending else []
            if duplicate:
                transactions *= 2
            (root / 'pending.json').write_text(json.dumps({'transactions': transactions}))
            if executed:
                (root / 'executed').touch()
            scripts = Path(__file__).resolve().parents[1] / 'vendor/ever-validator/scripts'
            source = scripts / 'depool_functions/ordinary_stake.shinc'
            program = r'''
source "$MANAGED_OPS"
source "$STAKE_SOURCE"
[[ $TEST_INITIAL == 1 ]] && export EVERNODE_INITIAL_STAKE=yes
export EVERNODE_APPROVED=yes
KEYS_DIR="$TEST_ROOT"; INPL_KEYS_DIR="$TEST_ROOT"
INPL_ELECTIONS_WORK_DIR="$TEST_ROOT"
CALL_BC=bc; CALL_CLI=fake_cli
INPL_SafeC_Wallet_ABI=safe.abi
# Reading the custodian set needs the wallet ABI on disk, as on a real node.
SafeC_Wallet_ABI="$TEST_ROOT/safe.abi"; : >"$SafeC_Wallet_ABI"
MSIG_AMOUNT=100000000000000; Required_Signs=$TEST_REQUIRED; DEPOOL_STAKE_FEE_NANO=500000000
MSG_LIFETIME=60
LC_Send_MSG_Timeout=1
# The wait polls for real, so bound it well inside the subprocess timeout.
EFFECT_POLL_INTERVAL=1; EFFECT_WAIT_MAX=4; EFFECT_MIN_WAIT=2
Get_SC_current_state() { echo "written StateInit of account"; }
Current_Depool_Info='{"minStake":"10000000000"}'
fake_cli() {
    case "$*" in
        '-j body '*) echo '{"Message":"payload"}';;
        '-j decode msg '*) echo '{"Body":"body"}';;
        *' getParameters '*) echo '{"expirationTime":"3600"}';;
        *' getCustodians '*) printf '%s\n' "$TEST_CUSTODIANS";;
        *) echo '{}';;
    esac
}
Get_DP_Parts_List() {
    if [[ -f $TEST_ROOT/executed ]]; then echo '{"participants":["0:wallet"]}';
    else echo '{"participants":[]}'; fi
}
Get_DP_Part_Info() {
    # The DePool total grows once a transfer has actually been accepted.
    if [[ -f $TEST_ROOT/topped-up ]]; then echo '{"total":"100000000000000"}';
    else echo '{"total":"50000000000000"}'; fi
}
Get_MSIG_Trans_List() {
    local extra="[]" mask signs gone=""
    # Custodian 0 submitted, so its bit is already set: this is why the key to
    # sign with is read from the mask and not counted from signsReceived.
    mask=$(cat "$TEST_ROOT/mask" 2>/dev/null || echo 1)
    signs=$(cat "$TEST_ROOT/signs" 2>/dev/null || echo 1)
    [[ -f "$TEST_ROOT/trans-executed" ]] && gone=yes
    if [[ -f "$TEST_ROOT/transfers" ]] && grep -q submitted "$TEST_ROOT/transfers" \
            && [[ $Required_Signs -gt 1 ]]; then
        extra="[{\"id\":\"$TEST_TRANS_ID\",\"creator\":\"$TEST_CREATOR\",\"dest\":\"0:depool\",\"payload\":\"payload\",\"value\":\"50000500000000\",\"bounce\":true,\"sendFlags\":\"3\"}]"
    fi
    jq -c --argjson extra "$extra" --arg mask "$mask" --arg signs "$signs" \
        --arg req "$Required_Signs" --arg creator "$TEST_CREATOR" --arg gone "$gone" \
        '.transactions += $extra
         | .transactions |= map(. + {confirmationsMask: $mask, signsRequired: $req,
                                     signsReceived: $signs})
         | if $gone == "yes"
           then .transactions |= map(select(.creator != $creator))
           else . end' "$TEST_ROOT/pending.json"
}
# Submit_And_Identify delivers the file and then finds its own entry in the
# wallet queue by matching its content, so the stub is only the delivery.
Send_File_To_BC() {
    echo submitted >>"$TEST_ROOT/transfers"
    if [[ $Required_Signs == 1 ]]; then
        [[ $BOUNCE == 1 ]] || touch "$TEST_ROOT/executed" "$TEST_ROOT/topped-up"
    fi
    printf "%s\n" success
}
Send_MSIG_Trans_Confirmation() {
    local index mask signs
    echo "signed:$1:$3" >>"$TEST_ROOT/transfers"
    index=${3#validator_}; index=$(( ${index%%.*} - 1 ))
    mask=$(cat "$TEST_ROOT/mask" 2>/dev/null || echo 1)
    signs=$(cat "$TEST_ROOT/signs" 2>/dev/null || echo 1)
    # A custodian that has already signed is refused before tvm.accept(), so
    # the wallet keeps exactly the state it had.
    if (( (mask >> index) & 1 ));then
        printf "%s\n" success
        return 0
    fi
    printf "%s\n" "$(( mask | (1 << index) ))" >"$TEST_ROOT/mask"
    printf "%s\n" "$(( signs + 1 ))" >"$TEST_ROOT/signs"
    if [[ $((signs + 1)) -ge $Required_Signs ]];then
        touch "$TEST_ROOT/trans-executed"
        [[ $BOUNCE == 1 ]] || touch "$TEST_ROOT/executed" "$TEST_ROOT/topped-up"
    fi
    printf "%s\n" success
}
echoerr() { echo "$*" >&2; }
make_ordinary_stake 0:depool validator 50000
'''
            result = subprocess.run(['bash', '-c', program], capture_output=True, text=True,
                                    env=dict(os.environ, TEST_ROOT=str(root), STAKE_SOURCE=str(source),
                                             MANAGED_OPS=str(scripts / 'managed_ops.shinc'),
                                             BOUNCE=str(int(bounce)), TEST_REQUIRED=str(required),
                                             TEST_TRANS_ID=transaction_id,
                                             TEST_CREATOR=CUSTODIAN_CREATOR,
                                             TEST_CUSTODIANS=CUSTODIAN_SET,
                                             TEST_INITIAL=str(int(initial))))
            transfers = (root / 'transfers').read_text() if (root / 'transfers').exists() else ''
            # The id is generated per run, so normalise it for the assertions.
            return result, transfers.replace(transaction_id, "<id>")

    def test_matching_pending_stake_is_confirmed_without_another_transfer(self):
        result, transfers = self.run_stake(pending=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(transfers, 'signed:123:validator_2.keys.json\n')

    def test_new_stake_gets_remaining_local_confirmation(self):
        result, transfers = self.run_stake()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(transfers, 'submitted\nsigned:<id>:validator_2.keys.json\n')

    def test_completed_stake_retry_does_not_transfer_again(self):
        result, transfers = self.run_stake(executed=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(transfers, '')

    def test_single_signature_stake_waits_for_delivery(self):
        result, transfers = self.run_stake(required=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(transfers, 'submitted\n')

    def test_ambiguous_pending_stakes_are_not_signed(self):
        result, transfers = self.run_stake(pending=True, duplicate=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(transfers, '')

    def test_unrelated_pending_transaction_is_not_confirmed(self):
        for unrelated, flags in [(True, '3'), (False, '128')]:
            with self.subTest(unrelated_payload=unrelated, flags=flags):
                result, transfers = self.run_stake(pending=True, unrelated=unrelated, flags=flags)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('signed:123:', transfers)
                self.assertIn('signed:<id>:', transfers)

    def test_wallet_confirmation_without_depool_stake_is_failure(self):
        result, _ = self.run_stake(bounce=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('did not take effect', result.stderr)

    def test_a_later_top_up_is_never_skipped_as_already_staked(self):
        """Without the initial-stake flag an existing stake must not short-circuit."""
        result, transfers = self.run_stake(executed=True, initial=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(transfers, 'submitted\nsigned:<id>:validator_2.keys.json\n')

    def test_a_top_up_also_resumes_an_exactly_matching_pending_transaction(self):
        """An exact dest/value/payload match is the same request, not a new one.

        Submission and confirmation are separate on-chain messages, so retrying
        after a failed confirmation must finish the transaction already in the
        wallet instead of submitting a second transfer.
        """
        result, transfers = self.run_stake(pending=True, initial=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(transfers, 'signed:123:validator_2.keys.json\n')
