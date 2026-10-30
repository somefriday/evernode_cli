"""The node's own console is the only transport; there is no DApp mode.

A managed node may be on a private network, so a public DApp endpoint has
nothing to say about it and a fallback can only hang. The reference scripts
decided between the two by guessing — ``pgrep ever-node`` in their own process
table, which is the host's while the node runs in a container. These tests keep
that choice from coming back, in the vendored scripts and in the CLI.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evernode import process, scripts
from support import make_node_configuration

VENDOR = Path(__file__).resolve().parents[1] / "vendor/ever-validator"
SCRIPTS = VENDOR / "scripts"


class VendoredScriptTests(unittest.TestCase):
    def shell_sources(self):
        return sorted(
            path
            for path in VENDOR.rglob("*")
            if path.suffix in (".sh", ".shinc") and path.is_file()
        )

    def test_no_script_ever_turns_dapp_mode_on(self):
        """The single assignment that produced the traced failure.

        ``Sign_Trans.sh`` set this when it could not see a node process, which
        is the normal state when the scripts run outside the container, and
        every later ever-cli call then spent 120 s on an endpoint the network
        does not have.
        """
        offenders = []
        for path in self.shell_sources():
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if "FORCE_USE_DAPP=true" in line.replace(" ", ""):
                    offenders.append(f"{path.relative_to(VENDOR)}:{number}")
        self.assertEqual(offenders, [])

    def test_env_sh_pins_console_transport(self):
        text = (SCRIPTS / "env.sh").read_text()
        self.assertIn("FORCE_USE_DAPP=false", text)
        self.assertNotIn("offline", text.split("FORCE_USE_DAPP=false")[1][:200])

    def test_no_script_asks_a_terminal_about_the_transport(self):
        """These prompts read /dev/tty, which a detached manager has not got."""
        offenders = [
            str(path.relative_to(VENDOR))
            for path in self.shell_sources()
            if "DAPP temporarily" in path.read_text()
        ]
        self.assertEqual(offenders, [])

    def test_the_transport_is_not_inferred_from_a_process_table(self):
        """`pgrep ever-node` asks whether a node is visible in *this* process
        table, which is the host's while the node runs in a container, so the
        answer was routinely wrong. Scoped to the scripts this CLI runs plus
        the two signers: elsewhere in the tree `pgrep` checks whether a service
        restart took, which is a different question and not a transport choice.
        """
        names = scripts.HOST_SCRIPTS | {
            "Remote_Signer.sh",
            "balance_check.sh",
            "functions.shinc",
        }
        offenders = [
            f"{name}:{number}"
            for name in sorted(names)
            for number, line in enumerate((SCRIPTS / name).read_text().splitlines(), 1)
            if "pgrep" in line and "NODE_BIN_NAME" in line
        ]
        self.assertEqual(offenders, [])

    def test_every_chain_touching_host_script_requires_the_console(self):
        """Offline key generation aside, nothing may run without the console."""
        missing = [
            name
            for name in sorted(scripts.HOST_SCRIPTS - scripts.OFFLINE_SCRIPTS)
            if "Require_Node_Console" not in (SCRIPTS / name).read_text()
        ]
        self.assertEqual(missing, [])


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "Requires bash and jq")
class RequireNodeConsoleTests(unittest.TestCase):
    """Exercise the one function that now decides whether an operation runs."""

    def run_guard(self, statistics):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            program = (
                "set +u\n"
                'FuncSelfScriptName=test\n'
                'function echoerr() { printf "%s\\n" "$*" >&2; }\n'
                f"function Get_TimeDiff() {{ printf '%s\\n' {statistics!r}; }}\n"
                f'source "{self.guard}"\n'
                "Require_Node_Console; echo \"rc=$?\"\n"
            )
            (directory / "case.sh").write_text(program)
            return subprocess.run(
                ["bash", str(directory / "case.sh")],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=30,
            )

    @classmethod
    def setUpClass(cls):
        """Extract the function so the case does not need a node environment."""
        lines = (SCRIPTS / "functions.shinc").read_text().splitlines()
        start = next(
            index
            for index, line in enumerate(lines)
            if line.startswith("function Require_Node_Console")
        )
        end = next(
            index for index, line in enumerate(lines[start:], start) if line == "}"
        )
        cls.temporary = tempfile.TemporaryDirectory()
        cls.guard = Path(cls.temporary.name) / "guard.shinc"
        cls.guard.write_text("\n".join(lines[start : end + 1]) + "\n")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_a_synced_node_is_accepted(self):
        result = self.run_guard("0 0 synchronization_finished disabled")
        self.assertIn("rc=0", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_a_node_inside_the_tolerance_is_accepted(self):
        result = self.run_guard("20 20 synchronization_by_blocks active")
        self.assertIn("rc=0", result.stdout)

    def test_a_lagging_node_is_refused_with_its_own_numbers(self):
        result = self.run_guard("61 4 synchronization_by_blocks disabled")
        self.assertIn("rc=1", result.stdout)
        self.assertIn("masterchain 61s", result.stderr)
        self.assertIn("shardchain 4s", result.stderr)

    def test_a_lagging_shardchain_alone_is_refused(self):
        """Shards can fall behind a caught-up masterchain after a split."""
        result = self.run_guard("2 99 synchronization_by_blocks disabled")
        self.assertIn("rc=1", result.stdout)
        self.assertIn("shardchain 99s", result.stderr)

    def test_a_node_that_is_down_is_refused_and_says_so(self):
        for status in ("Node Down", "Error", "db_broken", ""):
            with self.subTest(status=status):
                result = self.run_guard(status)
                self.assertIn("rc=1", result.stdout)
                self.assertIn("not answering", result.stderr)

    def test_the_refusal_never_mentions_an_alternative_transport(self):
        result = self.run_guard("Node Down")
        self.assertNotIn("DAPP", result.stderr.upper())


class CliPrecheckTests(unittest.TestCase):
    """The CLI refuses before the script runs, so the reason is not buried.

    Script output is kept in a private log because it prints addresses and
    balances, so a failure inside the script reaches the operator only as an
    exit code and a path. The commonest cause gets its own message instead.
    """

    def setUp(self):
        self.config = {"name": "validator01"}

    def test_a_synced_node_passes(self):
        statistics = {
            "sync_status": "synchronization_finished",
            "timediff": 0,
            "shards_timediff": 0,
        }
        with patch("evernode.nodes.fetch_node_statistics", return_value=statistics):
            scripts.require_node_console(self.config)

    def test_a_lagging_node_names_the_command_that_fixes_it(self):
        statistics = {
            "sync_status": "synchronization_by_blocks",
            "timediff": 900,
            "shards_timediff": 900,
        }
        with patch("evernode.nodes.fetch_node_statistics", return_value=statistics):
            with self.assertRaises(process.OperationError) as caught:
                scripts.require_node_console(self.config)
        self.assertIn("node sync -n validator01", str(caught.exception))

    def test_an_unreachable_node_is_reported_rather_than_routed_around(self):
        failure = process.OperationError("Node container is not running")
        with patch("evernode.nodes.fetch_node_statistics", side_effect=failure):
            with self.assertRaises(process.OperationError):
                scripts.require_node_console(self.config)

    def test_offline_scripts_are_only_the_ones_that_never_touch_the_chain(self):
        self.assertEqual(scripts.OFFLINE_SCRIPTS, {"Msig-prep.sh", "DePool-prep.sh"})
        self.assertTrue(scripts.OFFLINE_SCRIPTS < scripts.HOST_SCRIPTS)
        for name in scripts.OFFLINE_SCRIPTS:
            text = (SCRIPTS / name).read_text()
            with self.subTest(script=name):
                for call in ("Send_Message", "Get_SC_current_state", "CALL_CONS"):
                    self.assertNotIn(call, text)


if __name__ == "__main__":
    unittest.main()


class ReadinessGateTests(unittest.TestCase):
    """A node at the chain head may still report one reading above tolerance."""

    def setUp(self):
        self.config = make_node_configuration("validator01")

    def test_a_single_slow_block_does_not_abort_an_operation(self):
        readings = [
            {"node_status": "synchronization_finished", "timediff": 14, "shards_timediff": 14},
            {"node_status": "synchronization_finished", "timediff": 3, "shards_timediff": 3},
        ]
        with patch("evernode.nodes.fetch_node_statistics", side_effect=readings) as read:
            scripts.require_node_console(self.config, attempts=3, interval=0)
        self.assertEqual(read.call_count, 2)

    def test_a_node_left_behind_is_refused_with_what_was_seen(self):
        reading = {
            "node_status": "synchronization_by_archives",
            "timediff": 940,
            "shards_timediff": 940,
        }
        with patch("evernode.nodes.fetch_node_statistics", return_value=reading):
            with self.assertRaises(process.OperationError) as caught:
                scripts.require_node_console(self.config, attempts=2, interval=0)
        message = str(caught.exception)
        self.assertIn("node sync -n validator01 --wait", message)
        self.assertIn("940", message)
        self.assertIn("synchronization_by_archives", message)


class ScriptDiagnosticTests(unittest.TestCase):
    """A failing script's own reason must reach the operator, never a phrase."""

    def diagnose(self, stdout="", stderr=""):
        return scripts.script_diagnostics(
            subprocess.CompletedProcess([], 1, stdout=stdout, stderr=stderr)
        )

    def test_the_reason_a_transaction_is_unsigned_is_reported(self):
        reported = self.diagnose(stderr=(
            "---INFO: signing transaction 7702269231770027393 as custodian 1\n"
            "###-ERROR: Transaction 7702269231770027393: transaction 7702269231770027393"
            " holds 1 of the 2 signatures it needs, and every key this node holds has"
            " already signed it.\n"
        ))
        self.assertEqual(len(reported), 1)
        self.assertIn("1 of the 2 signatures", reported[0])

    def test_a_seed_phrase_is_never_reported(self):
        phrase = " ".join(["abandon"] * 12)
        reported = self.diagnose(stdout=(
            f"###-ERROR: could not store {phrase}\n"
            "###-WARNING: seed phrase 1 was not saved\n"
            "###-ERROR: the wallet is short of funds\n"
        ))
        self.assertEqual(reported, ["###-ERROR: the wallet is short of funds"])

    def test_dapp_credentials_are_never_the_reason(self):
        # There is no DApp mode, so these lines appear on every failure.
        reported = self.diagnose(stderr=(
            "\x1b[31;1m###-ERROR(functions.shinc line 130): Can't set project_id\x1b[0m\n"
            "\x1b[31;1m###-ERROR(functions.shinc line 131): Can't set access_key\x1b[0m\n"
            "\x1b[31;1m###-ERROR(stake_to_depool.sh line 206): DePool account is not active\x1b[0m\n"
        ))
        self.assertEqual(
            reported,
            ["###-ERROR(stake_to_depool.sh line 206): DePool account is not active"],
        )

    def test_a_script_that_marks_nothing_reports_only_the_log(self):
        self.assertEqual(self.diagnose(stdout="just progress output\n"), [])
