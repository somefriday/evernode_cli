"""The wallet queue limit, which has to be checked before sending.

SafeMultisigWallet allows five live transactions per submitting custodian and
rejects the sixth with error 113. Every require() in submitTransaction runs
before tvm.accept(), so that rejection is recorded in no block: the operation
has no effect and nothing on chain says why. Checking afterwards is therefore
impossible, and the entries that fill the queue are this node's own orphans.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "vendor/ever-validator/scripts"
MANAGED_OPS = SCRIPTS / "managed_ops.shinc"

# The custodian from the recorded incident: its pubkey and the decimal string
# the wallet reports as `creator` for the transactions it submitted.
CUSTODIAN_HEX = "8a1dd36660a72048f117f8908e39c2a1f3abb68e54907ab10f51b6ef4d2b6d33"
CUSTODIAN_DECIMAL = (
    "62471870697941799294731047801082795832357362990714514176758121630865889652019"
)


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq") and shutil.which("bc"),
    "Requires bash, jq and bc",
)
class QueueSpaceTests(unittest.TestCase):
    def run_guard(self, *, entries, public=CUSTODIAN_HEX, key_file=True,
                  list_fails=False):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            keys = directory / "validator_1.keys.json"
            if key_file:
                keys.write_text(json.dumps({"public": public, "secret": "bb" * 32}))
            (directory / "pending.json").write_text(
                json.dumps({"transactions": entries})
            )
            fail = "return 2" if list_fails else 'cat "$TEST_ROOT/pending.json"'
            program = (
                "set +u\n"
                'function echoerr() { printf "%s\\n" "$*" >&2; }\n'
                "CALL_BC=bc\n"
                f'TEST_ROOT="{directory}"\n'
                f'function Get_MSIG_Trans_List() {{ {fail}; }}\n'
                f'source "{MANAGED_OPS}"\n'
                f'Require_Queue_Space 0:wallet "{keys}"; echo "rc=$?"\n'
            )
            (directory / "case.sh").write_text(program)
            return subprocess.run(
                ["bash", str(directory / "case.sh")],
                capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=30,
            )

    @staticmethod
    def entry(creator=CUSTODIAN_DECIMAL, identifier="1"):
        return {"id": identifier, "creator": creator, "dest": "0:depool"}

    def test_the_hex_key_is_converted_to_the_decimal_the_wallet_reports(self):
        """A 256-bit key is far beyond jq's and the shell's arithmetic, so the
        conversion is the whole reason this needs bc."""
        with tempfile.TemporaryDirectory() as temporary:
            keys = Path(temporary) / "k.json"
            keys.write_text(json.dumps({"public": CUSTODIAN_HEX}))
            program = (
                "set +u\nCALL_BC=bc\n"
                'function echoerr() { printf "%s\\n" "$*" >&2; }\n'
                f'source "{MANAGED_OPS}"\n'
                f'Custodian_Key_Decimal "{keys}"\n'
            )
            path = Path(temporary) / "case.sh"
            path.write_text(program)
            result = subprocess.run(
                ["bash", str(path)], capture_output=True, text=True, timeout=30
            )
        self.assertEqual(result.stdout.strip(), CUSTODIAN_DECIMAL)

    def test_an_empty_queue_has_room(self):
        result = self.run_guard(entries=[])
        self.assertIn("rc=0", result.stdout)
        self.assertIn("0 of 5", result.stdout)

    def test_four_entries_still_leave_room(self):
        result = self.run_guard(entries=[self.entry(identifier=str(i)) for i in range(4)])
        self.assertIn("rc=0", result.stdout)
        self.assertIn("4 of 5", result.stdout)

    def test_a_full_queue_is_refused_before_anything_is_sent(self):
        result = self.run_guard(entries=[self.entry(identifier=str(i)) for i in range(5)])
        self.assertIn("rc=1", result.stdout)
        self.assertIn("nothing was sent", result.stderr)
        self.assertIn("depool confirm", result.stderr)

    def test_the_limit_counts_only_this_custodians_entries(self):
        """The limit is per submitting custodian, not per wallet, so another
        custodian's queue must not block this one."""
        others = [self.entry(creator="123456789", identifier=str(i)) for i in range(5)]
        result = self.run_guard(entries=others)
        self.assertIn("rc=0", result.stdout)
        self.assertIn("0 of 5", result.stdout)

    def test_a_mixed_queue_counts_correctly(self):
        entries = [self.entry(identifier="a"), self.entry(creator="99", identifier="b"),
                   self.entry(identifier="c")]
        result = self.run_guard(entries=entries)
        self.assertIn("2 of 5", result.stdout)

    def test_a_missing_key_file_refuses_rather_than_assuming_room(self):
        result = self.run_guard(entries=[], key_file=False)
        self.assertIn("rc=1", result.stdout)
        self.assertIn("Cannot read the submitting custodian", result.stderr)

    def test_a_malformed_public_key_refuses_rather_than_assuming_room(self):
        for public in ("", "0x" + CUSTODIAN_HEX, "zz" * 32, CUSTODIAN_HEX[:-1]):
            with self.subTest(public=public):
                result = self.run_guard(entries=[], public=public)
                self.assertIn("rc=1", result.stdout)

    def test_an_unreadable_wallet_refuses_rather_than_assuming_room(self):
        """Fail closed: a queue that could not be read may well be full, and a
        submit into a full one disappears without a trace."""
        result = self.run_guard(entries=[], list_fails=True)
        self.assertIn("rc=1", result.stdout)


class QueueGuardWiringTests(unittest.TestCase):
    """Every submit this CLI drives has to pass the guard."""

    def test_each_depool_operation_checks_before_submitting(self):
        for name in ("ordinary_stake", "remove_stake", "withdraw_part_stake",
                     "withdraw_entire_stake", "cancel_withdrawal",
                     "replenish_depool"):
            path = SCRIPTS / "depool_functions" / f"{name}.shinc"
            with self.subTest(operation=name):
                text = path.read_text()
                self.assertIn("Require_Queue_Space", text)
                self.assertLess(
                    text.index("Require_Queue_Space"),
                    text.index("Submit_And_Identify"),
                    "the guard must precede the submit",
                )

    def test_the_election_bid_checks_before_submitting(self):
        text = (SCRIPTS / "take_part_in_elections.sh").read_text()
        self.assertIn("Require_Queue_Space", text)
        self.assertLess(
            text.index("Require_Queue_Space"), text.index("Submit_And_Identify")
        )

    def test_the_limit_matches_the_contract(self):
        contract = (
            SCRIPTS.parent / "contracts/EverX/safemultisig/SafeMultisigWallet.sol"
        ).read_text()
        self.assertIn("MAX_QUEUED_REQUESTS = 5", contract)
        self.assertIn(
            "MSIG_MAX_QUEUED_REQUESTS=5", MANAGED_OPS.read_text().replace(" ", "")
        )


if __name__ == "__main__":
    unittest.main()
