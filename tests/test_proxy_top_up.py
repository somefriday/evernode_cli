"""A proxy top-up signs only the transaction it submitted itself: recognised by
its content and this node's key, never by its place in the wallet's list."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "evernode/vendor/ever-validator/scripts"
PROXY = "0:" + "c" * 64
WALLET = "0:" + "d" * 64
OURS, OTHER = "111", "222"  # the submitting custodians' keys, as the wallet lists them


def pending(id_, creator, dest=PROXY, value="5000000000", bounce=False):
    """A pending transfer; this node's top-ups do not bounce."""
    return {"id": str(id_), "creator": creator, "dest": dest, "value": value,
            "payload": "te6ccgEBAQEAAgAAAA==", "bounce": bounce, "sendFlags": "3"}


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "Requires bash and jq")
class ProxyTopUpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        text = (SCRIPTS / "functions.shinc").read_text()
        owed = text.index("function Proxy_Storage_Owed() {")
        start = text.index("function top_app_account () {")
        cls.function = (
            text[owed: text.index("\n}\n", owed) + 3]
            + text[start: text.index("} # top_app_account", start)] + "}"
        )

    def top_up(self, *, before=(), after_send=(), required=2, idle_days=0, landed=7000000000, lands=True):
        """Top up the proxy; the wallet lists BEFORE, and AFTER_SEND once the
        message is sent. IDLE_DAYS: how long the proxy has had no transaction;
        LANDED: its balance once the top-up arrived, if it LANDS."""
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        (directory / "pending.json").write_text(json.dumps({"transactions": list(before)}))
        (directory / "landed.json").write_text(json.dumps({"transactions": list(after_send)}))
        program = f"""source "{SCRIPTS / 'managed_ops.shinc'}"
DIR={directory}
MSG_LIFETIME=60 LC_Send_MSG_Timeout=10 EFFECT_WAIT_MAX=2 EFFECT_POLL_INTERVAL=1
ELECTIONS_WORK_DIR=$DIR INPL_ELECTIONS_WORK_DIR=$DIR KEYS_DIR=$DIR VALIDATOR_NAME=v
log_error_stack() {{ :; }}
echoerr() {{ echo "$*" >&2; }}
sleep() {{ :; }}
Get_Account_Custodians_Info() {{ echo "3 {required}"; }}
Custodian_Key_Decimal() {{ echo {OURS}; }}
Get_MSIG_Expiration_Time() {{ echo 3600; }}
Get_MSIG_Trans_List() {{ cat "$DIR/pending.json"; }}
Get_Account_Info() {{
    [[ -f $DIR/executed ]] && echo "Active {landed} $(date +%s)" || echo "Active 2000000000 $(( $(date +%s) - {idle_days} * 86400 ))"
}}
cli() {{ echo "$*" > "$DIR/message"; while [[ $# -gt 0 ]]; do [[ $1 == --output ]] && : > "$2"; [[ $1 == --lifetime ]] && echo "$2" > "$DIR/lifetime"; shift; done; }}
CALL_CLI=cli
Send_File_To_BC() {{ echo sent >> "$DIR/trace"; cp "$DIR/landed.json" "$DIR/pending.json"; [[ {required} -le 1 && {str(lands).lower()} == true ]] && touch "$DIR/executed"; echo success; }}
Sign_To_Execution() {{ echo "signed $3" >> "$DIR/trace"; touch "$DIR/executed"; }}
SCRIPT_DIR=$DIR
{self.function}
top_app_account {PROXY} 5000000000 {WALLET}
"""
        # The function this replaced signed through Sign_Trans.sh, so a regression shows here too.
        signer = directory / "Sign_Trans.sh"
        signer.write_text(f'#!/bin/bash\necho "signed $2" >> "{directory}/trace"\n')
        signer.chmod(0o755)
        result = subprocess.run(["bash", "-c", program], capture_output=True, text=True, timeout=60)
        trace = directory / "trace"
        lifetime = (directory / "lifetime").read_text().strip()
        self.message = (directory / "message").read_text()
        return result, trace.read_text().split("\n")[:-1] if trace.exists() else [], lifetime

    def test_another_custodians_transfer_is_never_signed(self):
        """The review's case: an unrelated 50,000-token transfer appears during the
        top-up, listed last; and one exactly like ours, submitted by another key."""
        now = int(time.time()) << 32
        others = [pending(now | 2, OTHER), pending(now | 3, OTHER, WALLET, "50000000000000")]
        result, trace, _ = self.top_up(after_send=others)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([line for line in trace if line.startswith("signed")], [])

    def test_its_own_top_up_is_signed_wherever_it_is_listed(self):
        now = int(time.time()) << 32
        landed = [pending(now | 1, OURS), pending(now | 3, OTHER, WALLET, "50000000000000")]
        result, trace, lifetime = self.top_up(after_send=landed)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(trace, ["sent", f"signed {now | 1}"])
        self.assertEqual(lifetime, "60")
        # credited before the storage owed is taken (ever-executor: credit_first is !bounce)
        self.assertIn('"value":5000000000,"bounce":false', self.message)

    def test_a_top_up_an_earlier_run_left_pending_is_completed_not_sent_again(self):
        """Also covers one that arrived after that run had given up on it."""
        earlier = (int(time.time()) - 600) << 32 | 1
        result, trace, _ = self.top_up(before=[pending(earlier, OURS)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(trace, [f"signed {earlier}"])

    def test_a_top_up_also_carries_the_storage_owed(self):
        """A proxy idle for 30 days owes 3 tokens by the estimate: the transfer
        carries 5 + 3, and arrives as 2 + 8 less the 1.3 really taken."""
        result, trace, _ = self.top_up(after_send=[], required=1, idle_days=30, landed=8700000000)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"value":8000000000,"bounce":false', self.message)

    def test_a_top_up_that_never_arrives_is_not_reported_done(self):
        """A one-signature Safe has no transaction to follow: the proxy's balance
        is the only proof. A floor below what an idle proxy already shows would
        call a lost top-up done."""
        result, _, _ = self.top_up(required=1, idle_days=45, lands=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_a_single_signature_wallet_is_confirmed_by_the_proxys_balance(self):
        result, trace, _ = self.top_up(required=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(trace, ["sent"])


if __name__ == "__main__":
    unittest.main()
