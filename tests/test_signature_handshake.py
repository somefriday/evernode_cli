"""The signing handshake: one signature at a time, each one evidenced.

Every reason a confirmation can fail - the key has already signed, the
transaction no longer exists, the message expired in transit - is checked
before tvm.accept(), so a failed confirmation is recorded in no block at all.
Nothing about the chain can be read afterwards to explain it. The only positive
evidence that a confirmation ran is the wallet's own confirmation mask gaining
that custodian's bit, or the transaction leaving the pending list because that
signature completed it.

So the wallet is asked three things, and nothing is inferred: which entry is
ours, who still owes a signature, and when absence stops meaning "executed".
"""
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "vendor/ever-validator/scripts"
MANAGED_OPS = SCRIPTS / "managed_ops.shinc"

# Three custodians. The first is the one the old code assumed had submitted.
KEYS = ["8a" + "1d" * 31, "b7" + "2e" * 31, "c4" + "3f" * 31]
DECIMALS = [str(int(key, 16)) for key in KEYS]
CUSTODIANS = json.dumps({
    "custodians": [
        {"index": str(index), "pubkey": decimal}
        for index, decimal in enumerate(DECIMALS)
    ]
})

PRELUDE = """
set +u
CALL_BC=bc
function echoerr() {{ printf "%s\\n" "$*" >&2; }}
function TD_unix2human() {{ printf "%s\\n" "$1"; }}
KEYS_DIR="{root}"
INPL_ELECTIONS_WORK_DIR="{root}"; ELECTIONS_WORK_DIR="{root}"
INPL_SafeC_Wallet_ABI=safe.abi; SafeC_Wallet_ABI="{root}/safe.abi"
MSG_LIFETIME=60; EFFECT_POLL_INTERVAL=1
CALL_CLI=fake_cli
function fake_cli() {{
    case "$*" in
        *' getCustodians '*) printf '%s\\n' "$TEST_CUSTODIANS";;
        *' getParameters '*) echo '{{"expirationTime":"3600"}}';;
        *) echo '{{}}';;
    esac
}}
function Get_SC_current_state() {{ echo "written StateInit of account"; }}
source "{managed_ops}"
"""


def write_keys(root, count=3):
    for index in range(1, count + 1):
        (root / f"validator_{index}.keys.json").write_text(
            json.dumps({"public": KEYS[index - 1], "secret": f"{index:02d}" * 32})
        )


def run_bash(root, body, **environment):
    (root / "safe.abi").touch()
    program = PRELUDE.format(root=root, managed_ops=MANAGED_OPS) + body
    script = root / "case.sh"
    script.write_text(program)
    import os
    return subprocess.run(
        ["bash", str(script)], capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=90,
        env=dict(os.environ, TEST_CUSTODIANS=CUSTODIANS, **environment),
    )


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq") and shutil.which("bc"),
    "Requires bash, jq and bc",
)
class UnsignedLocalKeysTests(unittest.TestCase):
    """Who still owes a signature is stated by the wallet, one bit per
    custodian index. Counting signatures instead - "received plus one" - is
    only right when the submitting custodian happened to be the first one."""

    def keys_for(self, mask, count=3):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_keys(root, count)
            result = run_bash(
                root, f'Unsigned_Local_Keys 0:wallet validator {mask}; echo "rc=$?"\n'
            )
        return result

    def test_the_submitting_custodian_is_not_asked_again(self):
        result = self.keys_for(1)
        self.assertIn("rc=0", result.stdout)
        self.assertNotIn("validator_1.keys.json", result.stdout)
        self.assertIn("1 validator_2.keys.json", result.stdout)
        self.assertIn("2 validator_3.keys.json", result.stdout)

    def test_a_submitter_that_is_not_the_first_custodian_is_handled(self):
        """The case the old count got wrong: it would have confirmed with the
        key that had already signed, and retried it until it gave up."""
        result = self.keys_for(2)
        self.assertIn("0 validator_1.keys.json", result.stdout)
        self.assertNotIn("validator_2.keys.json", result.stdout)
        self.assertIn("2 validator_3.keys.json", result.stdout)

    def test_the_last_custodian_as_submitter_is_handled(self):
        result = self.keys_for(4)
        self.assertIn("0 validator_1.keys.json", result.stdout)
        self.assertIn("1 validator_2.keys.json", result.stdout)
        self.assertNotIn("validator_3.keys.json", result.stdout)

    def test_every_mask_leaves_exactly_the_unsigned_keys(self):
        for mask in range(8):
            with self.subTest(mask=mask):
                result = self.keys_for(mask)
                expected = {
                    f"validator_{index + 1}.keys.json"
                    for index in range(3) if not (mask >> index) & 1
                }
                listed = {
                    line.split()[1] for line in result.stdout.splitlines()
                    if line.startswith(("0 ", "1 ", "2 "))
                }
                self.assertEqual(listed, expected)

    def test_a_fully_signed_transaction_leaves_no_key_to_use(self):
        result = self.keys_for(7)
        self.assertIn("rc=0", result.stdout)
        self.assertNotIn("keys.json", result.stdout)

    def test_a_key_this_wallet_does_not_know_is_ignored(self):
        """A key file left over from another wallet is not a custodian here, so
        it cannot be asked to sign: error 100 would be refused before accept."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_keys(root, 3)
            (root / "validator_9.keys.json").write_text(
                json.dumps({"public": "ff" * 32, "secret": "ee" * 32})
            )
            result = run_bash(
                root, 'Unsigned_Local_Keys 0:wallet validator 1; echo "rc=$?"\n'
            )
        self.assertIn("rc=0", result.stdout)
        self.assertNotIn("validator_9", result.stdout)

    def test_an_unreadable_mask_is_refused_rather_than_assumed(self):
        result = self.keys_for("notanumber")
        self.assertIn("rc=1", result.stdout)


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq") and shutil.which("bc"),
    "Requires bash, jq and bc",
)
class PendingEntryTests(unittest.TestCase):
    def look_up(self, identifier, entries, unreadable=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pending.json").write_text(json.dumps({"transactions": entries}))
            lister = "return 2" if unreadable else f'cat "{root}/pending.json"'
            body = (
                f"function Get_MSIG_Trans_List() {{ {lister}; }}\n"
                f'Get_Pending_Entry 0:wallet "{identifier}"; echo "rc=$?"\n'
            )
            return run_bash(root, body)

    @staticmethod
    def entry(identifier):
        return {
            "id": identifier, "creator": DECIMALS[0], "dest": "0:depool",
            "value": "500000000", "payload": "p", "bounce": True,
            "sendFlags": "3", "confirmationsMask": "1",
            "signsRequired": "2", "signsReceived": "1",
        }

    def test_the_named_transaction_is_returned(self):
        result = self.look_up("7701911206319200129",
                              [self.entry("7701911206319200129")])
        self.assertIn("rc=0", result.stdout)
        self.assertIn("7701911206319200129", result.stdout)

    def test_an_id_that_is_only_a_substring_does_not_match(self):
        """A textual search over the wallet's answer would match a truncated
        id, or one embedded in a 77-digit creator, and the confirmation sent
        for it would be refused before accept with nothing left to explain it."""
        full = "7701911206319200129"
        for probe in (full[:-1], full[1:], DECIMALS[0][5:24]):
            with self.subTest(probe=probe):
                result = self.look_up(probe, [self.entry(full)])
                self.assertIn("rc=0", result.stdout)
                self.assertNotIn("dest", result.stdout)

    def test_an_absent_transaction_is_absent_and_not_an_error(self):
        result = self.look_up("123", [self.entry("456")])
        self.assertIn("rc=0", result.stdout)
        self.assertNotIn("dest", result.stdout)

    def test_an_unreadable_wallet_is_not_reported_as_absent(self):
        """Absent means the transaction is not there; unreadable means we do
        not know, and the two must never collapse into one answer."""
        result = self.look_up("123", [self.entry("123")], unreadable=True)
        self.assertIn("rc=2", result.stdout)


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq") and shutil.which("bc"),
    "Requires bash, jq and bc",
)
class ExpiryTests(unittest.TestCase):
    """The moment absence stops proving execution is arithmetic: the id carries
    its creation second, and the wallet reports how long it keeps a pending
    transaction."""

    def expiry_of(self, identifier):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            return run_bash(
                root, f'Trans_Expires_At 0:wallet "{identifier}"; echo "rc=$?"\n'
            )

    def test_the_expiry_is_the_creation_time_plus_the_wallets_own_lifetime(self):
        created = int(time.time()) - 100
        result = self.expiry_of((created << 32) | 7)
        self.assertIn("rc=0", result.stdout)
        self.assertIn(str(created + 3600), result.stdout)

    def test_an_id_from_the_future_bounds_nothing(self):
        created = int(time.time()) + 86400
        result = self.expiry_of((created << 32) | 7)
        self.assertIn("rc=1", result.stdout)

    def test_a_non_numeric_id_bounds_nothing(self):
        self.assertIn("rc=1", self.expiry_of("abc").stdout)


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq") and shutil.which("bc"),
    "Requires bash, jq and bc",
)
class SignToExecutionTests(unittest.TestCase):
    """The handshake itself. The fake wallet behaves as the real one does: a
    confirmation from a custodian that has already signed changes nothing and
    is recorded nowhere, and the entry leaves the list when the last signature
    executes it."""

    WALLET = r'''
function Get_MSIG_Trans_List() {
    local mask signs
    mask=$(cat "$TEST_ROOT/mask" 2>/dev/null || echo "$START_MASK")
    signs=$(cat "$TEST_ROOT/signs" 2>/dev/null || echo "$START_SIGNS")
    if [[ -f "$TEST_ROOT/executed" ]];then
        echo '{"transactions":[]}'
        return 0
    fi
    jq -cn --arg id "$TEST_ID" --arg mask "$mask" --arg signs "$signs" \
        --arg req "$REQUIRED" --arg creator "$TEST_CREATOR" \
        '{transactions:[{id:$id, creator:$creator, dest:"0:depool",
          value:"500000000", payload:"p", bounce:true, sendFlags:"3",
          confirmationsMask:$mask, signsRequired:$req, signsReceived:$signs}]}'
}
function Send_MSIG_Trans_Confirmation() {
    local index mask signs
    echo "sent:$3" >>"$TEST_ROOT/trace"
    [[ ${DELIVERY_FAILS:-0} == 1 ]] && return 0
    index=${3#validator_}; index=$(( ${index%%.*} - 1 ))
    mask=$(cat "$TEST_ROOT/mask" 2>/dev/null || echo "$START_MASK")
    signs=$(cat "$TEST_ROOT/signs" 2>/dev/null || echo "$START_SIGNS")
    if (( (mask >> index) & 1 ));then
        echo "refused:$3" >>"$TEST_ROOT/trace"
        return 0
    fi
    printf "%s\n" "$(( mask | (1 << index) ))" >"$TEST_ROOT/mask"
    printf "%s\n" "$(( signs + 1 ))" >"$TEST_ROOT/signs"
    [[ $(( signs + 1 )) -ge $REQUIRED ]] && touch "$TEST_ROOT/executed"
    return 0
}
'''

    def sign(self, *, mask=1, signs=1, required=2, keys=3, delivery_fails=False,
             deadline_offset=20, unreadable=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_keys(root, keys)
            identifier = (int(time.time()) << 32) | 3
            wallet = self.WALLET
            if unreadable:
                wallet += 'function Get_MSIG_Trans_List() { return 2; }\n'
            body = wallet + (
                f'Sign_To_Execution 0:wallet validator "{identifier}" '
                f'"$(( $(date +%s) + {deadline_offset} ))" "The stake"\n'
                'echo "rc=$?"\n'
            )
            result = run_bash(
                root, body,
                TEST_ROOT=str(root), TEST_ID=str(identifier),
                TEST_CREATOR=DECIMALS[0], START_MASK=str(mask),
                START_SIGNS=str(signs), REQUIRED=str(required),
                DELIVERY_FAILS="1" if delivery_fails else "0",
            )
            trace = (root / "trace").read_text() if (root / "trace").exists() else ""
        return result, trace

    def test_one_signature_completes_a_two_of_three_wallet(self):
        result, trace = self.sign()
        self.assertIn("rc=0", result.stdout)
        self.assertEqual(trace, "sent:validator_2.keys.json\n")
        self.assertIn("executed", result.stdout)

    def test_signatures_are_sent_one_at_a_time_and_stop_on_execution(self):
        """Three required, one held: the second and third are sent in turn, and
        nothing is sent after the one that executes the transaction."""
        result, trace = self.sign(required=3)
        self.assertIn("rc=0", result.stdout)
        self.assertEqual(
            trace, "sent:validator_2.keys.json\nsent:validator_3.keys.json\n"
        )

    def test_a_key_that_already_signed_is_never_used(self):
        """It would be refused before tvm.accept() and leave no trace, so the
        loop would retry it until the deadline and report a failure for a
        transaction that only needed a different key."""
        result, trace = self.sign(mask=2, signs=1)
        self.assertIn("rc=0", result.stdout)
        self.assertNotIn("validator_2", trace)
        self.assertNotIn("refused:", trace)
        self.assertEqual(trace, "sent:validator_1.keys.json\n")

    def test_a_transaction_needing_a_key_we_do_not_have_is_reported_as_such(self):
        """Not a failure of signing: a statement that the remaining custodians
        have to sign it elsewhere."""
        result, trace = self.sign(mask=1, signs=1, required=3, keys=1)
        self.assertIn("rc=3", result.stdout)
        self.assertIn("sign it elsewhere", result.stderr)
        self.assertEqual(trace, "")

    def test_a_signature_the_wallet_never_records_is_a_failure(self):
        """The message may have been lost, or refused before accept - which
        leaves nothing in any block. Either way the wallet never showed it."""
        result, trace = self.sign(delivery_fails=True, deadline_offset=3)
        self.assertIn("rc=1", result.stdout)
        self.assertIn("never recorded the signature", result.stderr)
        self.assertIn("sent:validator_2.keys.json", trace)

    def test_nothing_is_sent_once_the_deadline_has_passed(self):
        """The bound has to hold before each signature, not only while waiting
        for one: a deadline already behind us must not buy one more send."""
        result, trace = self.sign(deadline_offset=-5)
        self.assertIn("rc=1", result.stdout)
        self.assertIn("nothing further was sent", result.stderr)
        self.assertEqual(trace, "")

    def test_an_unreadable_wallet_stops_rather_than_signs_blindly(self):
        result, trace = self.sign(unreadable=True)
        self.assertIn("rc=1", result.stdout)
        self.assertIn("could not be read", result.stderr)
        self.assertEqual(trace, "")

    def test_a_transaction_already_gone_is_not_signed_again(self):
        """A retry that finds nothing pending has nothing to do: confirming an
        id the wallet does not hold is refused before accept."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_keys(root)
            identifier = (int(time.time()) << 32) | 3
            body = (
                'function Get_MSIG_Trans_List() { echo \'{"transactions":[]}\'; }\n'
                'function Send_MSIG_Trans_Confirmation() { echo "sent:$3"; return 0; }\n'
                f'Sign_To_Execution 0:wallet validator "{identifier}" '
                '"$(( $(date +%s) + 20 ))" "The stake"\n'
                'echo "rc=$?"\n'
            )
            result = run_bash(root, body)
        self.assertIn("rc=0", result.stdout)
        self.assertNotIn("sent:", result.stdout)
        self.assertIn("already completed", result.stdout)


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq") and shutil.which("bc"),
    "Requires bash, jq and bc",
)
class RoundStakeDiagnosisTests(unittest.TestCase):
    """A bid cannot reach the elector for a round the DePool has no stake in,
    however well the wallet transaction was signed. That is not a signing
    failure and its remedy is a second stake tranche, so the message has to
    say which of the two happened."""

    def stake_for(self, rounds, election="1793318734"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            body = (
                'function Get_DP_Rounds() { printf "%s\\n" "$TEST_ROUNDS"; }\n'
                f'Get_DP_Round_Stake_For_Election 0:depool {election}; '
                'echo "rc=$?"\n'
            )
            return run_bash(root, body, TEST_ROUNDS=json.dumps({"rounds": rounds}))

    @staticmethod
    def round(elected_at, stake):
        return {"id": "3", "supposedElectedAt": str(elected_at), "stake": str(stake)}

    def test_the_round_being_elected_reports_its_own_stake(self):
        result = self.stake_for([self.round(1793318434, 50020000000000),
                                 self.round(1793318734, 0)])
        self.assertIn("rc=0", result.stdout)
        self.assertIn("0", result.stdout.splitlines()[0])

    def test_a_funded_round_reports_its_stake(self):
        result = self.stake_for([self.round(1793318734, 50000000000000)])
        self.assertIn("50000000000000", result.stdout)

    def test_a_round_the_depool_never_entered_reports_nothing(self):
        result = self.stake_for([self.round(1793318434, 1)])
        self.assertIn("rc=1", result.stdout)


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq") and shutil.which("bc"),
    "Requires bash, jq and bc",
)
class DePoolEnteredElectionTests(unittest.TestCase):
    """The pipeline holds four rounds and rotates them, so which position
    carries the round being elected depends on where the rotation has got to.
    Reading a position reported a DePool that had entered the election as one
    that had not."""

    def entered(self, rounds, election="1793321434"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            body = (
                'function Get_DP_Rounds() { printf "%s\\n" "$TEST_ROUNDS"; }\n'
                f'Effect_DePool_Set_For_Election 0:depool {election}; '
                'echo "rc=$?"\n'
            )
            return run_bash(root, body, TEST_ROUNDS=json.dumps({"rounds": rounds}))

    @staticmethod
    def round(identifier, elected_at):
        return {"id": str(identifier), "supposedElectedAt": str(elected_at),
                "stake": "0", "step": "1"}

    def test_the_round_is_found_wherever_it_sits_in_the_list(self):
        for position in range(4):
            rounds = [self.round(i, 0) for i in range(4)]
            rounds[position] = self.round(position, 1793321434)
            with self.subTest(position=position):
                self.assertIn("rc=0", self.entered(rounds).stdout)

    def test_a_depool_that_has_not_entered_is_not_claimed_to_have(self):
        rounds = [self.round(i, 0) for i in range(3)] + [self.round(3, 1793321134)]
        self.assertIn("rc=1", self.entered(rounds).stdout)

    def test_an_unreadable_depool_is_neither_yes_nor_no(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = run_bash(root, (
                "function Get_DP_Rounds() { return 1; }\n"
                'Effect_DePool_Set_For_Election 0:depool 1793321434; echo "rc=$?"\n'
            ))
        self.assertIn("rc=2", result.stdout)


class ElectionDiagnosisTests(unittest.TestCase):
    def test_a_zero_stake_round_is_named_as_the_cause(self):
        text = (SCRIPTS / "take_part_in_elections.sh").read_text()
        self.assertIn("Get_DP_Round_Stake_For_Election", text)
        self.assertIn("holds no stake in the round being elected", text)
        # A retry cannot put stake into a round that was elected empty, and
        # neither can staking now: a stake bids in the round that was
        # collecting when it was made. The advice has to name that round.
        self.assertIn("a retry cannot change it", text)
        self.assertIn("while it is the collecting one", text)
        self.assertIn("evernode depool info", text)
        self.assertNotIn("run 'evernode depool stake' again during the next round", text)


class ConfirmPathTests(unittest.TestCase):
    """Sign_Trans.sh is what `evernode depool confirm` runs."""

    def setUp(self):
        self.text = (SCRIPTS / "Sign_Trans.sh").read_text()

    def test_an_empty_wallet_is_an_error_and_not_a_success(self):
        """It exited 0 when the wallet held nothing, so a confirm that did
        nothing at all was reported as done."""
        self.assertIn("holds no pending transaction", self.text)
        empty = self.text.index("holds no pending transaction")
        self.assertIn("exit 1", self.text[empty:empty + 400])

    def test_the_named_id_is_looked_up_and_not_searched_for_as_text(self):
        self.assertNotIn('grep -q "${Trans_ID}"', self.text)
        self.assertIn("Get_Pending_Entry", self.text)

    def test_it_signs_through_the_one_handshake(self):
        self.assertIn("Sign_To_Execution", self.text)
        self.assertNotIn("SEND_ATTEMPTS", self.text)

    def test_the_blind_signing_loop_is_gone(self):
        """It sent every remaining signature and read the result once. Nothing
        can reach for it again if it does not exist."""
        managed = (SCRIPTS / "managed_ops.shinc").read_text()
        self.assertNotIn("Complete_MSIG_Trans_Signatures", managed)
        for path in sorted((SCRIPTS / "depool_functions").glob("*.shinc")):
            with self.subTest(operation=path.name):
                self.assertNotIn("Complete_MSIG_Trans_Signatures", path.read_text())

    def test_the_deadline_comes_from_the_transaction_itself(self):
        """A fixed attempt budget gave up with most of the transaction's valid
        life still ahead of it."""
        self.assertIn("Trans_Expires_At", self.text)


if __name__ == "__main__":
    unittest.main()
