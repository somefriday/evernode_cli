"""Run import adapters with the real vendor scripts and simulated node tools."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from evernode import configuration, process, scripts, storage
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
        for filename in ("env.sh", "functions.shinc", "text_mods.shinc"):
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
    assert args[0] == "exec", args
    tool, args = Path(args[2]).name, args[3:]
if tool == "sleep":
    sys.exit(0)
if "config" in args:
    sys.exit(0)
if "version" in args:
    print("ever-cli 0.44.0")
elif "getstatsnew" in args:
    print('{"global_id": 42}')
elif "getconfig 8" in args:
    print('{"p8": {"capabilities_dec": 0}}')
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

    def test_depool_verification_with_optional_setup_variable_unset(self):
        observed = scripts.verify_imported_depool(self.store, self.config, self.address)
        self.assertEqual(observed["validator_wallet"], self.address)

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
