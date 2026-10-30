"""Contract checks for the DePool stake-operation and status commands."""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
import unittest.mock
from unittest.mock import patch

from evernode import (
    arguments,
    cli,
    configuration,
    elections,
    process,
    provisioning,
    storage,
)
from support import make_node_configuration


def operational_node(stage="ready-for-elections", name="validator01"):
    return make_node_configuration(
        name,
        setup_stage=stage,
        wallet={
            "mode": "new",
            "custodians": 3,
            "required_signatures": 2,
            "wallet_address": "0:" + "a" * 64,
            "depool_address": "0:" + "b" * 64,
        },
    )


class WithdrawalCommandTests(unittest.TestCase):
    def run_command(self, *argv, stage="ready-for-elections"):
        with (
            patch("evernode.cli.require_root_privileges"),
            patch("evernode.cli.storage.NodeStateStore") as store,
            patch("evernode.setup_actions.nodes.fetch_node_statistics",
                  return_value={"sync_status": "synchronization_finished",
                                "timediff": 1, "shards_timediff": 1}),
            patch("evernode.scripts.execute_validator_script") as script,
            patch("evernode.setup_actions.elections.hold_election_lock"),
            contextlib.redirect_stdout(io.StringIO()) as output,
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            store.return_value.load_node_config.return_value = operational_node(stage)
            code = cli.main(list(argv))
        return code, script, output.getvalue() + errors.getvalue()

    def test_each_withdrawal_choice_calls_its_own_reference_operation(self):
        cases = {
            ("--amount", "100"): ("WD-part", "--", "--", "100"),
            ("--all",): ("WD-all", "--", "--"),
            ("--cancel",): ("cancelWithdrawal", "--", "--"),
        }
        for flags, expected in cases.items():
            with self.subTest(flags=flags):
                code, script, _ = self.run_command(
                    "depool", "withdraw", "-n", "validator01", "--yes", *flags
                )
                self.assertEqual(code, 0)
                self.assertEqual(script.call_args.args[2:], ("stake_to_depool.sh",) + expected)

    def test_replenish_sends_its_amount_to_the_reference_operation(self):
        code, script, _ = self.run_command(
            "depool", "replenish", "-n", "validator01", "--amount", "25", "--yes"
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            script.call_args.args[2:], ("stake_to_depool.sh", "replenish", "--", "--", "25")
        )

    def test_an_ordinary_stake_top_up_calls_the_reference_operation(self):
        code, script, _ = self.run_command(
            "depool", "stake", "-n", "validator01", "--amount", "50000", "--yes"
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            script.call_args.args[2:],
            ("stake_to_depool.sh", "ordinary", "--", "--", "50000"),
        )
        # Only the setup stage may skip an already present stake.
        self.assertIsNone(script.call_args.kwargs.get("variables"))

    def test_the_initial_stake_is_marked_so_a_retry_does_not_pay_twice(self):
        code, script, _ = self.run_command(
            "depool", "stake-initial", "-n", "validator01", "--yes",
            stage="depool-deployed",
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            script.call_args.kwargs["variables"], {"EVERNODE_INITIAL_STAKE": "yes"}
        )

    def test_a_nonpositive_stake_is_refused(self):
        code, script, text = self.run_command(
            "depool", "stake", "-n", "validator01", "--amount", "0", "--yes"
        )
        self.assertEqual(code, 1)
        script.assert_not_called()
        self.assertIn("at least one token", text)

    def test_confirming_a_reviewed_transaction_calls_the_signing_script(self):
        code, script, _ = self.run_command(
            "depool", "confirm", "-n", "validator01",
            "--transaction-id", "7701911356660055489", "--yes",
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            script.call_args.args[2:],
            ("Sign_Trans.sh", "validator01", "7701911356660055489"),
        )

    def test_a_transaction_id_that_is_not_a_number_is_refused(self):
        for value in ("abc", "1; rm -rf /", "-1", ""):
            with self.subTest(value=value):
                code, script, _ = self.run_command(
                    "depool", "confirm", "-n", "validator01",
                    "--transaction-id", value, "--yes",
                )
                self.assertEqual(code, 1)
                script.assert_not_called()

    def test_the_confirmation_question_names_the_transaction(self):
        questions = []
        with (
            patch("evernode.cli.require_root_privileges"),
            patch("evernode.cli.storage.NodeStateStore") as store,
            patch("evernode.setup_actions.nodes.fetch_node_statistics",
                  return_value={"sync_status": "synchronization_finished",
                                "timediff": 1, "shards_timediff": 1}),
            patch("evernode.scripts.execute_validator_script"),
            patch("evernode.setup_actions.elections.hold_election_lock"),
            patch("evernode.setup_actions.ui.confirm_operation",
                  side_effect=lambda _, question: questions.append(question)),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            store.return_value.load_node_config.return_value = operational_node()
            cli.main(["depool", "confirm", "-n", "validator01",
                      "--transaction-id", "42"])
        self.assertIn("42", questions[0])
        self.assertIn("executes the transfer", questions[0])

    def test_withdrawal_before_setup_is_complete_sends_nothing(self):
        for stage in ("node-started", "depool-deployed", "wallet-deployed"):
            with self.subTest(stage=stage):
                code, script, text = self.run_command(
                    "depool", "withdraw", "-n", "validator01", "--all", "--yes", stage=stage
                )
                self.assertEqual(code, 1)
                script.assert_not_called()
                self.assertIn("Complete wallet/DePool setup", text)

    def test_an_imported_node_may_also_withdraw(self):
        code, script, _ = self.run_command(
            "depool", "withdraw", "-n", "validator01", "--all", "--yes", stage="imported"
        )
        self.assertEqual(code, 0)
        script.assert_called_once()

    def test_a_nonpositive_amount_is_refused(self):
        for amount in ("0", "-5"):
            with self.subTest(amount=amount):
                code, script, text = self.run_command(
                    "depool", "withdraw", "-n", "validator01", "--amount", amount, "--yes"
                )
                self.assertEqual(code, 1)
                script.assert_not_called()
                self.assertIn("at least one token", text)

    def test_the_withdrawal_choices_are_mutually_exclusive(self):
        parser = arguments.build_argument_parser()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(["depool", "withdraw", "-n", "a", "--all", "--cancel"])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(["depool", "withdraw", "-n", "a"])

    def test_the_confirmation_names_the_effect_of_each_choice(self):
        for flags, expected in [
            (("--all",), "entire ordinary stake"),
            (("--amount", "100"), "100 tokens"),
            (("--cancel",), "Cancel the pending DePool withdrawal"),
        ]:
            with self.subTest(flags=flags):
                questions = []
                with (
                    patch("evernode.cli.require_root_privileges"),
                    patch("evernode.cli.storage.NodeStateStore") as store,
                    patch("evernode.setup_actions.nodes.fetch_node_statistics",
                          return_value={"sync_status": "synchronization_finished",
                                        "timediff": 1, "shards_timediff": 1}),
                    patch("evernode.scripts.execute_validator_script"),
                    patch("evernode.setup_actions.elections.hold_election_lock"),
                    patch("evernode.setup_actions.ui.confirm_operation",
                          side_effect=lambda _, question: questions.append(question)),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    store.return_value.load_node_config.return_value = operational_node()
                    cli.main(["depool", "withdraw", "-n", "validator01", *flags])
                self.assertIn(expected, questions[0])

    def test_depool_info_reads_state_without_confirmation_or_scripts(self):
        observed = {"pool_closed": False, "own_total_nanotokens": "1"}
        with (
            patch("evernode.cli.require_root_privileges"),
            patch("evernode.cli.storage.NodeStateStore") as store,
            patch("evernode.setup_actions.nodes.fetch_node_statistics",
                  return_value={"sync_status": "synchronization_finished",
                                "timediff": 1, "shards_timediff": 1}),
            patch("evernode.scripts.read_depool_state", return_value=observed),
            patch("evernode.scripts.execute_validator_script") as script,
            patch("evernode.setup_actions.ui.confirm_operation") as confirm,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            store.return_value.load_node_config.return_value = operational_node()
            self.assertEqual(cli.main(["depool", "info", "-n", "validator01"]), 0)
        confirm.assert_not_called()
        script.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["own_total_nanotokens"], "1")


class ElectionStateReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = storage.NodeStateStore(temporary.name)
        self.config = operational_node()
        (self.store.node_directory(self.config["name"]) / "logs/validator").mkdir(parents=True)

    def describe(self, **arguments):
        with patch("evernode.elections.node_election_cron_path") as path:
            path.return_value.exists.return_value = arguments.pop("enabled", False)
            path.return_value.is_symlink.return_value = False
            path.return_value.read_text.return_value = "# Managed by evernode\n"
            with patch("evernode.elections.cron_daemon_state",
                       return_value=arguments.pop("daemon", "running")):
                return elections.describe_election_state(self.store, self.config, **arguments)

    def test_an_installed_schedule_without_a_cron_daemon_is_reported(self):
        value = self.describe(enabled=True, daemon="not running")
        self.assertTrue(value["enabled"])
        self.assertIn("no cron daemon is running", value["warning"])

    def test_a_running_daemon_adds_no_warning(self):
        self.assertNotIn("warning", self.describe(enabled=True, daemon="running"))

    def test_validating_requires_both_set_membership_and_active_status(self):
        cases = [
            ({"in_current_vset_p34": True, "validation_status": "Active"}, True),
            ({"in_current_vset_p34": True, "validation_status": "Disabled"}, False),
            ({"in_current_vset_p34": False, "validation_status": "Active"}, False),
            ({"in_next_vset_p36": True, "validation_status": "Disabled"}, False),
        ]
        for statistics, expected in cases:
            with self.subTest(statistics=statistics):
                value = self.describe(enabled=True, statistics=statistics)
                self.assertEqual(value["chain"]["validating"], expected)

    def test_being_elected_for_the_next_round_is_reported_separately(self):
        value = self.describe(enabled=True, statistics={"in_next_vset_p36": True})
        self.assertTrue(value["chain"]["elected_for_next_round"])
        self.assertFalse(value["chain"]["validating"])

    def test_an_unreachable_console_is_reported_instead_of_claimed_idle(self):
        value = self.describe(enabled=True, console_error="container is not running")
        self.assertEqual(value["chain"], {"error": "container is not running"})

    def test_a_schedule_alone_never_implies_validation(self):
        self.assertNotIn("chain", self.describe(enabled=True))


@unittest.skipUnless(shutil.which("bash"), "Requires a POSIX workspace")
class ScriptRefreshTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(os.umask, os.umask(0o077))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        for component in provisioning.SOURCE_COMPONENTS:
            (self.source / component).mkdir(parents=True)
        # env.sh must already declare every managed setting, as upstream's does.
        self.config = operational_node()
        self.config["source_spec"] = {"local": str(self.source), "repo": None, "ref": None}
        declared = configuration.build_node_environment(self.config)
        (self.source / "scripts/env.sh").write_text(
            "".join(f"export {key}=placeholder\n" for key in declared)
        )
        (self.source / "scripts/functions.shinc").write_text("original\n")
        (self.source / "scripts/run.sh").write_text("echo original\n")
        (self.source / "contracts/a.tvc").write_text("contract\n")
        self.store = storage.NodeStateStore(self.root / "state")
        self.directory = self.store.node_directory(self.config["name"])
        for component in provisioning.SOURCE_COMPONENTS:
            (self.directory / component).mkdir(parents=True)
            os.chmod(self.directory / component, 0o700)
        with tempfile.TemporaryDirectory(dir=self.directory) as work:
            staged, _ = provisioning.render_source_snapshot(
                self.store, self.config, Path(work)
            )
            for component in provisioning.SOURCE_COMPONENTS:
                shutil.rmtree(self.directory / component)
                shutil.copytree(staged / component, self.directory / component)
                storage.restrict_workspace_permissions(self.directory / component)

    def test_an_unchanged_source_reports_no_work(self):
        plan = provisioning.refresh_node_scripts(self.store, self.config, dry_run=True)
        self.assertFalse(plan["changed"])
        self.assertEqual(plan["components"]["scripts"]["updated"], [])

    def test_a_changed_source_is_reported_and_installed_with_a_backup(self):
        (self.source / "scripts/functions.shinc").write_text("fixed\n")
        (self.source / "scripts/added.shinc").write_text("new\n")
        (self.source / "scripts/run.sh").unlink()
        plan = provisioning.refresh_node_scripts(self.store, self.config, dry_run=True)
        self.assertEqual(plan["components"]["scripts"]["updated"], ["functions.shinc"])
        self.assertEqual(plan["components"]["scripts"]["added"], ["added.shinc"])
        self.assertEqual(plan["components"]["scripts"]["removed"], ["run.sh"])
        # A dry run changes nothing.
        self.assertEqual((self.directory / "scripts/functions.shinc").read_text(), "original\n")

        applied = provisioning.refresh_node_scripts(self.store, self.config)
        self.assertEqual((self.directory / "scripts/functions.shinc").read_text(), "fixed\n")
        self.assertEqual((self.directory / "scripts/added.shinc").read_text(), "new\n")
        self.assertFalse((self.directory / "scripts/run.sh").exists())
        backup = Path(applied["backup"])
        self.assertEqual((backup / "scripts/functions.shinc").read_text(), "original\n")
        self.assertEqual((backup / "scripts/run.sh").read_text(), "echo original\n")

    def test_refreshing_keeps_the_directory_so_container_mounts_stay_valid(self):
        before = (self.directory / "scripts").stat().st_ino
        (self.source / "scripts/functions.shinc").write_text("fixed\n")
        provisioning.refresh_node_scripts(self.store, self.config)
        self.assertEqual((self.directory / "scripts").stat().st_ino, before)

    def test_refreshed_files_keep_private_permissions(self):
        (self.source / "scripts/functions.shinc").write_text("fixed\n")
        (self.source / "scripts/added.sh").write_text("echo new\n")
        provisioning.refresh_node_scripts(self.store, self.config)
        from stat import S_IMODE
        self.assertEqual(S_IMODE((self.directory / "scripts/functions.shinc").stat().st_mode), 0o600)
        self.assertEqual(S_IMODE((self.directory / "scripts/added.sh").stat().st_mode), 0o700)

    def test_a_subdirectory_emptied_by_the_refresh_is_removed(self):
        extra = self.source / "scripts/extra"
        extra.mkdir()
        (extra / "old.shinc").write_text("old\n")
        provisioning.refresh_node_scripts(self.store, self.config)
        self.assertTrue((self.directory / "scripts/extra/old.shinc").exists())
        shutil.rmtree(extra)
        # Without pruning the empty directory the fingerprint check below fails.
        provisioning.refresh_node_scripts(self.store, self.config)
        self.assertFalse((self.directory / "scripts/extra").exists())

    def test_a_node_without_recorded_source_inputs_is_refused(self):
        self.config["source_spec"] = None
        with self.assertRaisesRegex(process.OperationError, "No saved source inputs"):
            provisioning.refresh_node_scripts(self.store, self.config, dry_run=True)

    def test_a_refresh_records_the_new_source_hashes(self):
        (self.source / "scripts/functions.shinc").write_text("fixed\n")
        provisioning.refresh_node_scripts(self.store, self.config)
        self.assertEqual(
            self.config["source_hashes"]["scripts"],
            storage.calculate_directory_fingerprint(self.directory / "scripts"),
        )


class NodeStatusReportTests(unittest.TestCase):
    """A node whose schedule is installed has finished setup."""

    def status(self, *, enabled, stage="ready-for-elections"):
        from evernode import nodes
        with (
            patch("evernode.nodes.docker.inspect_managed_container", return_value=None),
            patch("evernode.elections.node_election_cron_path") as path,
        ):
            path.return_value.exists.return_value = enabled
            return nodes.get_node_status(operational_node(stage))

    def test_an_operating_node_advertises_no_further_setup_step(self):
        self.assertIsNone(self.status(enabled=True)["next_setup_command"])

    def test_a_node_awaiting_setup_still_names_its_next_step(self):
        value = self.status(enabled=False, stage="depool-deployed")
        self.assertEqual(value["next_setup_command"], "evernode depool stake-initial -n validator01")


class StakeOperationLockTests(unittest.TestCase):
    """A stake operation and a scheduled election sign from the same Safe."""

    def run_action(self, *argv):
        taken = []
        with (
            patch("evernode.cli.require_root_privileges"),
            patch("evernode.cli.storage.NodeStateStore") as store,
            patch("evernode.setup_actions.nodes.fetch_node_statistics",
                  return_value={"sync_status": "synchronization_finished",
                                "timediff": 1, "shards_timediff": 1}),
            patch("evernode.scripts.execute_validator_script"),
            patch("evernode.scripts.read_depool_state", return_value={}),
            patch("evernode.setup_actions.elections.hold_election_lock") as lock,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            lock.side_effect = lambda _store, name: taken.append(name) or contextlib.nullcontext()
            store.return_value.load_node_config.return_value = operational_node()
            cli.main(list(argv))
        return taken

    def test_stake_operations_hold_the_election_lock(self):
        for argv in (
            ("depool", "withdraw", "-n", "validator01", "--all", "--yes"),
            ("depool", "withdraw", "-n", "validator01", "--amount", "10", "--yes"),
            ("depool", "replenish", "-n", "validator01", "--amount", "10", "--yes"),
            ("depool", "stake-initial", "-n", "validator01", "--yes"),
        ):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_action(*argv), ["validator01"])

    def test_read_only_and_verification_actions_do_not_take_it(self):
        for argv in (
            ("depool", "info", "-n", "validator01"),
            ("wallet", "verify", "-n", "validator01"),
        ):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_action(*argv), [])

    def test_a_running_election_blocks_a_stake_operation(self):
        with (
            patch("evernode.cli.require_root_privileges"),
            patch("evernode.cli.storage.NodeStateStore") as store,
            patch("evernode.setup_actions.nodes.fetch_node_statistics",
                  return_value={"sync_status": "synchronization_finished",
                                "timediff": 1, "shards_timediff": 1}),
            patch("evernode.scripts.execute_validator_script") as script,
            patch("evernode.setup_actions.elections.hold_election_lock",
                  side_effect=process.OperationError("holds validator01's election lock")),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            store.return_value.load_node_config.return_value = operational_node()
            code = cli.main(["depool", "withdraw", "-n", "validator01", "--all", "--yes"])
        self.assertEqual(code, 1)
        script.assert_not_called()
        self.assertIn("election lock", errors.getvalue())


class NodeLogCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = storage.NodeStateStore(temporary.name)
        self.config = operational_node()
        self.logs = self.store.node_directory(self.config["name"]) / "logs/validator"
        self.logs.mkdir(parents=True)

    def test_a_missing_log_is_reported_clearly(self):
        from evernode import nodes
        with self.assertRaisesRegex(process.OperationError, "No election-cron log yet"):
            nodes.build_node_log_command(self.store, self.config, "election-cron")

    def test_following_still_waits_for_a_log_that_does_not_exist_yet(self):
        from evernode import nodes
        command = nodes.build_node_log_command(
            self.store, self.config, "election-cron", follow=True
        )
        self.assertIn("-F", command)

    def test_an_existing_log_is_read(self):
        from evernode import nodes
        (self.logs / "election-cron.log").write_text("line\n")
        command = nodes.build_node_log_command(self.store, self.config, "election-cron")
        self.assertEqual(command[:3], ["tail", "-n", "100"])


class ScriptRefreshCommandTests(unittest.TestCase):
    def run_refresh(self, *flags, changed=True):
        plan = {"node": "validator01", "changed": changed, "components": {}}
        with (
            patch("evernode.cli.require_root_privileges"),
            patch("evernode.cli.storage.NodeStateStore") as store,
            patch("evernode.cli.elections.hold_election_lock"),
            patch("evernode.cli.provisioning.refresh_node_scripts",
                  return_value=plan) as refresh,
            patch("evernode.cli.ui.confirm_operation") as confirm,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            store.return_value.list_node_names.return_value = ["validator01"]
            store.return_value.load_node_config.return_value = operational_node()
            code = cli.main(["node", "sync-scripts", "-n", "validator01", *flags])
        return code, refresh, confirm

    def test_an_approved_run_renders_the_source_once(self):
        code, refresh, confirm = self.run_refresh("--yes")
        self.assertEqual(code, 0)
        self.assertEqual(refresh.call_count, 1)
        self.assertNotIn("dry_run", refresh.call_args.kwargs)
        confirm.assert_not_called()

    def test_a_dry_run_never_applies_anything(self):
        code, refresh, confirm = self.run_refresh("--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(refresh.call_count, 1)
        self.assertTrue(refresh.call_args.kwargs["dry_run"])
        confirm.assert_not_called()

    def test_an_interactive_run_shows_the_plan_then_asks(self):
        code, refresh, confirm = self.run_refresh()
        self.assertEqual(code, 0)
        self.assertEqual(refresh.call_count, 2)
        self.assertTrue(refresh.call_args_list[0].kwargs["dry_run"])
        confirm.assert_called_once()

    def test_nothing_is_applied_when_no_file_differs(self):
        code, refresh, confirm = self.run_refresh(changed=False)
        self.assertEqual(code, 0)
        self.assertEqual(refresh.call_count, 1)
        confirm.assert_not_called()

    def test_a_held_election_lock_fails_the_node_without_stopping_the_batch(self):
        with (
            patch("evernode.cli.require_root_privileges"),
            patch("evernode.cli.storage.NodeStateStore") as store,
            patch("evernode.cli.elections.hold_election_lock",
                  side_effect=process.OperationError("holds validator01's election lock")),
            patch("evernode.cli.provisioning.refresh_node_scripts") as refresh,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            store.return_value.list_node_names.return_value = ["validator01"]
            store.return_value.load_node_config.return_value = operational_node()
            code = cli.main(["node", "sync-scripts", "--all", "--yes"])
        self.assertEqual(code, 1)
        refresh.assert_not_called()
        self.assertIn("election lock", output.getvalue())


class ValidatorSetLifecycleGuardTests(unittest.TestCase):
    """Stopping a node that is in a validator set halted the test network.

    A current-set member misses the blocks it owes; a next-set member is
    required for its successor catchain session to reach consensus, so removing
    one from a small set stops the chain permanently.
    """

    def run_lifecycle(self, action, statistics, *flags):
        from evernode import nodes
        with (
            patch("evernode.cli.require_root_privileges"),
            patch("evernode.cli.storage.NodeStateStore") as store,
            patch("evernode.nodes.fetch_node_statistics", return_value=statistics),
            patch("evernode.nodes.stop_node") as stop,
            patch("evernode.nodes.start_node"),
            patch("evernode.nodes.remove_node") as remove,
            patch("evernode.elections.node_election_cron_path") as cron,
            contextlib.redirect_stdout(io.StringIO()) as output,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            cron.return_value.exists.return_value = False
            store.return_value.load_node_config.return_value = operational_node()
            argv = ["node", action, "-n", "validator01", *flags]
            if action == "remove":
                argv.append("--yes")  # Only remove asks for confirmation.
            code = cli.main(argv)
        return code, stop, remove, output.getvalue()

    def test_stopping_a_current_set_member_is_refused(self):
        code, stop, _, text = self.run_lifecycle(
            "stop", {"in_current_vset_p34": True, "in_next_vset_p36": False}
        )
        self.assertEqual(code, 1)
        stop.assert_not_called()
        self.assertIn("current validator set", text)
        self.assertIn("--force", text)

    def test_stopping_a_next_set_member_is_refused(self):
        code, stop, _, text = self.run_lifecycle(
            "stop", {"in_current_vset_p34": False, "in_next_vset_p36": True}
        )
        self.assertEqual(code, 1)
        stop.assert_not_called()
        self.assertIn("next validator set", text)

    def test_a_member_of_both_sets_names_both(self):
        _, _, _, text = self.run_lifecycle(
            "stop", {"in_current_vset_p34": True, "in_next_vset_p36": True}
        )
        self.assertIn("current and next validator set", text)

    def test_restart_and_remove_are_guarded_too(self):
        for action in ("restart", "remove"):
            with self.subTest(action=action):
                code, stop, remove, _ = self.run_lifecycle(
                    action, {"in_current_vset_p34": True}
                )
                self.assertEqual(code, 1)
                stop.assert_not_called()
                remove.assert_not_called()

    def test_force_fences_a_replaced_validator(self):
        code, stop, _, _ = self.run_lifecycle(
            "stop", {"in_current_vset_p34": True}, "--force"
        )
        self.assertEqual(code, 0)
        stop.assert_called_once()

    def test_a_node_outside_every_set_stops_without_force(self):
        code, stop, _, _ = self.run_lifecycle(
            "stop", {"in_current_vset_p34": False, "in_next_vset_p36": False}
        )
        self.assertEqual(code, 0)
        stop.assert_called_once()

    def test_unknown_membership_is_treated_as_a_member(self):
        """Fail closed: anything but an explicit negative counts as membership."""
        for value in ("yes", 1, "true", "True"):
            with self.subTest(value=value):
                code, stop, _, _ = self.run_lifecycle(
                    "stop", {"in_current_vset_p34": value}
                )
                self.assertEqual(code, 1)
                stop.assert_not_called()

    def test_an_unreachable_console_does_not_block_recovery(self):
        """A stopped or broken node must still be removable."""
        from evernode import nodes
        with (
            patch("evernode.cli.require_root_privileges"),
            patch("evernode.cli.storage.NodeStateStore") as store,
            patch("evernode.nodes.fetch_node_statistics",
                  side_effect=process.OperationError("container is not running")),
            patch("evernode.nodes.stop_node") as stop,
            patch("evernode.elections.node_election_cron_path") as cron,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            cron.return_value.exists.return_value = False
            store.return_value.load_node_config.return_value = operational_node()
            code = cli.main(["node", "stop", "-n", "validator01"])
        self.assertEqual(code, 0)
        stop.assert_called_once()

    def test_starting_a_node_is_never_guarded(self):
        """Starting a set member is how an operator recovers it, never a hazard."""
        _, _, _, text = self.run_lifecycle("start", {"in_current_vset_p34": True})
        self.assertNotIn("validator set", text)


class CronDaemonDetectionTests(unittest.TestCase):
    """An installed schedule runs nothing without a cron daemon."""

    def detect(self, *, pgrep="/usr/bin/pgrep", codes=None, error=None):
        from evernode import elections
        calls = []

        def run(command, **_):
            calls.append(command[-1])
            if error:
                raise error
            return unittest.mock.Mock(returncode=codes.pop(0))

        with (
            patch("evernode.elections.shutil.which", return_value=pgrep),
            patch("evernode.elections.subprocess.run", side_effect=run),
        ):
            return elections.cron_daemon_state(), calls

    def test_a_running_debian_cron_is_detected(self):
        self.assertEqual(self.detect(codes=[0])[0], "running")

    def test_a_running_crond_is_detected_after_cron_misses(self):
        state, calls = self.detect(codes=[1, 0])
        self.assertEqual(state, "running")
        self.assertEqual(calls, ["cron", "crond"])

    def test_no_daemon_at_all_is_reported(self):
        self.assertEqual(self.detect(codes=[1, 1])[0], "not running")

    def test_a_host_without_pgrep_reports_unknown_rather_than_guessing(self):
        self.assertEqual(self.detect(pgrep=None)[0], "unknown")

    def test_a_failing_pgrep_reports_unknown(self):
        self.assertEqual(self.detect(error=OSError("boom"))[0], "unknown")
