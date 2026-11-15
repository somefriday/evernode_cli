"""Get_Account_Info reads through the node's console, the only transport this
installation uses: its network endpoint is not configured."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "evernode/vendor/ever-validator/scripts"
SAFE = "-1:" + "a" * 64
DEPOOL = "0:" + "b" * 64


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "Requires bash and jq")
class AccountReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        text = (SCRIPTS / "functions.shinc").read_text()
        start = text.index("function Get_Account_Info() {")
        cls.function = text[start: text.index("\n}\n", start) + len("\n}\n")]

    def read(self, node_workchain, account, answer='{"acc_type":"Active","balance":"7","last_paid":"1"}'):
        program = f"""set -uo pipefail
FORCE_USE_DAPP=false
NODE_WC={node_workchain}
log_error_stack() {{ :; }}
console() {{ echo console >&2; printf '%s' '{answer}'; }}
endpoint() {{ echo endpoint >&2; echo '{{}}'; }}
CALL_CONS=console
CALL_CLI=endpoint
{self.function}
Get_Account_Info {account}
"""
        return subprocess.run(["bash", "-c", program], capture_output=True, text=True, timeout=30)

    def test_both_workchains_are_read_through_the_console(self):
        """A DePool is always in workchain 0, also beside a masterchain Safe
        (setup --workchain -1): that read must not go to the endpoint."""
        for node_workchain in ("0", "-1"):
            for account in (SAFE, DEPOOL):
                with self.subTest(node_workchain=node_workchain, account=account[:3]):
                    result = self.read(node_workchain, account)
                    self.assertEqual(result.stderr.strip(), "console")
                    self.assertEqual(result.stdout.split(), ["Active", "7", "1"])

    def test_an_account_that_could_not_be_read_is_an_error_not_an_empty_one(self):
        """A console error used to read as balance 0 with success: a stake then
        looked gone from its account, and a proxy looked empty."""
        for answer, status in (
            ("Error: connection refused", 1),
            ("", 1),
            ('{"acc_type":"Nonexist"}', 0),
        ):
            with self.subTest(answer=answer):
                result = self.read("0", DEPOOL, answer)
                self.assertEqual(result.stdout.split(), ["None", "0", "0"])
                self.assertEqual(result.returncode, status)


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "Requires bash and jq")
class CustodianReadTests(unittest.TestCase):
    def custodians(self, custodians, parameters):
        text = (SCRIPTS / "functions.shinc").read_text()
        start = text.index("function Get_Account_Custodians_Info() {")
        function = text[start: text.index("\n}\n", start) + len("\n}\n")]
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        program = f"""ELECTIONS_WORK_DIR={directory} INPL_ELECTIONS_WORK_DIR={directory}
log_error_stack() {{ :; }}
Get_SC_current_state() {{ touch "$ELECTIONS_WORK_DIR/{SAFE[3:]}.boc"; }}
cli() {{ [[ $* == *getCustodians* ]] && echo '{custodians}' || echo '{parameters}'; }}
CALL_CLI=cli
{function}
Get_Account_Custodians_Info {SAFE}
"""
        return subprocess.run(["bash", "-c", program], capture_output=True, text=True, timeout=30)

    def test_the_counts_a_wallet_reports_are_returned(self):
        result = self.custodians('{"custodians":[1,2,3]}', '{"requiredTxnConfirms":"2"}')
        self.assertEqual((result.returncode, result.stdout.split()), (0, ["3", "2"]))

    def test_a_failed_read_is_not_taken_for_one_signature(self):
        """Every "-gt 1" test took "null" or nothing for a wallet that needs
        one signature: the bid was submitted and never signed."""
        for custodians, parameters in (("", ""), ("{}", "{}"), ('{"custodians":[1,2]}', "{}")):
            with self.subTest(custodians=custodians, parameters=parameters):
                result = self.custodians(custodians, parameters)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
