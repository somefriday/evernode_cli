"""Regression checks for public commands and moved orchestration boundaries."""

import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from evernode import arguments, cli, docker, nodes, process, storage
from support import make_node_configuration


class CommandContractTests(unittest.TestCase):
    def test_creation_and_resume_flags_keep_their_public_names(self):
        parser = arguments.build_argument_parser()
        create = parser.parse_args(
            [
                "node",
                "create",
                "-n",
                "validator-02",
                "--image",
                "local/node:1",
                "--ip",
                "203.0.113.2",
                "--memory",
                "40G",
                "--network",
                "main",
                "--adnl-port",
                "58889",
                "--metrics-port",
                "9103",
                "--project-source",
                "/root/Ever-Validator",
                "--yes",
                "--dry-run",
            ]
        )
        self.assertEqual(
            (create.name, create.adnl_port, create.metrics_port),
            ("validator-02", 58889, 9103),
        )
        self.assertTrue(create.dry_run)
        resume = parser.parse_args(["node", "create", "--resume", "-n", "validator-02"])
        self.assertTrue(resume.resume)
        self.assertIsNone(resume.image)

    def test_stop_keeps_explicit_all_and_default_grace_period(self):
        parser = arguments.build_argument_parser()
        args = parser.parse_args(["node", "stop", "--all"])
        self.assertTrue(args.all)
        self.assertEqual(args.timeout, 30)
        args = parser.parse_args(
            ["node", "stop", "-n", "validator-01", "--timeout", "120"]
        )
        self.assertFalse(args.all)
        self.assertEqual(args.timeout, 120)

    def test_unsupported_commands_fail_without_ambiguity(self):
        for command in (
            ["node", "continue", "-n", "validator"],
            ["elections", "enable"],
            ["node", "create", "--existing-depool", "address"],
        ):
            with (
                self.subTest(command=command),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit) as error:
                    arguments.build_argument_parser().parse_args(command)
                self.assertEqual(error.exception.code, 2)

    def test_explicit_wallet_and_depool_actions_are_available(self):
        parser = arguments.build_argument_parser()
        for command in (
            ["wallet", "create", "-n", "validator"],
            ["wallet", "recover", "-n", "validator"],
            ["wallet", "verify", "-n", "validator"],
            ["depool", "prepare", "-n", "validator"],
            ["depool", "deploy", "-n", "validator"],
            ["depool", "stake-initial", "-n", "validator"],
            ["depool", "verify", "-n", "validator"],
        ):
            with self.subTest(command=command):
                args = parser.parse_args(command)
                self.assertEqual(args.name, "validator")

    def test_host_command_delegates_and_preserves_exit_status(self):
        report = {
            "platform": "linux",
            "commands": {"git": "/usr/bin/git"},
            "docker": {"error": "unavailable"},
            "compose": "v2",
        }
        with (
            patch("evernode.host.collect_host_dependency_report", return_value=report),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
        ):
            self.assertEqual(cli.main(["host", "check"]), 1)
        self.assertIn("unavailable", stdout.getvalue())

    def test_host_setup_dry_run_uses_detected_distribution_plan(self):
        with (
            patch("evernode.cli.require_root_privileges"),
            patch(
                "evernode.host.get_setup_plan",
                return_value="install from Docker's official Debian repository",
            ),
            patch("evernode.host.install_host_dependencies") as install,
            contextlib.redirect_stdout(io.StringIO()) as stdout,
        ):
            self.assertEqual(cli.main(["host", "setup", "--dry-run"]), 0)
        self.assertIn("Docker's official Debian repository", stdout.getvalue())
        install.assert_not_called()

    def test_log_paths_and_container_targets_stay_node_specific(self):
        config = make_node_configuration()
        store = storage.NodeStateStore("/var/lib/ever-validator")
        for component, filename in (
            ("node", "node.log"),
            ("stderr", "stderr.log"),
            ("stdout", "stdout.log"),
        ):
            command = nodes.build_node_log_command(
                store, config, component, tail=25, follow=True
            )
            self.assertEqual(
                command,
                [
                    "tail",
                    "-n",
                    "25",
                    "-F",
                    str(
                        Path("/var/lib/ever-validator/nodes/node-01/logs/node")
                        / filename
                    ),
                ],
            )
        self.assertTrue(
            nodes.build_node_log_command(store, config, "elections")[-1].endswith(
                "/logs/validator/validator.log"
            )
        )
        with patch.object(
            docker, "inspect_managed_container", return_value={"owned": True}
        ):
            self.assertEqual(
                nodes.build_node_log_command(store, config, "statsd", tail=5),
                ["docker", "logs", "--tail", "5", "statsd-node-01"],
            )
            self.assertEqual(
                nodes.build_node_log_command(store, config, "startup", follow=True)[-1],
                "node-01",
            )

    def test_lifecycle_rejects_unknown_actions_before_accessing_state(self):
        with self.assertRaisesRegex(process.OperationError, "Unsupported"):
            nodes.apply_node_lifecycle_operation(None, "node-01", "unknown")
