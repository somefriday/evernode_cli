"""Run import adapters with the real vendor scripts and simulated node tools."""

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from evernode import configuration, process, scripts, storage, wallets
from support import make_node_configuration


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq"), "Requires bash and jq"
)
class ImportScriptTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(os.umask, os.umask(0o077))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = storage.NodeStateStore(temporary.name)
        self.config = make_node_configuration("validator01")
        self.root = self.store.node_directory(self.config["name"])
        self.script_dir = self.root / "scripts"
        self.script_dir.mkdir(parents=True)
        (self.root / "elections").mkdir()
        vendor = Path(__file__).resolve().parents[1] / "vendor/ever-validator/scripts"
        for filename in (
            "env.sh",
            "functions.shinc",
            "managed_ops.shinc",
            "text_mods.shinc",
        ):
            shutil.copyfile(vendor / filename, self.script_dir / filename)

        binaries = self.root / "bin"
        binaries.mkdir()
        driver = binaries / "driver"
        driver.write_text(
            f"#!{sys.executable}\n"
            """import json, os, sys
from pathlib import Path

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
if tool == "docker":
    if args[0] == "ps":
        print("simulated-container")
        sys.exit(0)
    # Every chain-touching operation first checks that this node's console can
    # answer, because it is the only transport. The fake node is running and
    # synced; the adapters under test are not about that check.
    if args[:2] == ["container", "inspect"]:
        print(json.dumps([{"State": {"Status": "running"}}]))
        sys.exit(0)
    assert args[0] == "exec", args
    tool, args = Path(args[2]).name, args[3:]
if tool == "sleep":
    sys.exit(0)
if "config" in args:
    sys.exit(0)
if "version" in args:
    print("ever-cli 0.44.0")
elif "getstatsnew" in args:
    print(json.dumps({
        "global_id": 42,
        "sync_status": "synchronization_finished",
        "timediff": 1,
        "shards_timediff": 1,
        "validation_status": "disabled",
    }))
elif "getconfig 8" in args:
    print('{"p8": {"capabilities_dec": 0}}')
elif any(arg.startswith("getaccount ") for arg in args):
    # One state per read, so a test can let a deployment become Active late.
    states = os.environ.get("TEST_ACCOUNT_STATES", "Active").split(",")
    counter = Path(os.environ["ELECTIONS_WORK_DIR"], "account-reads")
    index = len(counter.read_text()) if counter.exists() else 0
    with counter.open("a") as stream:
        stream.write("x")
    print(json.dumps({"acc_type": states[min(index, len(states) - 1)],
                      "balance": "100000000000", "last_paid": 1}))
elif any(arg.startswith("getaccountstate ") for arg in args):
    if os.environ.get("FAIL_STATE"):
        print("simulated account fetch failure", file=sys.stderr)
        sys.exit(1)
    command = next(arg for arg in args if arg.startswith("getaccountstate "))
    Path(os.environ["ELECTIONS_WORK_DIR"], Path(command.split()[-1]).name).touch()
    print("written StateInit of account")
elif "getCustodians" in args:
    print(json.dumps({"custodians": [{"index": "0", "pubkey": "1"}]}))
    if os.environ.get("FAIL_GETTER"):
        print("simulated getter failure", file=sys.stderr)
        sys.exit(23)
elif "getParameters" in args:
    print('{"requiredTxnConfirms": "1"}')
elif "getDePoolInfo" in args:
    print(json.dumps({"validatorWallet": "0:" + "a" * 64}))
elif "get" in args:
    print(os.environ["TEST_ELECTOR_RESPONSE"])
else:
    raise RuntimeError((tool, args))
"""
        )
        driver.chmod(0o700)
        for name in ("docker", "ever-cli", "console", "sleep"):
            (binaries / name).symlink_to(driver)
        values = configuration.build_node_environment(self.config)
        # Also supports test runners inside Docker, where env.sh calls binaries directly.
        values["NODE_BIN_DIR"] = str(binaries)
        configuration.write_node_environment(self.script_dir / "env.sh", values)
        environment = dict(
            os.environ, PATH=f"{binaries}{os.pathsep}{os.environ['PATH']}"
        )
        for name in (
            "INITIAL_SETUP",
            "SHELLOPTS",
            "BASH_ENV",
            "FAIL_STATE",
            "FAIL_GETTER",
        ):
            environment.pop(name, None)
        self.environment = patch.dict(os.environ, environment, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.address = "0:" + "a" * 64

    def test_wallet_verification_with_optional_setup_variable_unset(self):
        observed = scripts.verify_imported_safe(self.store, self.config, self.address)
        self.assertEqual(observed["public_keys"], ["0" * 63 + "1"])
        self.assertEqual(observed["required_signatures"], 1)

    def test_a_deployment_is_awaited_until_the_account_is_active(self):
        """Deploying is one message; the account is Active only once it executes."""
        with patch.dict(os.environ, {"TEST_ACCOUNT_STATES": "Uninit,Uninit,Active"}):
            observed = scripts.wait_for_active_account(
                self.store, self.config, self.address, "DePool", timeout=30, interval=0
            )
        self.assertEqual(observed["acc_type"], "Active")
        reads = (self.root / "elections/account-reads").read_text()
        self.assertEqual(len(reads), 3)

    def test_an_account_that_never_activates_is_reported_with_the_retry(self):
        with patch.dict(os.environ, {"TEST_ACCOUNT_STATES": "Uninit"}):
            with self.assertRaises(process.OperationError) as caught:
                scripts.wait_for_active_account(
                    self.store, self.config, self.address, "DePool",
                    timeout=1, interval=0,
                )
        message = str(caught.exception)
        self.assertIn(self.address, message)
        self.assertIn("did not become active", message)

    def test_a_deploy_does_not_advance_its_stage_before_the_contract_exists(self):
        """A stage recorded before the contract exists breaks the next command."""
        cases = [
            ("wallet-created", "Msig_deploy.sh", wallets.wallet_deploy, "wallet_address"),
            ("depool-prepared", "DePool_deploy.sh", wallets.depool_deploy, "depool_address"),
        ]
        for stage, script, action, field in cases:
            with self.subTest(stage=stage):
                (self.script_dir / script).write_text("exit 0\n")
                config = dict(
                    self.config,
                    setup_stage=stage,
                    wallet={
                        "mode": "new",
                        "custodians": 3,
                        "required_signatures": 2,
                        "wallet_address": self.address,
                        "depool_address": "0:" + "b" * 64,
                    },
                )
                with patch(
                    "evernode.wallets.scripts.wait_for_active_account",
                    side_effect=process.OperationError("not active yet"),
                ) as awaited:
                    with self.assertRaises(process.OperationError):
                        action(self.store, config)
                awaited.assert_called_once()
                self.assertEqual(awaited.call_args.args[2], config["wallet"][field])
                self.assertEqual(config["setup_stage"], stage)

    def test_approved_setup_adapters_receive_confirmation_without_a_terminal(self):
        for filename in ("DePool_deploy.sh", "stake_to_depool.sh"):
            with self.subTest(script=filename):
                path = self.script_dir / filename
                path.write_text('test "${EVERNODE_APPROVED:-}" = yes\n')
                result = scripts.execute_validator_script(self.store, self.config, filename)
                self.assertEqual(result.returncode, 0)

    def test_depool_verification_with_optional_setup_variable_unset(self):
        observed = scripts.verify_imported_depool(self.store, self.config, self.address)
        self.assertEqual(observed["validator_wallet"], self.address)

    def test_engine_adnl_decoding_does_not_require_xxd(self):
        directory = self.root / 'node_cfg'
        directory.mkdir()
        key = bytes(range(32))
        (directory / 'config.json').write_text(json.dumps({'validator_keys': [{
            'validator_adnl_key_id': base64.b64encode(key).decode(), 'election_id': 123,
        }]}))
        unavailable = self.root / 'bin/xxd'
        unavailable.write_text('#!/bin/bash\nexit 97\n')
        unavailable.chmod(0o700)
        result = subprocess.run(['bash', '-c', 'source ./env.sh; source ./functions.shinc; Get_Engine_ADNL'],
                                cwd=self.script_dir, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), key.hex() + ' 123')

    def test_elector_lookup_failure_is_not_reported_as_participation(self):
        vendor = Path(__file__).resolve().parents[1] / 'vendor/ever-validator/scripts'
        target = self.script_dir / 'take_part_in_elections.sh'
        shutil.copyfile(vendor / target.name, target)
        lock = self.root / 'bin/flock'
        lock.write_text('#!/bin/bash\nexit 0\n')
        lock.chmod(0o700)
        keys = self.root / 'keys'
        keys.mkdir()
        (keys / 'validator01.addr').write_text(self.address)
        (keys / 'depool.addr').write_text('0:' + 'b' * 64)
        (keys / 'validator01_1.keys.json').write_text(json.dumps({'public': '1' * 64, 'secret': '2' * 64}))
        abi = self.root / 'contracts/EverX/safemultisig/SafeMultisigWallet.abi.json'
        abi.parent.mkdir(parents=True)
        abi.touch()
        rounds = {'rounds': {str(i): {'id': str(i), 'supposedElectedAt': '123'} for i in range(4)}}
        with (self.script_dir / 'functions.shinc').open('a') as output:
            output.write('\nGet_TimeDiff() { echo "0 0"; }\n'
                         'Get_Current_Elector_Type() { echo "elector solidity"; }\n'
                         'Get_Current_Elections_ID() { echo 123; }\n'
                         'Get_Account_Info() { echo "Active 100000000000000 1"; }\n'
                         'Get_Account_Custodians_Info() { echo "3 2"; }\n'
                         'Get_DP_Info() { echo \'{"validatorWallet":"' + self.address + '","proxies":["p0","p1"]}\'; }\n'
                         'Get_DP_Rounds() { echo \'' + json.dumps(rounds) + '\'; }\n'
                         'Get_Engine_ADNL() { echo "' + 'a' * 64 + ' 123"; }\n'
                         'Elector_ADNL_Search() { echo "lookup failed"; return 1; }\n')
        result = subprocess.run(['bash', str(target)], cwd=self.script_dir,
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Wrong ADNL key', result.stdout)
        self.assertNotIn('You participate already', result.stdout)
        self.assertFalse((self.root / 'elections/123/123_elector-confirmed-bid.txt').exists())

    def test_malformed_engine_adnl_keys_fail_closed(self):
        directory = self.root / 'node_cfg'
        directory.mkdir()
        for raw_keys in [(b'short',), (bytes(range(32)), b'short')]:
            with self.subTest(keys=len(raw_keys)):
                (directory / 'config.json').write_text(json.dumps({'validator_keys': [
                    {'validator_adnl_key_id': base64.b64encode(key).decode(), 'election_id': i + 1}
                    for i, key in enumerate(raw_keys)
                ]}))
                result = subprocess.run(['bash', '-c', 'source ./env.sh; source ./functions.shinc; Get_Engine_ADNL'],
                                        cwd=self.script_dir, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Cannot decode', result.stderr)

    @unittest.skipUnless(shutil.which('bc'), 'Requires bc')
    def test_solidity_elector_booleans_and_full_precision_adnl(self):
        adnl = 'a' * 64
        member = {'adnl_addr': str(int(adnl, 16)), 'stake': '50000000000000',
                  'time': '123', 'max_factor': '196608', 'addr': '1'}
        cases = [
            (True, {'key': member}, 0, '50000000000000 123 196608 1'),
            (True, {}, 4, 'absent'),
            (False, {}, 3, 'null'),
        ]
        program = ('source ./env.sh; source ./functions.shinc; '
                   'Get_Current_Elector_Type() { echo "elector solidity"; }; '
                   'Elector_ADNL_Search ' + adnl)
        for opened, members, code, expected in cases:
            with self.subTest(opened=opened, members=bool(members)):
                response = json.dumps({'election_open': opened, 'cur_elect': {'members': members}})
                result = subprocess.run(['bash', '-c', program], cwd=self.script_dir,
                                        env=dict(os.environ, TEST_ELECTOR_RESPONSE=response),
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

    def test_an_elector_no_description_decodes_is_its_own_outcome(self):
        """Unreadable is not the same as "wrong elector type", and not fatal.

        Every bundled description failing used to be reported as a fatal
        elector-type fault, which stopped the node bidding. It now has its own
        code so the caller can bid anyway and say what it could not confirm.
        """
        program = ('source ./env.sh; source ./functions.shinc; '
                   'Get_Current_Elector_Type() { echo "elector solidity"; }; '
                   'Elector_ADNL_Search ' + 'a' * 64)
        result = subprocess.run(['bash', '-c', program], cwd=self.script_dir,
                                env=dict(os.environ, TEST_ELECTOR_RESPONSE='Failed to decode output messages'),
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), 'unreadable')
        self.assertIn('no bundled elector interface description', result.stderr)

    def test_inherited_nounset_does_not_break_reference_scripts(self):
        with patch.dict(os.environ, SHELLOPTS="nounset"):
            observed = scripts.verify_imported_safe(
                self.store, self.config, self.address
            )
        self.assertEqual(observed["custodians"], 1)

    def test_wallet_getter_failure_is_not_accepted_as_success(self):
        with patch.dict(os.environ, FAIL_GETTER="1"):
            with self.assertRaisesRegex(
                process.OperationError, "simulated getter failure"
            ):
                scripts.verify_imported_safe(self.store, self.config, self.address)

    def test_account_fetch_failure_is_reported_by_both_adapters(self):
        with patch.dict(os.environ, FAIL_STATE="1"):
            for verify in (
                scripts.verify_imported_safe,
                scripts.verify_imported_depool,
            ):
                with self.subTest(adapter=verify.__name__):
                    with self.assertRaisesRegex(process.OperationError, "Cannot get"):
                        verify(self.store, self.config, self.address)

    def test_invalid_environment_stops_verification(self):
        configuration.write_node_environment(
            self.script_dir / "env.sh", {"IS_ENVIRONMENT_CONFIGURED": "false"}
        )
        with self.assertRaisesRegex(
            process.OperationError, "environment is not configured"
        ):
            scripts.verify_imported_safe(self.store, self.config, self.address)

    def _send_message(self, successful_poll):
        """Run the reference retry loop without sending a blockchain message."""
        program = r"""source ./env.sh >/dev/null
source ./functions.shinc >/dev/null
Get_Account_Info() { echo 'Active 100 1'; }
Get_Account_Custodians_Info() { echo '3 2'; }
Send_File_To_BC() { echo success; }
sleep() { :; }
Get_MSIG_Trans_List() {
    local poll
    poll=$(cat polls)
    echo "$((poll + 1))" > polls
    if (( poll >= SUCCESSFUL_POLL )); then
        echo '{"transactions":[{"dest":"destination","id":"7695634054725582017"}]}'
    else
        echo '{"transactions":[]}'
    fi
}
echo 0 > polls
Send_Message source destination unused.boc
"""
        environment = dict(os.environ, SUCCESSFUL_POLL=str(successful_poll))
        caller = self.script_dir / "send-message-test.sh"
        caller.write_text(program)
        return subprocess.run(
            ["bash", str(caller)],
            cwd=self.script_dir,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_send_message_retry_returns_only_transaction_id(self):
        result = self._send_message(successful_poll=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "7695634054725582017\n")
        self.assertIn("TransQTY=0", result.stderr)
        self.assertIn("In Send_Message()", result.stderr)

    def test_send_message_exhausted_retries_leave_stdout_empty(self):
        result = self._send_message(successful_poll=99)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("In Send_Message()", result.stderr)
