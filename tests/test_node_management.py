import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from evernode import configuration, docker, elections, nodes, process, storage
from evernode.cli import select_node_names, main
from evernode.arguments import build_argument_parser


from support import make_node_configuration


class ConfigTests(unittest.TestCase):
    def setUp(self):
        # Tests exercise real file modes/locks under an unprivileged temp root.
        # Production still requires root-owned ancestors through check_root.
        self.old_umask = os.umask(0o077)
        self.addCleanup(os.umask, self.old_umask)
        guard = patch.object(storage.NodeStateStore, "validate_state_root")
        guard.start()
        self.addCleanup(guard.stop)

    def test_names_reject_paths_and_shell_input(self):
        for value in ("../keys", "UPPER", "a/b", "x;id", "-node", ""):
            with self.assertRaises(process.OperationError):
                configuration.validate_node_name(value)

    def test_compose_exposes_only_adnl_publicly(self):
        config = dict(
            name="node-01",
            statsd="statsd-node-01",
            image_id="sha256:abc",
            memory="40G",
            adnl_port=58888,
            metrics_port=9102,
        )
        c = docker.build_node_compose_config(
            config, Path("/var/lib/ever-validator/nodes/node-01")
        )
        self.assertEqual(c["services"]["node"]["ports"], ["58888:58888/udp"])
        self.assertEqual(c["services"]["statsd"]["ports"], ["127.0.0.1:9102:9102"])
        self.assertNotIn("volumes", c["services"]["statsd"])
        self.assertNotIn("entrypoint.sh", str(c))
        self.assertNotIn("/var/run/docker.sock", str(c))

    def test_env_preserves_other_assignments_and_quotes_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "env.sh"
            p.write_text("export NAME=old\nOTHER=keep\n")
            configuration.write_node_environment(p, {"NAME": "a b"})
            self.assertEqual(p.read_text(), "export NAME='a b'\nOTHER=keep\n")
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(process.OperationError):
                configuration.write_node_environment(p, {"MISSING": "x"})

    def test_sync_requires_both_numeric_lags_and_correct_phase(self):
        good = dict(
            node_status="synchronization_by_blocks", timediff=5, shards_timediff=4
        )
        self.assertTrue(nodes.is_node_synchronized(good))
        for updates in (
            {"shards_timediff": "unknown"},
            {"timediff": 11},
            {"node_status": "load_master_state"},
            {"timediff": -5},
        ):
            self.assertFalse(nodes.is_node_synchronized(dict(good, **updates)))

    def test_discard_failed_creation_removes_all_node_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = storage.NodeStateStore(Path(temporary) / "state")
            directory = store.node_directory("node-01")
            for item in ("node_db", "logs", "keys", "node_cfg", "scripts"):
                (directory / item).mkdir(parents=True, exist_ok=True)
            config = make_node_configuration()
            store.save_node_config(config)
            with (
                patch("evernode.docker.inspect_container", return_value=None),
                patch(
                    "evernode.elections.node_election_cron_path",
                    return_value=Path(temporary) / "evernode-node-01",
                ),
            ):
                nodes.discard_failed_creation(store, config)
            self.assertFalse(directory.exists())

    def test_port_selection_respects_saved_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = storage.NodeStateStore(tmp)
            d = s.node_directory("node-01")
            d.mkdir(parents=True)
            s.save_node_config(make_node_configuration())
            with patch("evernode.docker.is_host_port_available", return_value=True):
                self.assertEqual(
                    docker.select_available_node_port(s, 58888, "udp"), 58889
                )
                with self.assertRaises(process.OperationError):
                    docker.select_available_node_port(s, 58888, "udp", 58888)

    def test_refuses_unmanaged_container(self):
        with patch(
            "evernode.docker.inspect_container", return_value={"Config": {"Labels": {}}}
        ):
            with self.assertRaises(process.OperationError):
                docker.inspect_managed_container("node-01", "node-01")

    def test_remove_preserves_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = storage.NodeStateStore(tmp)
            d = store.node_directory("node-01")
            d.mkdir(parents=True)
            node_config = make_node_configuration()
            for directory in ("keys", "node_cfg", "elections", "node_db", "logs"):
                (d / directory).mkdir()
                (d / directory / "sentinel").write_text("keep")
            with (
                patch("evernode.nodes.stop_node"),
                patch("evernode.docker.inspect_managed_container", return_value=None),
            ):
                nodes.remove_node(store, node_config, purge=True)
            for directory in ("keys", "node_cfg", "elections"):
                self.assertTrue((d / directory / "sentinel").exists())
            self.assertFalse((d / "node_db/sentinel").exists())

    def test_election_stop_rejects_unrecognized_cron(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cron"
            p.write_text("unrelated job")
            with patch("evernode.elections.node_election_cron_path", return_value=p):
                with self.assertRaises(process.OperationError):
                    elections.disable_node_election_schedule("node-01")
                p.write_text("# Managed by evernode\njob")
                elections.disable_node_election_schedule("node-01")
                self.assertFalse(p.exists())

    def test_ambiguous_selection_never_targets_all(self):
        with patch.object(
            storage.NodeStateStore, "list_node_names", return_value=["a", "b"]
        ):
            with self.assertRaises(process.OperationError):
                select_node_names(
                    storage.NodeStateStore("/unused"),
                    build_argument_parser().parse_args(["node", "stop"]),
                )

    def test_dry_run_does_not_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = storage.NodeStateStore(tmp)
            s.node_directory("a").mkdir(parents=True)
            s.save_node_config(make_node_configuration("a"))
            with (
                patch("evernode.cli.require_root_privileges"),
                patch("evernode.nodes.stop_node") as stop,
            ):
                self.assertEqual(
                    main(["--state-dir", tmp, "node", "stop", "-n", "a", "--dry-run"]),
                    0,
                )
                stop.assert_not_called()

    def test_partial_init_refuses_to_overwrite_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = storage.NodeStateStore(tmp)
            d = s.node_directory("node-01")
            (d / "node_cfg").mkdir(parents=True)
            key = d / "node_cfg" / "key"
            key.write_text("existing")
            config = dict(
                name="node-01",
                statsd="statsd-node-01",
                phase="prepared",
                initialized=False,
            )
            with (
                patch("evernode.docker.inspect_managed_container"),
                patch("evernode.docker.execute_node_compose"),
            ):
                with self.assertRaisesRegex(
                    process.OperationError, "Refusing to regenerate keys"
                ):
                    nodes.start_node(s, config)
            self.assertEqual(key.read_text(), "existing")


class LocalSyncTests(unittest.TestCase):
    def setUp(self):
        self.old_umask = os.umask(0o077)
        self.addCleanup(os.umask, self.old_umask)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = storage.NodeStateStore(self.temporary.name)
        guard = patch.object(storage.NodeStateStore, "validate_state_root")
        guard.start()
        self.addCleanup(guard.stop)
        self.source = self._create_node("source", "source-state")
        self.target = self._create_node("target", "target-state")

    def _create_node(self, name, payload):
        directory = self.store.node_directory(name)
        for path in (
            directory,
            directory / "node_db",
            directory / "node_db/catchains",
            directory / "node_cfg",
        ):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(path, 0o700)
        (directory / "node_db/state").write_text(payload)
        (directory / "node_db/catchains/private-state").write_text("do-not-copy")
        (directory / "node_cfg/config.json").write_text(
            '{"ton_global_config_name":"/ever-node/node_cfg/ton-global.config.json"}'
        )
        (directory / "node_cfg/ton-global.config.json").write_text('{"network":"main"}')
        for path in (
            directory / "node_db/state",
            directory / "node_db/catchains/private-state",
            directory / "node_cfg/config.json",
            directory / "node_cfg/ton-global.config.json",
        ):
            os.chmod(path, 0o600)
        config = make_node_configuration(name, initialized=True, phase="console-ready")
        self.store.save_node_config(config)
        return config

    @staticmethod
    def _statistics(config):
        if config["name"] == "source":
            return {
                "node_status": "synchronization_finished",
                "timediff": 0,
                "shards_timediff": 0,
                "masterchainblocknumber": 123,
                "in_current_vset_p34": False,
                "in_next_vset_p36": False,
            }
        return {
            "node_status": "load_master_state",
            "timediff": 100,
            "shards_timediff": "unknown",
        }

    def test_parser_exposes_local_sync_with_unambiguous_names(self):
        args = build_argument_parser().parse_args(
            ["node", "lsync", "--from", "source", "--to", "target", "--yes"]
        )
        self.assertEqual((args.source, args.target), ("source", "target"))
        self.assertTrue(args.yes)

    def test_local_sync_preflight_requires_explicit_safe_source_membership(self):
        unsafe = dict(self._statistics(self.source), in_current_vset_p34="unknown")
        with patch(
            "evernode.nodes.fetch_node_statistics",
            side_effect=lambda config: (
                unsafe if config["name"] == "source" else self._statistics(config)
            ),
        ):
            with self.assertRaisesRegex(process.OperationError, "explicitly report"):
                nodes.inspect_local_sync(self.store, "source", "target")

    def test_local_sync_copies_only_database_without_catchains_or_identity(self):
        commands, stopped, started = [], [], []

        def copy_database(command, **_kwargs):
            commands.append(command)
            self.assertEqual(
                command[:7],
                [
                    "rsync",
                    "-aH",
                    "--numeric-ids",
                    "--sparse",
                    "--delete",
                    "--info=progress2",
                    "--exclude",
                ],
            )
            self.assertIn("/catchains/", command)
            shutil.copytree(
                Path(str(command[-2]).rstrip("/")),
                Path(str(command[-1]).rstrip("/")),
                ignore=shutil.ignore_patterns("catchains"),
            )

        with (
            patch("evernode.nodes.fetch_node_statistics", side_effect=self._statistics),
            patch(
                "evernode.nodes.stop_node",
                side_effect=lambda config, *args, **kwargs: stopped.append(
                    config["name"]
                ),
            ),
            patch(
                "evernode.nodes.start_node",
                side_effect=lambda _store, config: started.append(config["name"]),
            ),
            patch("evernode.nodes.process.execute_command", side_effect=copy_database),
        ):
            result = nodes.local_sync(self.store, "source", "target")

        target_dir = self.store.node_directory("target")
        self.assertEqual((target_dir / "node_db/state").read_text(), "source-state")
        self.assertFalse((target_dir / "node_db/catchains").exists())
        self.assertEqual(
            (target_dir / "node_cfg/config.json").read_text(),
            '{"ton_global_config_name":"/ever-node/node_cfg/ton-global.config.json"}',
        )
        self.assertEqual(
            (stopped, started), (["target", "source"], ["source", "target"])
        )
        self.assertEqual(result["source"], "source")
        self.assertEqual(len(commands), 1)
        self.assertTrue((target_dir / "lsync.json").exists())

    def test_local_sync_restores_target_database_when_target_start_fails(self):
        starts = []

        def copy_database(command, **_kwargs):
            shutil.copytree(
                Path(str(command[-2]).rstrip("/")),
                Path(str(command[-1]).rstrip("/")),
                ignore=shutil.ignore_patterns("catchains"),
            )

        def start(_store, config):
            starts.append(config["name"])
            if config["name"] == "target" and starts.count("target") == 1:
                raise process.OperationError("target console unavailable")

        with (
            patch("evernode.nodes.fetch_node_statistics", side_effect=self._statistics),
            patch("evernode.nodes.stop_node"),
            patch("evernode.nodes.start_node", side_effect=start),
            patch("evernode.nodes.process.execute_command", side_effect=copy_database),
        ):
            with self.assertRaisesRegex(process.OperationError, "Local sync failed"):
                nodes.local_sync(self.store, "source", "target")

        target_dir = self.store.node_directory("target")
        self.assertEqual((target_dir / "node_db/state").read_text(), "target-state")
        self.assertFalse(
            any(path.name.endswith(".backup") for path in target_dir.iterdir())
        )
        self.assertEqual(starts, ["source", "target", "target"])


if __name__ == "__main__":
    unittest.main()
