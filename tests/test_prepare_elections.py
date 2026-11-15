"""prepare_elections.sh: the DePool top-up, the no-election case and how it stops."""

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "evernode/vendor/ever-validator/scripts"
PREPARE = (SCRIPTS / "prepare_elections.sh").read_text()


def bash(program, *arguments, **environment):
    return subprocess.run(
        ["bash", "-c", program, "test", *arguments],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60,
        env={"PATH": "/usr/bin:/bin", **environment},
    )


@unittest.skipUnless(shutil.which("bash"), "Requires bash")
class PrepareElectionsTests(unittest.TestCase):
    def test_the_depool_top_up_confirms_the_transaction_it_signed(self):
        """A two-signature Safe's top-up was refused after being signed: the
        confirmation was given no transaction id."""
        self.assertIn('Complete_And_Await "$DP_Trans_ID" "$Validator_addr"', PREPARE)
        program = r"""set -uo pipefail
source "$MANAGED_OPS"
echoerr() { echo "$*" >&2; }
Get_MSIG_Expiration_Time() { echo 3600; }
Sign_To_Execution() { echo "signed:$3"; }
Effect_Ok() { return 0; }
Required_Signs=2
Complete_And_Await "$1" 0:wallet validator01 "The DePool top-up" Effect_Ok
"""
        managed = str(SCRIPTS / "managed_ops.shinc")
        transaction = str(int(__import__("time").time()) << 32)
        signed = bash(program, transaction, MANAGED_OPS=managed)
        self.assertEqual(signed.returncode, 0, signed.stderr)
        self.assertEqual(signed.stdout.splitlines()[0], "signed:" + transaction)
        refused = bash(program, "", MANAGED_OPS=managed)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("has no transaction id to confirm", refused.stderr)

    def test_with_no_election_open_it_writes_in_the_elections_directory(self):
        start = PREPARE.index("declare -i elections_id=$(Get_Current_Elections_ID)")
        block = PREPARE[start: PREPARE.index("\nfi\n", start) + len("\nfi\n")]
        program = 'ID="$1"; Get_Current_Elections_ID() { echo "$ID"; }\n' + block + (
            'echo "$CURR_ELECTIONS_DIR $INPL_CURR_ELECTIONS_DIR"\n'
        )
        with tempfile.TemporaryDirectory() as work:
            for election, expected in (("0", f"{work} /ever-node/elections"),
                                       ("1793451980", f"{work}/1793451980 /ever-node/elections/1793451980")):
                with self.subTest(election=election):
                    result = bash(program, election, ELECTIONS_WORK_DIR=work,
                                  INPL_ELECTIONS_WORK_DIR="/ever-node/elections")
                    self.assertEqual(result.stdout.splitlines()[-1], expected, result.stderr)

    def test_a_failed_step_stops_the_script(self):
        """It defines no function, so a return would only print an error and go
        on to send a missing or stale message; and FUNCNAME would be empty."""
        self.assertIsNone(re.search(r"(?m)^\s*function\s|^\s*\w+\(\)\s*\{", PREPARE))
        self.assertIsNone(re.search(r"(?m)^\s*return\b", PREPARE))
        self.assertNotIn("FUNCNAME", PREPARE)

    def test_only_a_proxy_that_reads_as_active_is_topped_up(self):
        """One that could not be read shows a balance of 0."""
        for proxy in ("Proxy0", "Proxy1"):
            with self.subTest(proxy=proxy):
                self.assertIn(
                    f'if [[ ${{{proxy}_Info%% *}} == "Active" && ${proxy}_Bal -lt $TOPUP_THRESHOLD ]];then',
                    PREPARE,
                )

    def test_a_failed_read_stops_the_run_instead_of_reading_as_nothing(self):
        """A balance read as 0 raised a false alert; a pending list read as empty
        skipped the stop at a half-signed transaction."""
        self.assertIn('if ! Validator_Acc_Info="$(Get_Account_Info "${Validator_addr}")";then', PREPARE)
        self.assertIn('if ! Trans_List="$(Get_MSIG_Trans_List "${Validator_addr}")";then', PREPARE)


if __name__ == "__main__":
    unittest.main()
