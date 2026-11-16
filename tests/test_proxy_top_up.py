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

    def top_up(self, *, before=(), after_send=(), required=2, idle_days=0, landed=7000000000, lands=True,
               status="Active"):
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
log_error_stack() {{ echo "$*" >&2; }}
echoerr() {{ echo "$*" >&2; }}
sleep() {{ :; }}
Get_Account_Custodians_Info() {{ echo "3 {required}"; }}
Custodian_Key_Decimal() {{ echo {OURS}; }}
Get_MSIG_Expiration_Time() {{ echo 3600; }}
Get_MSIG_Trans_List() {{ cat "$DIR/pending.json"; }}
Get_Account_Info() {{
    [[ -f $DIR/executed ]] && echo "Active {landed} $(date +%s)" || echo "{status} 2000000000 $(( $(date +%s) - {idle_days} * 86400 ))"
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
        lifetime = (directory / "lifetime").read_text().strip() if (directory / "lifetime").exists() else ""
        self.message = (directory / "message").read_text() if (directory / "message").exists() else ""
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

    def test_a_last_paid_of_zero_is_unknown_not_decades_of_storage(self):
        """0 is what a failed read reports; counted as a time, it owed about
        2,000 tokens, all of which the top-up would have carried."""
        program = self.function + '\nfor paid in 0 "" null; do Proxy_Storage_Owed "$paid"; done\n'
        result = subprocess.run(["bash", "-c", program], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.stdout.split(), ["0", "0", "0"])

    def test_a_proxy_owing_more_than_a_top_up_carries_gets_nothing(self):
        """Idle 2,000 days, it owes about 200 tokens by the estimate. A 55-token
        transfer would be credited and then taken in full, and the proxy frozen
        by it, so nothing is sent and the amount it needs is named."""
        result, trace, _ = self.top_up(required=1, idle_days=2000)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((trace, self.message), ([], ""))
        self.assertIn("owes about 200 tokens", result.stderr)
        self.assertIn("Send it 205 tokens", result.stderr)

    def test_up_to_the_cap_the_storage_owed_travels_with_the_top_up(self):
        result, _, _ = self.top_up(required=1, idle_days=500, landed=57000000000)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"value":55000000000,"bounce":false', self.message)

    def test_nothing_is_sent_to_a_proxy_that_is_not_active(self):
        """A transfer that does not bounce would stay in a frozen proxy without
        unfreezing it."""
        for status in ("Frozen", "", "null"):
            with self.subTest(status=status):
                result, trace, _ = self.top_up(required=1, status=status)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((trace, self.message), ([], ""))

    def test_a_single_signature_wallet_is_confirmed_by_the_proxys_balance(self):
        result, trace, _ = self.top_up(required=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(trace, ["sent"])



def p18(*entries):
    """The node console's `getconfig 18` answer: (utime_since, mc bit price, mc cell price)."""
    return json.dumps({"p18": [
        {"utime_since": since, "bit_price_ps_dec": "1", "cell_price_ps_dec": "500",
         "mc_bit_price_ps_dec": str(bit), "mc_cell_price_ps_dec": str(cell)}
        for since, bit, cell in entries]})


MAINNET = (0, 1000, 500000)


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("bc"), "Requires bash, jq and bc")
class ProxyStorageOwedTests(unittest.TestCase):
    """A proxy's storage debt is counted at the masterchain prices in force now,
    never as less than the 0.1 token a day assumed before the prices were read.
    A proxy that owes more than it holds is frozen by the DePool's next message."""

    @classmethod
    def setUpClass(cls):
        text = (SCRIPTS / "functions.shinc").read_text()
        start = text.index("function Current_MC_Storage_Prices() {")
        cls.functions = text[start: text.index("\n}\n", text.index("function Proxy_Storage_Owed() {")) + 3]

    def owed(self, answer, idle=86400, dapp="false"):
        with tempfile.TemporaryDirectory() as directory:
            reply = Path(directory) / "p18.json"
            reply.write_text(answer)
            program = (
                f'console() {{ [[ "$*" == "-jc getconfig 18" ]] && cat "{reply}"; }}\n'
                f"CALL_CONS=console CALL_BC=bc FORCE_USE_DAPP={dapp}\n"
                + self.functions
                + f'\nProxy_Storage_Owed $(( $(date +%s) - {idle} ))\n'
            )
            result = subprocess.run(["bash", "-c", program], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return int(result.stdout)

    def test_at_mainnet_prices_the_floor_decides(self):
        # A proxy costs about 0.044 token a day there; 0.1 a day is counted.
        self.assertAlmostEqual(self.owed(p18(MAINNET)), 100_000_000, delta=10_000)

    def test_higher_prices_raise_the_debt(self):
        # A hundred times mainnet's prices: 13000 bits and 40 cells for a day.
        expected = -(-86400 * (13000 * 100_000 + 40 * 50_000_000) // 65536)
        self.assertAlmostEqual(self.owed(p18((0, 100_000, 50_000_000))), expected, delta=expected // 1000)

    def test_only_prices_in_force_count(self):
        """An entry dated in the future is not in force yet."""
        future = (int(time.time()) + 86400, 45_000_000, 22_500_000_000)
        self.assertAlmostEqual(self.owed(p18(MAINNET, future)), 100_000_000, delta=10_000)
        started = (int(time.time()) - 3600, 100_000, 50_000_000)
        self.assertGreater(self.owed(p18(MAINNET, started)), 4 * 10**9)

    def test_prices_that_cannot_be_read_leave_the_floor(self):
        self.assertAlmostEqual(self.owed(""), 100_000_000, delta=10_000)
        self.assertAlmostEqual(self.owed(p18((0, 100_000, 50_000_000)), dapp="true"), 100_000_000, delta=10_000)

    def test_a_debt_past_bash_arithmetic_is_capped_far_over_any_top_up(self):
        self.assertEqual(self.owed(p18((0, 10**15, 10**15)), idle=10**8), 999999999999999999)


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "Requires bash and jq")
class TopUpBeforeUseTests(unittest.TestCase):
    """evernode tops the DePool's proxies up before election start, depool stake
    and stake-initial, with the rule each run's preparation applies before the
    tick: under 1.92 tokens once the storage owed is paid, 5 tokens more."""

    def run_top_up(self, accounts, owed=0, fails=False):
        from unittest.mock import patch
        from evernode import scripts
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        (directory / "env.sh").write_text("")
        lines = "\n".join(f'    {address}) echo "{reply}";;' for address, reply in accounts.items())
        (directory / "functions.shinc").write_text(
            f"function Get_Account_Info() {{\n  case $1 in\n{lines}\n    *) echo 'None 0 0'; return 1;;\n  esac\n}}\n"
            f"function Proxy_Storage_Owed() {{ echo {owed}; }}\n"
            f'function top_app_account() {{ echo "$1 $2 $3" >> "{directory}/sent"; '
            + ('echo "###-ERROR: the Safe holds too little"; return 1; }\n' if fails else "}\n")
        )
        with patch("evernode.scripts._import_verification_context", return_value=directory):
            entries = scripts.top_up_proxies(None, {"name": "dp"}, list(accounts), WALLET)
        sent = (directory / "sent").read_text().splitlines() if (directory / "sent").exists() else []
        return entries, sent

    def test_a_proxy_short_of_the_threshold_gets_five_tokens(self):
        low, high = "-1:" + "1" * 64, "-1:" + "2" * 64
        entries, sent = self.run_top_up({low: "Active 1919999999 1", high: "Active 1920000000 1"})
        self.assertEqual([e["top_up"] for e in entries], ["done", "none"])
        self.assertEqual(sent, [f"{low} 5000000000 {WALLET}"])

    def test_the_storage_owed_is_paid_first(self):
        proxy, other = "-1:" + "1" * 64, "-1:" + "2" * 64
        entries, sent = self.run_top_up({proxy: "Active 3000000000 1", other: "Active 9000000000 1"},
                                        owed=1500000000)
        self.assertEqual([e["top_up"] for e in entries], ["done", "none"])

    def test_nothing_is_sent_to_a_proxy_that_is_not_active(self):
        frozen, active = "-1:" + "1" * 64, "-1:" + "2" * 64
        entries, sent = self.run_top_up({frozen: "Frozen 0 1", active: "Active 5000000000 1"})
        self.assertEqual(([e["top_up"] for e in entries], sent), (["none", "none"], []))

    def test_a_failed_top_up_comes_back_with_the_kits_reason(self):
        proxy, other = "-1:" + "1" * 64, "-1:" + "2" * 64
        entries, _ = self.run_top_up({proxy: "Active 1000000000 1", other: "Active 9000000000 1"}, fails=True)
        self.assertEqual(entries[0]["top_up"], "failed")
        self.assertIn("###-ERROR: the Safe holds too little", entries[0]["errors"])
        self.assertEqual(entries[1]["top_up"], "none")


if __name__ == "__main__":
    unittest.main()
