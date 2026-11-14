"""evernode node log-level: the switch, what it refuses and the command line."""

import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evernode import log_level, process, storage
from evernode.arguments import build_argument_parser
from evernode.cli import main

from support import make_node_configuration

KIT_SETTINGS = (
    Path(__file__).resolve().parents[1]
    / "evernode/vendor/ever-validator/configs/rnode/log_cfg.yml"
).read_text()


class LogLevelTests(unittest.TestCase):
    def setUp(self):
        umask = os.umask(0o077)
        self.addCleanup(os.umask, umask)
        guard = patch.object(storage.NodeStateStore, "validate_state_root")
        guard.start()
        self.addCleanup(guard.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = temporary.name
        self.store = storage.NodeStateStore(self.root)

    def node(self, name, settings=KIT_SETTINGS):
        """A started node: its node_cfg holds the log settings the kit made."""
        self.store.node_directory(name).mkdir(parents=True)
        self.store.save_node_config(make_node_configuration(name))
        path = self.store.node_directory(name) / "node_cfg" / "log_cfg.yml"
        path.parent.mkdir()
        if settings is not None:
            path.write_text(settings)
        return path

    def run_command(self, *words):
        out = io.StringIO()
        with patch("evernode.cli.require_root_privileges"), contextlib.redirect_stdout(out):
            code = main(["--state-dir", self.root, "node", "log-level", *words])
        return code, out.getvalue()

    def test_new_nodes_start_at_info(self):
        self.node("validator01")
        self.assertEqual(log_level.read_level(self.store, "validator01"), "info")

    def test_a_switch_changes_only_the_five_components_and_switches_back(self):
        path = self.node("validator01")
        applies = log_level.set_level(self.store, "validator01", "debug")
        self.assertEqual(applies, "within 30 seconds")
        self.assertEqual(log_level.read_level(self.store, "validator01"), "debug")
        changed = [
            line for line, before in zip(path.read_text().splitlines(), KIT_SETTINGS.splitlines())
            if line != before
        ]
        self.assertEqual(changed, ["    level: debug"] * 5)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        log_level.set_level(self.store, "validator01", "info")
        self.assertEqual(path.read_text(), KIT_SETTINGS)

    def test_components_set_apart_by_hand_read_as_custom(self):
        self.node("validator01", KIT_SETTINGS.replace(
            "  telemetry:\n    level: info", "  telemetry:\n    level: debug"
        ))
        self.assertEqual(log_level.read_level(self.store, "validator01"), "custom")

    def test_refuses_a_node_not_started_and_a_file_it_does_not_know(self):
        self.node("fresh", settings=None)
        with self.assertRaisesRegex(process.OperationError, "no log settings yet"):
            log_level.set_level(self.store, "fresh", "debug")
        path = self.node("hand", settings="root:\n  level: info\n")
        with self.assertRaisesRegex(process.OperationError, "election kit's layout"):
            log_level.set_level(self.store, "hand", "debug")
        self.assertEqual(path.read_text(), "root:\n  level: info\n")

    def test_a_file_that_is_not_reread_applies_at_the_next_start(self):
        self.node("validator01", KIT_SETTINGS.replace("refresh_rate: 30 seconds\n", ""))
        self.assertEqual(
            log_level.set_level(self.store, "validator01", "debug"), "at the next node start"
        )

    def test_all_switches_every_node_and_reports_the_one_that_fails(self):
        self.node("validator01")
        self.node("validator02")
        self.node("fresh", settings=None)
        code, out = self.run_command("--all", "debug")
        self.assertEqual(code, 1)
        self.assertEqual(log_level.read_level(self.store, "validator01"), "debug")
        self.assertEqual(log_level.read_level(self.store, "validator02"), "debug")
        self.assertIn('"node": "fresh",\n  "result": "failed"', out)
        code, out = self.run_command("-n", "validator01")
        self.assertEqual(code, 0)
        self.assertIn('"log_level": "debug"', out)

    def test_the_level_is_info_or_debug(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_argument_parser().parse_args(["node", "log-level", "trace"])
        with self.assertRaisesRegex(process.OperationError, "info or debug"):
            log_level.set_level(self.store, "validator01", "trace")


if __name__ == "__main__":
    unittest.main()
