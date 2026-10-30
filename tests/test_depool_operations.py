"""Exercise the real DePool stake operations against a simulated Safe boundary.

These reference functions prompt on /dev/tty and, with more than one required
signature, only print the remaining Sign_Trans.sh command. Both make them
unusable from a managed, detached session, so the managed path is covered here.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "vendor/ever-validator/scripts"

# Each operation: the call the dispatcher makes, and the DePool state once the
# operation has actually been accepted. Signing is not the effect -- the
# confirmation that executes the wallet transaction ends in an internal message
# the DePool can still refuse -- so a stub that only records signatures would
# not tell a working operation from a silently refused one.
PENDING_WITHDRAWAL = '{"stakes":{"0":"50000000000000"},"total":"50000000000000",' \
                     '"withdrawValue":"100000000000","reinvest":false}'
# The submitting custodian of the fixture, and the value each operation puts
# on its transfer, which is what Submit_And_Identify matches on.
CUSTODIAN_PUBLIC = "8a" + "1d" * 31
CUSTODIAN_CREATOR = str(int(CUSTODIAN_PUBLIC, 16))
# The wallet decides who still owes a signature from a bitmask over custodian
# indices, so the fixture needs a real custodian set: one key file per
# custodian, matched to its index by its own public key and nothing else.
CUSTODIAN_KEYS = [CUSTODIAN_PUBLIC, "b7" + "2e" * 31, "c4" + "3f" * 31]
CUSTODIAN_SET = json.dumps({
    "custodians": [
        {"index": str(index), "pubkey": str(int(public, 16))}
        for index, public in enumerate(CUSTODIAN_KEYS)
    ]
})
SUBMIT_VALUE = {
    "withdraw_part_stake": "500000000",
    "withdraw_entire_stake": "500000000",
    "cancel_withdrawal": "500000000",
    "remove_stake": "100500000000",
    "replenish_depool": "20000000000",
}
BEFORE = '{"stakes":{"0":"50000000000000"},"total":"50000000000000",' \
         '"withdrawValue":"0","reinvest":true}'
OPERATIONS = {
    "withdraw_part_stake": (
        "withdraw_part_stake 0:depool validator 100",
        '{"stakes":{"0":"50000000000000"},"total":"50000000000000",'
        '"withdrawValue":"100000000000","reinvest":true}',
        "20000000000",
    ),
    "withdraw_entire_stake": (
        "withdraw_entire_stake 0:depool validator",
        '{"stakes":{"0":"50000000000000"},"total":"50000000000000",'
        '"withdrawValue":"0","reinvest":false}',
        "20000000000",
    ),
    # A cancellation only makes sense when a withdrawal is actually pending,
    # so this one starts from a different state: its effect is the return to
    # the ordinary one, which would be invisible if it started there.
    "cancel_withdrawal": (
        "cancel_withdrawal 0:depool validator",
        BEFORE,
        "20000000000",
        PENDING_WITHDRAWAL,
    ),
    "remove_stake": (
        "remove_stake 0:depool validator 100",
        '{"stakes":{"0":"49900000000000"},"total":"49900000000000",'
        '"withdrawValue":"0","reinvest":true}',
        "20000000000",
    ),
    "replenish_depool": (
        "replenish_depool 0:depool validator 20",
        BEFORE,
        "39500000000",
    ),
}

HARNESS = r"""
source "$MANAGED_OPS"
source "$OPERATION_SOURCE"
KEYS_DIR="$TEST_ROOT"; INPL_KEYS_DIR="$TEST_ROOT"
INPL_ELECTIONS_WORK_DIR="$TEST_ROOT"; ELECTIONS_WORK_DIR="$TEST_ROOT"
INPL_SafeC_Wallet_ABI=safe.abi; INPL_DePool_ABI=depool.abi; SIG_ID_OPTION=""
# Reading the custodian set needs the wallet ABI on disk, the same as reading
# the pending list does on a real node.
SafeC_Wallet_ABI="$TEST_ROOT/safe.abi"; : >"$SafeC_Wallet_ABI"
CALL_BC=bc; CALL_CLI=fake_cli
Required_Signs=$TEST_REQUIRED
DEPOOL_STAKE_FEE_NANO=500000000
Depool_BalanceThreshold=45000000000; Depool_Self_Balance=20000000000
Current_Depool_Info='{"minStake":"10000000000"}'
MSIG_AMOUNT=100000000000000
MSG_LIFETIME=60
BoldText=""; NormText=""
# The wait polls for real, so keep it quick and bounded well inside the
# subprocess timeout; a hang here is a failure worth seeing, not one to wait on.
EFFECT_POLL_INTERVAL=1; EFFECT_WAIT_MAX=4; EFFECT_MIN_WAIT=2
LC_Send_MSG_Timeout=1
fake_cli() {
    case "$*" in
        '-j body '*) echo '{"Message":"payload"}';;
        '-j decode msg '*) echo '{"Body":"body"}';;
        *' getParameters '*) echo '{"expirationTime":"3600"}';;
        *' getCustodians '*) printf '%s\n' "$TEST_CUSTODIANS";;
        *) echo '{}';;
    esac
}
Get_SC_current_state() { echo "written StateInit of account"; }
# The DePool answers with the operation's effect only once a message that
# executes the wallet transaction has actually been sent.
Accepted() { [[ -f "$TEST_ROOT/accepted" ]]; }
Get_DP_Part_Info() { if Accepted; then echo "$TEST_PART_AFTER"; else echo "$TEST_PART_BEFORE"; fi; }
Get_DP_Parts_List() { echo '{"participants":["0:wallet"]}'; }
Get_DP_Self_Balance() {
    if Accepted; then DP_balance_nT="$TEST_BALANCE_AFTER"; else DP_balance_nT=20000000000; fi
    echo "scale=3; $DP_balance_nT / 1000000000" | bc
}
# The wallet queue: whatever the case staged, plus this operation's own entry
# once it has been delivered. Submit_And_Identify has to find that entry by
# matching its content, so the entry has to be a real one.
Get_MSIG_Trans_List() {
    local extra="[]" mask signs gone=""
    # Custodian 0 submitted, so its bit is set from the start -- which is why
    # counting signatures cannot say which key still owes one.
    mask=$(cat "$TEST_ROOT/mask" 2>/dev/null || echo 1)
    signs=$(cat "$TEST_ROOT/signs" 2>/dev/null || echo 1)
    [[ -f "$TEST_ROOT/executed" ]] && gone=yes
    if [[ -f "$TEST_ROOT/trace" ]] && grep -q submitted "$TEST_ROOT/trace" \
            && [[ $Required_Signs -gt 1 ]]; then
        extra="[{\"id\":\"$TEST_TRANS_ID\",\"creator\":\"$TEST_CREATOR\",\"dest\":\"0:depool\",\"payload\":\"payload\",\"value\":\"$TEST_SUBMIT_VALUE\",\"bounce\":true,\"sendFlags\":\"3\"}]"
    fi
    # A transaction leaves the list when the last signature executes it, which
    # is the only reason it may leave while still inside its lifetime.
    jq -c --argjson extra "$extra" --arg mask "$mask" --arg signs "$signs" \
        --arg req "$Required_Signs" --arg creator "$TEST_CREATOR" --arg gone "$gone" \
        '.transactions += $extra
         | .transactions |= map(. + {confirmationsMask: $mask, signsRequired: $req,
                                     signsReceived: $signs})
         | if $gone == "yes"
           then .transactions |= map(select(.creator != $creator))
           else . end' "$TEST_ROOT/pending.json"
}
# Submit_And_Identify delivers the file and then identifies its own entry by
# content, so the stub is the delivery and the wallet queue answers the rest.
Send_File_To_BC() {
    echo "submitted" >>"$TEST_ROOT/trace"
    # With one required signature the submit transfers inline, so delivery is
    # itself the message that takes effect and no entry is ever created.
    [[ $Required_Signs -le 1 ]] && touch "$TEST_ROOT/accepted"
    printf "%s\n" success
}
Send_MSIG_Trans_Confirmation() {
    local index mask signs
    echo "signed:$1:$3" >>"$TEST_ROOT/trace"
    index=${3#validator_}; index=$(( ${index%%.*} - 1 ))
    mask=$(cat "$TEST_ROOT/mask" 2>/dev/null || echo 1)
    signs=$(cat "$TEST_ROOT/signs" 2>/dev/null || echo 1)
    # A custodian that has already signed is rejected before tvm.accept(), so
    # the wallet is left exactly as it was and no block records the attempt.
    if (( (mask >> index) & 1 ));then
        printf "%s\n" success
        return 0
    fi
    printf "%s\n" "$(( mask | (1 << index) ))" >"$TEST_ROOT/mask"
    printf "%s\n" "$(( signs + 1 ))" >"$TEST_ROOT/signs"
    if [[ $((signs + 1)) -ge $Required_Signs ]];then
        touch "$TEST_ROOT/executed"
        touch "$TEST_ROOT/accepted"
    fi
    printf "%s\n" success
}
echoerr() { echo "$*" >&2; }
"""


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq") and shutil.which("bc"),
    "Requires bash, jq and bc",
)
class DePoolOperationTests(unittest.TestCase):
    def run_operation(self, name, *, approved=True, required=2, signing_fails=False,
                      pending=0, pending_value="500000000", refused=False,
                      transaction_id=None, already_effected=False):
        entry = OPERATIONS[name]
        call, after, balance_after = entry[:3]
        before = entry[3] if len(entry) > 3 else BEFORE
        if refused:
            # The wallet transaction executed, but its internal message was
            # refused, so the DePool's state never changes. This is the case no
            # signature count can detect.
            after, balance_after = before, "20000000000"
        if transaction_id is None:
            # A plausible id: SafeMultisigWallet builds it as (now << 32) | lt,
            # so it carries the creation time the deadline is derived from.
            transaction_id = (int(time.time()) << 32) | 1
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "validator.addr").write_text("0:wallet\n")
            # The submitting custodian, whose share of the wallet queue is
            # checked before anything is sent.
            for index, public in enumerate(CUSTODIAN_KEYS, 1):
                (root / f"validator_{index}.keys.json").write_text(
                    json.dumps({"public": public, "secret": f"{index:02d}" * 32})
                )
            # The wallet queue the operation inspects before submitting. It
            # carries our own creator, because an entry another custodian
            # submitted is not ours to resume however well its content matches.
            matching = {
                "id": str(transaction_id), "creator": CUSTODIAN_CREATOR,
                "dest": "0:depool", "payload": "payload", "value": pending_value,
                "bounce": True, "sendFlags": "3",
            }
            (root / "pending.json").write_text(
                json.dumps({"transactions": [matching] * pending})
            )
            if already_effected:
                # An earlier attempt got this transaction confirmed in time, so
                # the DePool already shows the effect before this run starts.
                (root / "accepted").touch()
            program = HARNESS
            if signing_fails:
                program += 'Send_MSIG_Trans_Confirmation() { return 4; }\n'
            if approved:
                program += "export EVERNODE_APPROVED=yes\n"
            program += call + "\n"
            result = subprocess.run(
                ["bash", "-c", program],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=60,
                env=dict(
                    os.environ,
                    TEST_ROOT=str(root),
                    TEST_REQUIRED=str(required),
                    TEST_TRANS_ID=str(transaction_id),
                    TEST_CREATOR=CUSTODIAN_CREATOR,
                    TEST_CUSTODIANS=CUSTODIAN_SET,
                    TEST_SUBMIT_VALUE=SUBMIT_VALUE[name],
                    TEST_PART_BEFORE=before,
                    TEST_PART_AFTER=after,
                    TEST_BALANCE_AFTER=balance_after,
                    MANAGED_OPS=str(SCRIPTS / "managed_ops.shinc"),
                    OPERATION_SOURCE=str(SCRIPTS / f"depool_functions/{name}.shinc"),
                ),
            )
            trace = (root / "trace").read_text() if (root / "trace").exists() else ""
            # The id is generated per run, so normalise it for the assertions.
            return result, trace.replace(str(transaction_id), "<id>")

    def test_every_operation_completes_its_required_signatures(self):
        for name in OPERATIONS:
            with self.subTest(operation=name):
                result, trace = self.run_operation(name)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(trace, "submitted\nsigned:<id>:validator_2.keys.json\n")

    def test_three_signature_wallet_sends_both_remaining_signatures(self):
        result, trace = self.run_operation("withdraw_part_stake", required=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            trace,
            "submitted\nsigned:<id>:validator_2.keys.json\n"
            "signed:<id>:validator_3.keys.json\n",
        )

    def test_single_signature_wallet_needs_no_confirmation(self):
        result, trace = self.run_operation("withdraw_entire_stake", required=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(trace, "submitted\n")

    def test_failed_confirmation_is_reported_instead_of_silent_success(self):
        result, _ = self.run_operation("withdraw_part_stake", signing_fails=True)
        self.assertNotEqual(result.returncode, 0)
        # The wallet's own mask never gained the signature, which is the only
        # evidence a confirmation ran: one it refuses is recorded nowhere.
        self.assertIn("never recorded the signature", result.stderr)

    def test_an_exactly_matching_pending_transaction_is_resumed_not_resubmitted(self):
        """Submission and confirmation are separate on-chain messages.

        A run that died between them left a real transaction holding one
        signature; retrying must finish it rather than submit a second transfer.
        """
        for name in ("withdraw_entire_stake", "withdraw_part_stake",
                     "cancel_withdrawal", "replenish_depool"):
            with self.subTest(operation=name):
                value = "20000000000" if name == "replenish_depool" else "500000000"
                result, trace = self.run_operation(name, pending=1, pending_value=value)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(trace, "signed:<id>:validator_2.keys.json\n")
                self.assertNotIn("submitted", trace)

    def test_an_unrelated_pending_transaction_does_not_block_a_new_submission(self):
        result, trace = self.run_operation(
            "withdraw_entire_stake", pending=1, pending_value="999"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(trace, "submitted\nsigned:<id>:validator_2.keys.json\n")

    def test_two_matching_pending_transactions_are_refused_rather_than_guessed(self):
        result, trace = self.run_operation("withdraw_entire_stake", pending=2)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("More than one pending transaction", result.stderr)
        self.assertEqual(trace, "")

    def test_unapproved_operation_without_a_terminal_does_not_send(self):
        for name in OPERATIONS:
            with self.subTest(operation=name):
                result, trace = self.run_operation(name, approved=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(trace, "")

    def test_every_operation_waits_for_its_own_effect(self):
        """Signing to the required count is not the operation.

        The confirmation that executes the wallet transaction ends in an
        internal message the DePool can refuse -- pool closed, below minStake,
        too little attached. Nothing about the signatures differs in that case,
        so an operation that reported on signing reported success for a
        transfer that never arrived.
        """
        for name in OPERATIONS:
            with self.subTest(operation=name):
                result, trace = self.run_operation(name, refused=True)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("did not take effect", result.stderr)
                # It signed, which is exactly why signing proves nothing.
                self.assertIn("signed:<id>:", trace)

    def test_a_confirmed_effect_is_reported_with_the_wait_it_took(self):
        result, _ = self.run_operation("withdraw_part_stake")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("is confirmed on chain", result.stdout)

    def test_a_single_signature_wallet_ignores_a_stale_pending_entry(self):
        """With one required signature the submit transfers inline and creates
        no pending entry, so a match can only be a leftover from a different
        wallet configuration. Resuming it would skip the submit and then wait
        for an effect nothing produced."""
        result, trace = self.run_operation(
            "withdraw_entire_stake", required=1, pending=1
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(trace, "submitted\n")

    def test_an_expired_transaction_is_reported_as_unsignable(self):
        """A pending transaction dies an hour after its id was generated, and
        no further signature can revive it. Waiting the full patience would
        only delay a verdict that is already settled."""
        expired = ((int(time.time()) - 7200) << 32) | 1
        # Such a transaction can only be a leftover a retry finds: an id this
        # stale is older than the window a fresh submit is identified within.
        result, _ = self.run_operation(
            "withdraw_part_stake", refused=True, pending=1, transaction_id=expired
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("can no longer be signed", result.stderr)

    def test_an_expired_transaction_whose_effect_landed_still_succeeds(self):
        """The transaction may have been confirmed before it expired, which is
        the state a resumed run finds. One poll must still happen."""
        expired = ((int(time.time()) - 7200) << 32) | 1
        result, _ = self.run_operation(
            "withdraw_part_stake", pending=1, transaction_id=expired,
            already_effected=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("is confirmed on chain", result.stdout)

    def test_the_wait_outlasts_every_message_the_operation_signed(self):
        """What makes a failure report final: by the time it is printed,
        nothing this operation sent can still execute."""
        managed = (SCRIPTS / "managed_ops.shinc").read_text()
        self.assertIn("${MSG_LIFETIME:-60} * 2", managed)
        self.assertIn("export MSG_LIFETIME=60", (SCRIPTS / "env.sh").read_text())
        for path in sorted((SCRIPTS / "depool_functions").glob("*.shinc")):
            with self.subTest(operation=path.name):
                self.assertNotIn("--lifetime 3600", path.read_text())


@unittest.skipUnless(shutil.which("bash"), "Requires bash")
class StakeOperationArgumentTests(unittest.TestCase):
    """WD-all and cancelWithdrawal were unreachable: the mandatory-parameter
    guard demanded four arguments while both accept exactly three, and an
    argument error exited 0 so a caller saw success."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        shutil.copyfile(
            SCRIPTS / "stake_to_depool.sh", self.directory / "stake_to_depool.sh"
        )
        self.marker = self.directory / "reached-dispatch"
        # The real script loads these two before its argument guard. Stubs keep
        # the test on the guard instead of the whole reference environment.
        (self.directory / "env.sh").write_text(
            "export ENV_LOADED=true\n"
            "export VALIDATOR_NAME=validator\n"
            'export KEYS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/keys"\n'
        )
        (self.directory / "functions.shinc").write_text(
            "function DispEnvInfo() { echo stub-env; }\n"
            "function Determine_Current_Network() { echo stub-network; }\n"
            'function echoerr() { echo "$*" >&2; }\n'
            "function NameToAddr() { echo 0:depool; }\n"
            "function Require_Node_Console() { return 0; }\n"
            # Reaching this means the guard passed. It runs in a command
            # substitution, so it records a marker instead of exiting.
            f'function Get_Account_Custodians_Info() {{ touch "{self.marker}"; return 1; }}\n'
        )
        keys = self.directory / "keys"
        keys.mkdir()
        (keys / "validator.addr").write_text("0:wallet\n")
        (keys / "validator_1.keys.json").write_text('{"public":"aa","secret":"bb"}')

    def dispatch(self, *arguments):
        return subprocess.run(
            ["bash", str(self.directory / "stake_to_depool.sh"), *arguments],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )

    def test_withdrawal_operations_pass_the_mandatory_parameter_guard(self):
        for operation in ("WD-all", "cancelWithdrawal"):
            with self.subTest(operation=operation):
                self.marker.unlink(missing_ok=True)
                result = self.dispatch(operation, "--", "--")
                self.assertNotIn("Insufficient parameters", result.stdout)
                self.assertTrue(self.marker.exists(), result.stdout + result.stderr)

    def test_amount_taking_operations_still_pass_the_guard(self):
        for arguments in (("WD-part", "--", "--", "100"), ("ordinary", "--", "--", "50")):
            with self.subTest(arguments=arguments):
                self.marker.unlink(missing_ok=True)
                result = self.dispatch(*arguments)
                self.assertNotIn("Insufficient parameters", result.stdout)
                self.assertTrue(self.marker.exists(), result.stdout + result.stderr)

    def test_an_argument_error_no_longer_reports_success(self):
        result = self.dispatch("WD-all")
        self.assertIn("Insufficient parameters", result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())
