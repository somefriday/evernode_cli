"""Resource reporting stays read-only and isolates failures between nodes."""

import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from evernode import cli, docker, process, storage
from support import make_node_configuration

ORIGINAL_INSPECT = docker.inspect_managed_container


class ResourceTests(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.store = Mock(spec=storage.NodeStateStore)
        self.store.list_node_names.return_value = ["validator01", "validator02"]
        self.store.load_node_config.side_effect = make_node_configuration
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        stack.enter_context(contextlib.redirect_stdout(self.stdout))
        stack.enter_context(contextlib.redirect_stderr(self.stderr))
        self.inspect = stack.enter_context(
            patch("evernode.resources.docker.inspect_managed_container")
        )
        self.inspect.return_value = {
            "State": {"Status": "running", "OOMKilled": False},
            "HostConfig": {"Memory": 1024},
        }
        self.execute = stack.enter_context(
            patch("evernode.resources.process.execute_command")
        )
        self.execute.return_value = SimpleNamespace(stdout="CPU MEMORY\n")

    def run_cli(self, *args):
        with (
            patch("evernode.cli.require_root_privileges"),
            patch("evernode.cli.os.umask"),
            patch("evernode.cli.storage.NodeStateStore", return_value=self.store),
        ):
            return cli.main(["node", "resources", *args])

    def test_all_nodes_and_exporters_in_registry_order(self):
        self.assertEqual(self.run_cli(), 0)
        output = self.stdout.getvalue()
        self.assertLess(
            output.index("=== validator01 ==="), output.index("=== validator02 ===")
        )
        self.assertEqual(
            self.execute.call_args_list,
            [
                call(["docker", "stats", "--no-stream", c])
                for c in (
                    "validator01",
                    "statsd-validator01",
                    "validator02",
                    "statsd-validator02",
                )
            ],
        )
        self.assertEqual(
            self.store.load_node_config.call_args_list,
            [call("validator01"), call("validator02")],
        )
        self.store.save_node_config.assert_not_called()

    def test_explicit_node_keeps_existing_running_output(self):
        self.assertEqual(self.run_cli("-n", "validator02"), 0)
        self.store.list_node_names.assert_not_called()
        self.store.load_node_config.assert_called_once_with("validator02")
        output = self.stdout.getvalue()
        self.assertNotIn("===", output)
        self.assertNotIn('"status"', output)
        self.assertIn('"memory_limit_bytes": 1024', output)
        self.assertIn('"oom_killed": false', output)

    def test_no_nodes_needs_no_docker_calls(self):
        self.store.list_node_names.return_value = []
        self.assertEqual(self.run_cli(), 0)
        self.assertEqual(self.stdout.getvalue(), "No managed nodes found.\n")
        self.inspect.assert_not_called()
        self.execute.assert_not_called()

    def test_stopped_and_absent_containers_do_not_report_live_usage(self):
        self.inspect.side_effect = [
            {
                "State": {"Status": "exited", "OOMKilled": True},
                "HostConfig": {"Memory": 1024},
            },
            None,
        ]
        self.assertEqual(self.run_cli("-n", "validator01"), 0)
        self.execute.assert_not_called()
        output = self.stdout.getvalue()
        self.assertIn('"status": "exited"', output)
        self.assertIn('"status": "absent"', output)
        self.assertIn('"oom_killed": true', output)

    def test_invalid_node_metadata_does_not_hide_remaining_nodes(self):
        self.store.load_node_config.side_effect = [
            process.OperationError("Cannot read node configuration"),
            make_node_configuration("validator02"),
        ]
        self.assertEqual(self.run_cli(), 1)
        self.assertIn(
            "validator01: Cannot read node configuration", self.stderr.getvalue()
        )
        self.assertEqual(self.execute.call_count, 2)
        self.assertIn('"container": "validator02"', self.stdout.getvalue())

    def test_inspection_failure_continues_with_exporter_and_other_nodes(self):
        running = self.inspect.return_value
        self.inspect.side_effect = [
            process.OperationError("Docker unavailable"),
            running,
            running,
            running,
        ]
        self.assertEqual(self.run_cli(), 1)
        self.assertEqual(self.execute.call_count, 3)
        self.assertIn(
            "validator01/validator01: Docker unavailable", self.stderr.getvalue()
        )

    def test_stats_failure_during_container_exit_does_not_abort_batch(self):
        self.execute.side_effect = [
            process.OperationError("Container stopped"),
            *[SimpleNamespace(stdout="stats\n")] * 3,
        ]
        self.assertEqual(self.run_cli(), 1)
        self.assertEqual(self.execute.call_count, 4)
        self.assertIn("Container stopped", self.stderr.getvalue())

    def test_container_ownership_is_still_checked(self):
        self.inspect.side_effect = ORIGINAL_INSPECT
        with patch(
            "evernode.docker.inspect_container",
            return_value={
                "Config": {"Labels": {docker.NODE_OWNERSHIP_LABEL: "other-node"}}
            },
        ):
            self.assertEqual(self.run_cli("-n", "validator01"), 1)
        self.execute.assert_not_called()
        self.assertIn("not managed by this node", self.stderr.getvalue())

    def test_other_commands_still_require_selection_with_multiple_nodes(self):
        with self.assertRaisesRegex(process.OperationError, "Select a node"):
            cli.select_node_names(self.store, SimpleNamespace(name=None))
