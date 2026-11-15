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

    def proxies(self, proxy0, proxy1):
        """Run the script's own proxy check; return how it ended, its top-ups and alerts."""
        functions = (SCRIPTS / "functions.shinc").read_text()
        start = functions.index("function Proxy_Storage_Owed() {")
        owed = functions[start: functions.index("\n}\n", start) + 3]
        loop = PREPARE[PREPARE.index("# Keep both proxies funded."):
                       PREPARE.index("# Check DePool has enough balance")]
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        program = f"""SelfScriptName=prepare_elections.sh elections_id=1 CURR_ELECTIONS_DIR={directory}
VALIDATOR_NAME=v Validator_addr=-1:wallet TOPUP_THRESHOLD=1920000000 dp_proxy0=-1:p0 dp_proxy1=-1:p1
Get_Account_Info() {{ [[ $1 == -1:p0 ]] && echo "$PROXY0" || echo "$PROXY1"; }}
top_app_account() {{ echo "top-up $1 $2"; }}
Send_msg_toTelBot() {{ echo "alert $2" >> {directory}/alerts; }}
{owed}
{loop}"""
        result = bash(program, PROXY0=proxy0, PROXY1=proxy1)
        alerts = Path(directory, "alerts")
        return (
            result,
            [line for line in result.stdout.splitlines() if line.startswith("top-up")],
            alerts.read_text().splitlines() if alerts.exists() else [],
        )

    def test_a_proxy_is_topped_up_by_what_it_holds_once_it_pays_its_storage(self):
        """A balance is as of the last transaction; storage owed since is taken
        at the next one, and in the masterchain owing over 0.1 token freezes it."""
        now, day = int(__import__("time").time()), 86400
        _, top_ups, _ = self.proxies(f"Active 3000000000 {now}", f"Active 3000000000 {now - 30 * day}")
        self.assertEqual(top_ups, ["top-up -1:p1 5000000000"], "only the one idle for 30 days")
        # one that owes more than it holds as well: top_app_account adds what it owes
        _, top_ups, _ = self.proxies(f"Active 1000000000 {now - 60 * day}", f"Active 3000000000 {now}")
        self.assertEqual(top_ups, ["top-up -1:p0 5000000000"])

    def test_an_unreadable_proxy_is_left_alone(self):
        now = int(__import__("time").time())
        result, top_ups, alerts = self.proxies("None 0 0", f"Active 3000000000 {now}")
        self.assertEqual((result.returncode, top_ups, alerts), (0, [], []))

    def test_a_frozen_proxy_stops_the_run_with_an_alarm(self):
        """No tick, so no bid: no stake goes through it until its state is restored."""
        now = int(__import__("time").time())
        result, top_ups, alerts = self.proxies(f"Frozen 3000000000 {now}", f"Active 1000000000 {now}")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(top_ups, [], "the run stops before the other proxy too")
        self.assertIn("PROXY 0 OF THE DEPOOL IS FROZEN:\n!!! -1:p0", result.stdout)
        self.assertIn("can be lost", result.stdout)
        self.assertEqual(len(alerts), 1)
        self.assertIn("Proxy0 -1:p0 is frozen", alerts[0])

    def test_a_failed_read_stops_the_run_instead_of_reading_as_nothing(self):
        """A balance read as 0 raised a false alert; a pending list read as empty
        skipped the stop at a half-signed transaction."""
        self.assertIn('if ! Validator_Acc_Info="$(Get_Account_Info "${Validator_addr}")";then', PREPARE)
        self.assertIn('if ! Trans_List="$(Get_MSIG_Trans_List "${Validator_addr}")";then', PREPARE)


if __name__ == "__main__":
    unittest.main()
