import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from evernode import (
    arguments,
    configuration,
    nodes,
    process,
    provisioning,
    scripts,
    storage,
    cli,
)
from support import make_node_configuration


def result(stdout=""):
    return subprocess.CompletedProcess([], 0, stdout, "")


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.mask = os.umask(0o077)
        self.addCleanup(os.umask, self.mask)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = storage.NodeStateStore(self.root / "state")
        self.source = self.root / "upstream"
        for name in ("scripts", "configs", "contracts"):
            (self.source / name).mkdir(parents=True)
            (self.source / name / "sentinel").write_text("original")
        (self.source / "scripts/env.sh").write_text(
            "".join(
                f"export {k}=old\n"
                for k in configuration.build_node_environment(make_node_configuration())
            )
        )
        guard = patch.object(storage.NodeStateStore, "validate_state_root")
        guard.start()
        self.addCleanup(guard.stop)
        for target, value in (
            ("evernode.docker.inspect_container", None),
            ("evernode.docker.is_host_port_available", True),
            ("evernode.process.execute_command", result("commit-one\n")),
        ):
            mock = patch(target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

    def create(self, name="node-01", **values):
        with self.store.acquire_operation_lock(name):
            c = make_node_configuration(name, **values)
            provisioning.provision_node_workspace(
                self.store, c, project_source=self.source
            )
        return c

    def test_source_capture_failure_leaves_resumable_reservation(self):
        with patch(
            "evernode.provisioning.capture_node_source_snapshot",
            side_effect=process.OperationError("source unavailable"),
        ):
            with self.assertRaisesRegex(process.OperationError, "unavailable"):
                self.create()
        c = self.store.load_node_config("node-01")
        self.assertEqual(c["prepare_stage"], "reserved")
        self.assertEqual(c["source_spec"]["local"], str(self.source.resolve()))
        provisioning.prepare_node_workspace(self.store, c)
        self.assertEqual(self.store.load_node_config("node-01")["phase"], "prepared")

    def test_interrupted_copy_resumes_from_snapshot_not_changed_upstream(self):
        original = shutil.copytree

        def copy(src, dst, *args, **kwargs):
            if ".prepared-source" in str(src) and Path(src).name == "configs":
                raise process.OperationError("interrupted copy")
            return original(src, dst, *args, **kwargs)

        with patch("evernode.provisioning.shutil.copytree", side_effect=copy):
            with self.assertRaisesRegex(process.OperationError, "interrupted copy"):
                self.create()
        (self.source / "configs/sentinel").write_text("new upstream")
        c = self.store.load_node_config("node-01")
        provisioning.prepare_node_workspace(self.store, c)
        node = self.store.node_directory(c["name"])
        self.assertEqual((node / "configs/sentinel").read_text(), "original")
        self.assertEqual(c["phase"], "prepared")
        self.assertFalse(any((node / "keys").iterdir()))
        self.assertEqual((node / "scripts/env.sh").stat().st_mode & 0o777, 0o600)

    def test_resume_completed_preparation_does_not_touch_keys(self):
        c = self.create()
        key = self.store.node_directory(c["name"]) / "keys/identity"
        key.write_text("keep")
        with patch("evernode.provisioning.capture_node_source_snapshot") as capture:
            provisioning.prepare_node_workspace(self.store, c)
        capture.assert_not_called()
        self.assertEqual(key.read_text(), "keep")

    def test_resume_refuses_modified_partial_workspace(self):
        c = self.create()
        c["phase"] = "preparing"
        (self.store.node_directory(c["name"]) / "scripts/env.sh").write_text(
            "local edits"
        )
        with self.assertRaisesRegex(process.OperationError, "refusing overwrite"):
            provisioning.prepare_node_workspace(self.store, c)

    def test_resume_refuses_existing_identity_before_writing_sources(self):
        c = self.create()
        c["phase"] = "preparing"
        key = self.store.node_directory(c["name"]) / "node_cfg/key"
        key.write_text("private")
        with patch("evernode.provisioning.capture_node_source_snapshot") as capture:
            with self.assertRaisesRegex(process.OperationError, "Identity files"):
                provisioning.prepare_node_workspace(self.store, c)
        capture.assert_not_called()
        self.assertEqual(key.read_text(), "private")

    def test_snapshot_tampering_and_symlink_are_rejected(self):
        c = self.create()
        snapshot = self.store.node_directory(c["name"]) / ".prepared-source"
        (snapshot / "contracts/sentinel").write_text("tampered")
        with self.assertRaisesRegex(process.OperationError, "modified"):
            provisioning.capture_node_source_snapshot(self.store, c)
        (snapshot / "contracts/sentinel").unlink()
        (snapshot / "contracts/sentinel").symlink_to(self.source / "contracts/sentinel")
        with self.assertRaisesRegex(process.OperationError, "linked"):
            provisioning.capture_node_source_snapshot(self.store, c)

    def test_second_node_keeps_first_workspace_and_reservations(self):
        first = self.create()
        before = storage.calculate_directory_fingerprint(
            self.store.node_directory(first["name"])
        )
        second = self.create("node-02", adnl_port=58889, metrics_port=9103)
        self.assertEqual(
            storage.calculate_directory_fingerprint(
                self.store.node_directory(first["name"])
            ),
            before,
        )
        self.assertEqual(first["image_id"], second["image_id"])
        with self.assertRaisesRegex(process.OperationError, "reserved"):
            self.create("node-03")
        self.assertFalse(self.store.node_directory("node-03").exists())

    def test_resume_cli_rejects_input_changes_and_dry_run_does_not_mutate(self):
        c = self.create()
        with (
            patch("evernode.cli.require_root_privileges"),
            patch("evernode.provisioning.prepare_node_workspace") as prepare,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            args = [
                "--state-dir",
                str(self.store.root),
                "node",
                "create",
                "--resume",
                "-n",
                c["name"],
            ]
            self.assertEqual(cli.main(args + ["--ip", "203.0.113.2"]), 1)
            self.assertEqual(cli.main(args + ["--dry-run"]), 0)
            prepare.assert_not_called()

    def test_all_continues_after_one_stop_failure(self):
        self.create()
        self.create("node-02", adnl_port=58889, metrics_port=9103)
        seen = []

        def stop(c, timeout):
            seen.append(c["name"])
            if c["name"] == "node-01":
                raise process.OperationError("cannot stop")

        with (
            patch("evernode.cli.require_root_privileges"),
            patch("evernode.nodes.stop_node", side_effect=stop),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = cli.main(
                ["--state-dir", str(self.store.root), "node", "stop", "--all"]
            )
        self.assertEqual(code, 1)
        self.assertEqual(seen, ["node-01", "node-02"])

    def test_same_node_lock_conflicts_other_node_and_registry_do_not(self):
        with self.store.acquire_operation_lock("node-01"):
            with self.assertRaisesRegex(process.OperationError, "holds"):
                with self.store.acquire_operation_lock("node-01"):
                    self.fail("lock should conflict")
            with (
                self.store.acquire_operation_lock("node-02"),
                self.store.acquire_operation_lock(),
            ):
                pass

    def test_metadata_rejects_wrong_types_and_injected_references(self):
        self.create()
        path = self.store.node_directory("node-01") / "node.json"
        for invalid in (
            [],
            make_node_configuration(memory="-1G"),
            make_node_configuration(adnl_port=True),
            make_node_configuration(image_id="not-an-id"),
            make_node_configuration(statsd="../x"),
            make_node_configuration(initialized="false"),
        ):
            storage.write_json_atomically(path, invalid)
            with self.assertRaises(process.OperationError):
                self.store.load_node_config("node-01")

    def test_metadata_private_mode_and_node_symlink_checks(self):
        self.create()
        path = self.store.node_directory("node-01") / "node.json"
        path.chmod(0o644)
        with self.assertRaisesRegex(process.OperationError, "private"):
            self.store.load_node_config("node-01")
        path.chmod(0o600)
        node = self.store.node_directory("node-01")
        moved = self.root / "moved"
        node.rename(moved)
        node.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(process.OperationError):
            self.store.load_node_config("node-01")

    def test_script_runner_checks_context_and_cleans_failed_docker_task(self):
        c = self.create()
        script = (
            self.store.node_directory(c["name"])
            / "scripts/init_scripts/R_gen_init_configs.sh"
        )
        script.parent.mkdir()
        script.write_text("exit 1\n")
        with (
            patch(
                "evernode.docker.execute_node_compose",
                side_effect=process.OperationError("init failed"),
            ) as compose,
            patch(
                "evernode.docker.inspect_managed_container",
                return_value={"managed": True},
            ),
            patch("evernode.process.execute_command") as run,
        ):
            with self.assertRaisesRegex(process.OperationError, "init failed"):
                scripts.execute_supported_node_script(
                    self.store, c, "init_scripts/R_gen_init_configs.sh"
                )
        self.assertIn("--workdir", compose.call_args.args)
        self.assertIn("/ever-node/scripts", compose.call_args.args)
        self.assertTrue(compose.call_args.kwargs["sensitive"])
        self.assertEqual(
            run.call_args.args[0], ["docker", "rm", "-f", "evernode-init-node-01"]
        )

    def test_script_runner_refuses_unreviewed_script(self):
        with self.assertRaisesRegex(process.OperationError, "Unsupported"):
            scripts.execute_supported_node_script(
                self.store, make_node_configuration(), "../arbitrary.sh"
            )

    def test_start_reclaims_removed_image_before_releasing_registry_lock(self):
        c = self.create()
        c["phase"] = "removed"
        self.store.save_node_config(c)

        def start(store, value):
            self.assertEqual(store.load_node_config(value["name"])["phase"], "prepared")
            with (
                store.acquire_operation_lock()
            ):  # Startup is outside the short registry lock.
                pass

        with (
            patch("evernode.cli.require_root_privileges"),
            patch("evernode.process.execute_command", return_value=result()),
            patch("evernode.nodes.start_node", side_effect=start),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                cli.main(
                    [
                        "--state-dir",
                        str(self.store.root),
                        "node",
                        "start",
                        "-n",
                        c["name"],
                    ]
                ),
                0,
            )

    def test_generated_configuration_must_match_network_and_paths(self):
        c = self.create()
        directory = self.store.node_directory(c["name"])
        cfg = directory / "node_cfg"
        network = directory / "configs/mainnet"
        network.mkdir()
        global_config = {"validator": {"zero_state": {"root_hash": "expected"}}}
        storage.write_json_atomically(network / "global.json", global_config)
        storage.write_json_atomically(cfg / "global.json", global_config)
        storage.write_json_atomically(
            cfg / "config.json",
            {
                "internal_db_path": "/ever-node/node_db",
                "adnl_node": {"key": "fixture"},
                "control_server": {"key": "fixture"},
                "ton_global_config_name": "/ever-node/node_cfg/global.json",
            },
        )
        storage.write_json_atomically(
            cfg / "default_config.json",
            {"ip_address": "203.0.113.1:58888", "control_server_port": 5888},
        )
        storage.write_json_atomically(
            cfg / "console.json",
            {
                "config": {
                    "server_address": "127.0.0.1:5888",
                    "client_key": {"fixture": "private"},
                    "server_key": {"fixture": "public"},
                }
            },
        )
        provisioning.validate_generated_node_configuration(directory, c)
        storage.write_json_atomically(
            cfg / "global.json", {"validator": {"zero_state": {"root_hash": "wrong"}}}
        )
        with self.assertRaisesRegex(process.OperationError, "selected source network"):
            provisioning.validate_generated_node_configuration(directory, c)

    def test_stop_checks_both_owners_before_disabling_cron(self):
        with (
            patch(
                "evernode.docker.inspect_managed_container",
                side_effect=[{}, process.OperationError("unmanaged exporter")],
            ),
            patch("evernode.elections.disable_node_election_schedule") as disable,
        ):
            with self.assertRaisesRegex(process.OperationError, "unmanaged exporter"):
                nodes.stop_node(make_node_configuration())
        disable.assert_not_called()


if __name__ == "__main__":
    unittest.main()
