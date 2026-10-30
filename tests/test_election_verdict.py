"""The election path's verdict and its resume.

An election bid succeeded on chain and was reported as a failure: the script
read the wallet's pending list once, about a second before the confirmation was
included, saw one signature of two and declared the bid unsigned. It then
exited 0 anyway, so the caller saw success for a run that had printed an error.

Both halves are covered here -- the verdict now waits for the elector's own
participant list, and a retry finishes the bid already in the wallet instead of
adding another half-signed one.
"""
import base64
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "vendor/ever-validator/scripts"
MANAGED_OPS = SCRIPTS / "managed_ops.shinc"
TAKE_PART = SCRIPTS / "take_part_in_elections.sh"


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "Requires bash and jq")
class EngineADNLTests(unittest.TestCase):
    """The ADNL a bid registers under is the key console generated for it."""

    def run_lookup(self, keys, election_id):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "config.json").write_text(json.dumps({"validator_keys": keys}))
            program = (
                "set +u\n"
                'function echoerr() { printf "%s\\n" "$*" >&2; }\n'
                f'NODE_CFG_DIR="{directory}"\n'
                f'source "{MANAGED_OPS}"\n'
                f'Get_Engine_ADNL_For_Election {election_id}; echo "rc=$?"\n'
            )
            (directory / "case.sh").write_text(program)
            return subprocess.run(
                ["bash", str(directory / "case.sh")],
                capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=30,
            )

    @staticmethod
    def key(election_id, byte):
        return {
            "validator_adnl_key_id": base64.b64encode(bytes([byte]) * 32).decode(),
            "election_id": election_id,
        }

    def test_the_key_for_the_named_election_is_returned(self):
        result = self.run_lookup([self.key(100, 0xAB)], 100)
        self.assertIn("ab" * 32, result.stdout)
        self.assertIn("rc=0", result.stdout)

    def test_the_right_key_is_picked_from_a_ring_of_several(self):
        """A node keeps the keys of past elections, so position is not identity.
        The reference helper only ever looked at the first two entries."""
        keys = [self.key(100, 0x11), self.key(200, 0x22), self.key(300, 0x33)]
        result = self.run_lookup(keys, 300)
        self.assertIn("33" * 32, result.stdout)
        self.assertNotIn("11" * 32, result.stdout)

    def test_an_election_with_no_key_is_an_error_not_an_empty_answer(self):
        """Verifying a bid against an empty ADNL would ask the elector whether
        it knows nobody, which it always does."""
        result = self.run_lookup([self.key(100, 0x11)], 999)
        self.assertIn("rc=1", result.stdout)
        self.assertNotIn("rc=0", result.stdout)

    def test_an_empty_key_ring_is_an_error(self):
        result = self.run_lookup([], 100)
        self.assertIn("rc=1", result.stdout)

    def test_a_key_of_the_wrong_length_is_refused(self):
        key = {
            "validator_adnl_key_id": base64.b64encode(b"short").decode(),
            "election_id": 100,
        }
        result = self.run_lookup([key], 100)
        self.assertIn("rc=1", result.stdout)

    def test_a_non_numeric_election_id_is_refused(self):
        result = self.run_lookup([self.key(100, 0x11)], '"; echo pwned; "')
        self.assertNotIn("pwned", result.stdout)
        self.assertIn("rc=1", result.stdout)


@unittest.skipUnless(shutil.which("bash"), "Requires bash")
class ElectorEffectTests(unittest.TestCase):
    """Only the elector's participant list says a bid worked."""

    def run_predicate(self, status, output="absent"):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            program = (
                "set +u\n"
                'function echoerr() { printf "%s\\n" "$*" >&2; }\n'
                f'function Elector_ADNL_Search() {{ echo "{output}"; return {status}; }}\n'
                f'source "{MANAGED_OPS}"\n'
                'Effect_Elector_Has_Our_ADNL ' + "ab" * 32 + '; echo "rc=$?"\n'
            )
            (directory / "case.sh").write_text(program)
            return subprocess.run(
                ["bash", str(directory / "case.sh")],
                capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=30,
            )

    def test_a_registered_stake_is_the_effect(self):
        result = self.run_predicate(0, "100000000000 0 3 0xabc")
        self.assertIn("rc=0", result.stdout)

    def test_an_absent_adnl_is_not_yet_rather_than_unreadable(self):
        """A bid in flight is the ordinary case and must keep the wait going."""
        result = self.run_predicate(4, "absent")
        self.assertIn("rc=1", result.stdout)

    def test_closed_elections_are_not_yet_rather_than_unreadable(self):
        result = self.run_predicate(3, "null")
        self.assertIn("rc=1", result.stdout)

    def test_an_unreadable_elector_is_distinguished_from_an_absent_bid(self):
        """"The elector did not answer" and "the bid is not registered" are
        different operator problems, so they must not share a verdict."""
        for status in (1, 2, 5):
            with self.subTest(elector_status=status):
                result = self.run_predicate(status)
                self.assertIn("rc=2", result.stdout)

    def test_the_predicate_does_not_leak_the_elector_reply_to_stdout(self):
        result = self.run_predicate(0, "100000000000 0 3 0xabc")
        self.assertNotIn("0xabc", result.stdout)


class ElectionScriptInvariantTests(unittest.TestCase):
    """What the traced failure means this script must no longer do."""

    @classmethod
    def setUpClass(cls):
        cls.text = TAKE_PART.read_text()
        cls.code = "\n".join(
            line
            for line in cls.text.splitlines()
            if not line.lstrip().startswith("#")
        )

    def test_the_verdict_is_not_a_single_read_of_the_pending_list(self):
        self.assertNotIn("was NOT signed by all keys", self.text)
        self.assertIn("Await_Effect", self.text)
        self.assertIn("Effect_Elector_Has_Our_ADNL", self.text)

    def test_the_transaction_is_not_chosen_by_taking_the_last_one_listed(self):
        """Every DePool operation targets the same address, so counting entries
        to that destination and taking the newest can pick someone else's."""
        self.assertNotIn("tail -n 1", self.text)

    def test_the_wait_is_bounded_by_the_elections_closing(self):
        """A bid that registers after the close cannot be elected, so the
        wallet's hour-long expiry is the wrong bound here."""
        self.assertIn("elections_id - elections_end_before", self.code)

    def test_the_wait_never_ends_before_the_bid_can_land(self):
        """A stalled network keeps its election open long past that nominal
        close, and the elector still accepts bids there. Reporting failure the
        instant after submitting, because a date in the past said so, would be
        the same race with a different cause."""
        self.assertIn("2 * ", self.code)
        self.assertIn("Bid_Deadline=", self.code)

    def test_the_keys_to_confirm_with_come_from_the_wallet_not_from_a_count(self):
        """Whichever custodian submitted has already signed, and confirming
        with that key again is rejected before tvm.accept() - leaving nothing
        in any block to explain it. The wallet names who still owes a
        signature, so no loop over key-file indices can be right."""
        self.assertNotIn("for (( i=0; i < LocalKeysQty", self.text)
        self.assertNotIn("for (( i=1; i < LocalKeysQty", self.text)
        self.assertIn("Sign_To_Execution", self.code)

    def test_a_retry_resumes_the_bid_already_in_the_wallet(self):
        """Otherwise each attempt adds another half-signed bid, and five from
        one custodian fill the queue and stop the node bidding at all."""
        self.assertIn("Find_Matching_Pending_Trans", self.text)
        resume = self.text.index("Find_Matching_Pending_Trans")
        submit = self.text.index("Submit_And_Identify")
        self.assertLess(resume, submit, "the resume must precede the submit")

    def test_the_bid_is_identified_by_content_and_by_our_own_key(self):
        """Send_Message returned the newest pending entry addressed to the
        destination, and every operation of every custodian targets that same
        address, so the signatures could have gone to someone else's
        transaction."""
        self.assertNotIn("Send_Message ", self.code)
        self.assertIn('"${MSIG_KEY_FILES_ARRAY[0]}"', self.code)

    def test_a_failed_bid_no_longer_exits_zero(self):
        """It printed an error and exited 0, so a caller saw success."""
        self.assertTrue(self.text.rstrip().endswith("exit 1"), self.text[-200:])
        self.assertIn("is not registered with the elector", self.text)

    def test_the_sync_guard_cannot_read_an_error_string_as_a_lag_of_zero(self):
        """`[[ "Node Down" -gt 20 ]]` is false in bash, so a dead node passed
        the masterchain check; and shard lag was warned about and ignored."""
        self.assertNotIn("MC_TIME_DIFF -gt", self.text)
        self.assertNotIn("SH_TIME_DIFF -gt", self.text)
        self.assertIn('Require_Node_Console "$TIMEDIFF_MAX"', self.code)


@unittest.skipUnless(shutil.which("bash"), "Requires bash")
class AccountBalanceEffectTests(unittest.TestCase):
    """Verifying a top-up against the same reading that asked for it."""

    def run_predicate(self, info, expected, fails=False):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            body = "return 1" if fails else f'echo "{info}"'
            program = (
                "set +u\n"
                'function echoerr() { printf "%s\\n" "$*" >&2; }\n'
                f"function Get_Account_Info() {{ {body}; }}\n"
                f'source "{MANAGED_OPS}"\n'
                f'Effect_Account_Balance_At_Least 0:depool {expected}; echo "rc=$?"\n'
            )
            (directory / "case.sh").write_text(program)
            return subprocess.run(
                ["bash", str(directory / "case.sh")],
                capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=30,
            )

    def test_a_balance_at_the_floor_is_the_effect(self):
        result = self.run_predicate("Active 20000000000 1480000001", 20000000000)
        self.assertIn("rc=0", result.stdout)

    def test_a_balance_below_the_floor_is_not_yet(self):
        result = self.run_predicate("Active 19000000000 1480000001", 20000000000)
        self.assertIn("rc=1", result.stdout)

    def test_a_missing_account_is_unreadable_rather_than_empty(self):
        """Get_Account_Info answers "None 0 0" for an account it cannot read,
        and a zero balance is not the same statement as a failed read."""
        result = self.run_predicate("None 0 0", 20000000000)
        self.assertIn("rc=1", result.stdout)

    def test_a_failed_read_is_distinguished(self):
        result = self.run_predicate("", 20000000000, fails=True)
        self.assertIn("rc=2", result.stdout)


class ElectionPreparationTests(unittest.TestCase):
    """The automatic DePool top-up could never be signed.

    The reference code counted pending transactions to the DePool and asked
    whether the count had *fallen* after submitting, while a submit makes it
    rise -- and the block is only reached when that count is zero, so the test
    could never be true. The top-up was submitted, never signed, and the
    half-signed transfer it left then made every later run exit at the
    unsigned-transaction check, so a DePool that dipped below its threshold
    stopped the node bidding until someone cleared the wallet by hand.
    """

    @classmethod
    def setUpClass(cls):
        cls.text = (SCRIPTS / "prepare_elections.sh").read_text()
        # Comments here describe the defect, so the checks read code only.
        cls.code = "\n".join(
            line
            for line in cls.text.splitlines()
            if not line.lstrip().startswith("#")
        )
        # The top-up block, bounded so the tik section below it is not read.
        span = cls.text[
            cls.text.index("Send_File_To_BC") : cls.text.index("Send TIK query")
        ]
        cls.block = "\n".join(
            line for line in span.splitlines() if not line.lstrip().startswith("#")
        )

    def test_the_inverted_comparison_is_gone(self):
        # `Exist_DP_Trans_Qty -gt 0` elsewhere is the legitimate guard against
        # running with an unsigned transaction; the defect was comparing it
        # against the count taken after the submit.
        self.assertNotIn("New_DP_Trans_Qty", self.code)
        self.assertNotIn("Exist_DP_Trans_Qty -gt $", self.code)

    def test_the_top_up_is_identified_by_content_not_by_counting(self):
        self.assertIn("Find_Matching_Pending_Trans", self.block)
        self.assertNotIn("tail -n 1", self.block)

    def test_the_top_up_waits_for_the_depool_balance(self):
        self.assertIn("Effect_Account_Balance_At_Least", self.block)

    def test_an_unsigned_top_up_is_a_failure_not_a_warning(self):
        """It used to print an error and carry on into the election, which is
        what this block exists to prevent."""
        self.assertIn("nothing was transferred", self.text)
        self.assertIn("exit 1", self.block)

    def test_a_single_signature_wallet_does_not_look_for_a_pending_entry(self):
        lookup = self.block.index("Find_Matching_Pending_Trans")
        guard = self.block.index("${Required_Signs} -gt 1")
        self.assertLess(guard, lookup)

    def test_the_sync_guard_replaced_the_hand_rolled_check(self):
        self.assertIn('Require_Node_Console "$TIMEDIFF_MAX"', self.code)
        self.assertNotIn("MC_TIME_DIFF=888", self.code)


if __name__ == "__main__":
    unittest.main()
