"""Resource reporting stays read-only and isolates failures between nodes."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
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
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        for name in self.store.list_node_names.return_value:
            directory = root / name
            (directory / "node_db").mkdir(parents=True)
            (directory / "logs").mkdir()
        self.store.node_directory.side_effect = lambda name: root / name
        self.disk = stack.enter_context(
            patch("evernode.resources.disk_usage.measure_workspace")
        )
        self.disk.side_effect = lambda path: {
            path: 8192,
            path / "node_db": 4096,
            path / "logs": 1024,
        }
        self.filesystem = stack.enter_context(
            patch("evernode.resources.shutil.disk_usage")
        )
        self.filesystem.return_value = SimpleNamespace(total=1024**4, free=1024**3)
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
        self.execute.side_effect = lambda args: SimpleNamespace(
            stdout="CONTAINER ID NAME CPU MEMORY\n"
            + "".join(f"{container} CPU MEMORY\n" for container in args[3:])
        )

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
                call(["docker", "stats", "--no-stream", node, f"statsd-{node}"])
                for node in ("validator01", "validator02")
            ],
        )
        self.assertEqual(
            self.store.load_node_config.call_args_list,
            [call("validator01"), call("validator02")],
        )
        self.store.save_node_config.assert_not_called()
        self.assertEqual(self.disk.call_count, 2)
        self.filesystem.assert_called_once()
        self.assertEqual(output.count("1.0 TiB capacity, 1.0 GiB available"), 1)
        self.assertEqual(output.count("CONTAINER ID"), 2)
        self.assertEqual(output.count("Disk summary:"), 1)
        self.assertEqual(
            output.splitlines()[-1], "Disk summary: 1.0 TiB capacity, 1.0 GiB available"
        )
        self.assertNotIn(str(self.store.node_directory("validator01")), output)

    def test_explicit_node_shows_stats_and_disk_without_json_or_false_oom(self):
        self.assertEqual(self.run_cli("-n", "validator02"), 0)
        self.store.list_node_names.assert_not_called()
        self.store.load_node_config.assert_called_once_with("validator02")
        output = self.stdout.getvalue()
        self.assertNotIn("===", output)
        self.assertNotIn('"status"', output)
        self.assertIn("validator02 CPU MEMORY", output)
        self.assertIn("statsd-validator02 CPU MEMORY", output)
        self.assertEqual(output.count("CONTAINER ID"), 1)
        self.assertNotIn("memory_limit_bytes", output)
        self.assertNotIn("OOM", output)
        self.assertNotIn("{", output)
        self.assertIn("validator02 disk utilization", output)
        self.assertIn("3.0 KiB", output)
        self.assertEqual(output.count("Disk summary:"), 1)
        self.assertEqual(
            output.splitlines()[-1], "Disk summary: 1.0 TiB capacity, 1.0 GiB available"
        )
        self.assertNotIn(str(self.store.node_directory("validator02")), output)
        self.disk.assert_called_once_with(self.store.node_directory("validator02"))

    def test_no_nodes_needs_no_docker_calls(self):
        self.store.list_node_names.return_value = []
        self.assertEqual(self.run_cli(), 0)
        self.assertEqual(self.stdout.getvalue(), "No managed nodes found.\n")
        self.inspect.assert_not_called()
        self.execute.assert_not_called()
        self.disk.assert_not_called()

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
        self.assertIn("validator01: exited", output)
        self.assertIn("statsd-validator01: absent", output)
        self.assertEqual(output.count("was OOM-killed"), 1)
        self.assertIn("validator01 disk utilization", output)

    def test_invalid_node_metadata_does_not_hide_remaining_nodes(self):
        self.store.load_node_config.side_effect = [
            process.OperationError("Cannot read node configuration"),
            make_node_configuration("validator02"),
        ]
        self.assertEqual(self.run_cli(), 1)
        self.assertIn(
            "validator01: Cannot read node configuration", self.stderr.getvalue()
        )
        self.assertEqual(self.execute.call_count, 1)
        self.assertIn("validator02 CPU MEMORY", self.stdout.getvalue())

    def test_inspection_failure_continues_with_exporter_and_other_nodes(self):
        running = self.inspect.return_value
        self.inspect.side_effect = [
            process.OperationError("Docker unavailable"),
            running,
            running,
            running,
        ]
        self.assertEqual(self.run_cli(), 1)
        self.assertEqual(self.execute.call_count, 2)
        self.assertEqual(
            self.execute.call_args_list[0],
            call(["docker", "stats", "--no-stream", "statsd-validator01"]),
        )
        self.assertIn(
            "validator01/validator01: Docker unavailable", self.stderr.getvalue()
        )
        self.assertEqual(self.disk.call_count, 2)

    def test_stats_failure_during_container_exit_does_not_abort_batch(self):
        self.execute.side_effect = [
            process.OperationError("Container stopped"),
            SimpleNamespace(stdout="stats\n"),
        ]
        self.assertEqual(self.run_cli(), 1)
        self.assertEqual(self.execute.call_count, 2)
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

    def test_failed_disk_scan_does_not_hide_other_node_or_show_zero(self):
        good = self.store.node_directory("validator02")
        self.disk.side_effect = [process.OperationError("du timed out"), {good: 4096}]
        self.assertEqual(self.run_cli(), 1)
        self.assertIn(
            "validator01: disk report incomplete or unavailable", self.stderr.getvalue()
        )
        self.assertIn("du timed out", self.stderr.getvalue())
        self.assertEqual(self.stdout.getvalue().count("DATABASE"), 1)
        self.assertIn("validator02 disk utilization", self.stdout.getvalue())
        self.assertEqual(self.execute.call_count, 2)

    def test_disk_report_still_works_when_docker_is_unavailable(self):
        self.inspect.side_effect = process.OperationError("Docker unavailable")
        self.assertEqual(self.run_cli(), 1)
        self.assertEqual(self.disk.call_count, 2)
        self.assertEqual(self.stdout.getvalue().count("DATABASE"), 2)

    def test_separate_database_filesystem_is_reported_once(self):
        database = self.store.node_directory("validator02") / "node_db"
        with patch.object(
            Path,
            "stat",
            autospec=True,
            side_effect=lambda path: SimpleNamespace(
                st_dev=2 if path == database else 1
            ),
        ):
            self.assertEqual(self.run_cli(), 0)
        self.assertEqual(self.filesystem.call_count, 2)
        output = self.stdout.getvalue()
        self.assertEqual(output.count("Disk summary:"), 1)
        self.assertEqual(
            output.splitlines()[-1], "Disk summary: 2.0 TiB capacity, 2.0 GiB available"
        )

    def test_filesystem_failure_does_not_hide_later_node(self):
        self.filesystem.side_effect = [
            OSError("Filesystem unavailable"),
            SimpleNamespace(total=1024**4, free=1024**3),
        ]
        self.assertEqual(self.run_cli(), 1)
        self.assertIn("Filesystem unavailable", self.stderr.getvalue())
        self.assertEqual(self.stdout.getvalue().count("DATABASE"), 2)
        self.assertEqual(
            self.stdout.getvalue().splitlines()[-1],
            "Disk summary: unavailable (incomplete measurement)",
        )
