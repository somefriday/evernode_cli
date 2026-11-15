"""Get_Account_Info reads through the node's console, the only transport this
installation uses: its network endpoint is not configured."""

from pathlib import Path
import shutil
import subprocess
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


if __name__ == "__main__":
    unittest.main()
