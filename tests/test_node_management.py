import base64
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from evernode import configuration, docker, elections, images, nodes, process, storage
from evernode.cli import handle_node_local_sync, select_node_names, main
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

    def test_node_may_keep_its_database_files_open(self):
        """Docker's 1024 stopped mainnet nodes at "Too many open files"."""
        c = docker.build_node_compose_config(
            make_node_configuration(), Path("/var/lib/ever-validator/nodes/node-01")
        )
        self.assertEqual(
            c["services"]["node"]["ulimits"],
            {"nofile": {"soft": 1048576, "hard": 1048576}},
        )

    def test_start_applies_the_current_container_definition_to_a_stopped_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = storage.NodeStateStore(tmp)
            config = make_node_configuration(initialized=True)
            compose = s.node_directory("node-01") / "compose.json"
            compose.parent.mkdir(parents=True)
            for state, rewritten in ((None, True), ("exited", True), ("running", False)):
                compose.write_text("{}")
                found = state and {"State": {"Status": state}}
                with (
                    patch("evernode.docker.inspect_managed_container", return_value=found),
                    patch("evernode.docker.execute_node_compose"),
                    patch("evernode.nodes.fetch_node_statistics", return_value={}),
                    patch.object(s, "save_node_config"),
                ):
                    nodes.start_node(s, config)
                written = json.loads(compose.read_text())
                self.assertEqual(
                    "ulimits" in written.get("services", {}).get("node", {}), rewritten, state
                )

    def test_env_preserves_other_assignments_and_quotes_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "env.sh"
            p.write_text("export NAME=old\nOTHER=keep\n")
            configuration.write_node_environment(p, {"NAME": "a b"})
            self.assertEqual(p.read_text(), "export NAME='a b'\nOTHER=keep\n")
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(process.OperationError):
                configuration.write_node_environment(p, {"MISSING": "x"})

    def test_env_is_replaced_whole_never_rewritten_in_place(self):
        """An election run sourcing env.sh mid-write read a truncated file."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "env.sh"
            p.write_text("export NAME=old\n")
            before = p.stat().st_ino
            configuration.write_node_environment(p, {"NAME": "new"})
            self.assertNotEqual(p.stat().st_ino, before)
            self.assertEqual(p.read_text(), "export NAME=new\n")
            self.assertEqual([path.name for path in Path(tmp).iterdir()], ["env.sh"])

    def test_sync_requires_both_numeric_lags_and_correct_phase(self):
        good = dict(
            node_status="synchronization_by_blocks", timediff=5, shards_timediff=4
        )
        self.assertTrue(nodes.is_node_synchronized(good))
        for updates in (
            {"shards_timediff": "unknown"},
            {"timediff": 11},
            {"node_status": "load_master_state"},
            {"timediff": -11},
        ):
            self.assertFalse(nodes.is_node_synchronized(dict(good, **updates)))

    def test_a_late_clock_is_not_a_node_behind(self):
        """E4: ever-node's lags are u32 `now - gen_utime`, so a block stamped
        ahead of this server's clock wraps to just under 2**32."""
        good = dict(node_status="synchronization_finished", timediff=3, shards_timediff=2)
        self.assertEqual(nodes.clock_behind(good), 0)
        self.assertEqual(nodes.clock_behind(dict(good, shards_timediff="unknown")), 0)
        for lag, synchronized, behind in (
            (-5, True, 5),
            (4294967291, True, 5),
            (-10, True, 10),
            (-11, False, 11),
            (4294967285, False, 11),
            (2147483647, False, 0),
        ):
            with self.subTest(lag=lag):
                for key in ("timediff", "shards_timediff"):
                    value = dict(good, **{key: lag})
                    self.assertEqual(nodes.is_node_synchronized(value), synchronized)
                    self.assertEqual(nodes.clock_behind(value), behind)

    def test_statistics_carry_signed_lags(self):
        reading = {"node_status": "synchronization_finished", "timediff": 4294967285,
                   "shards_timediff": 4294967294}
        with patch("evernode.nodes._run_console", return_value=reading):
            value = nodes.fetch_node_statistics(make_node_configuration("validator01"))
        self.assertEqual((value["timediff"], value["shards_timediff"]), (-11, -2))
        self.assertIn("clock is 11s behind", nodes.late_clock(nodes.clock_behind(value)))

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
            config = make_node_configuration("node-01")
            with (
                patch("evernode.docker.inspect_managed_container", return_value=None),
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
            directory / "elections",
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

    def _target_fails_to_start(self, raised, remove=None):
        """Run local sync where the target does not start on the copy; return the starts."""
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
            patch("evernode.nodes._remove_directory", side_effect=remove)
            if remove else contextlib.nullcontext(),
        ):
            with self.assertRaises(raised):
                nodes.local_sync(self.store, "source", "target")
        self.assertEqual(
            (self.store.node_directory("target") / "node_db/state").read_text(), "target-state"
        )
        return starts

    def test_local_sync_restores_target_database_when_target_start_fails(self):
        self.assertEqual(
            self._target_fails_to_start(process.OperationError), ["source", "target", "target"]
        )
        target_dir = self.store.node_directory("target")
        self.assertFalse(
            any(path.name.endswith(".backup") for path in target_dir.iterdir())
        )

    def test_a_copy_that_cannot_be_deleted_does_not_hide_why_the_sync_failed(self):
        def remove(path):
            raise OSError("Directory not empty")

        with contextlib.redirect_stderr(io.StringIO()) as errors:
            starts = self._target_fails_to_start(process.OperationError, remove)
        self.assertEqual(starts, ["source", "target", "target"])
        self.assertIn(".failed was not removed (Directory not empty); delete it by hand", errors.getvalue())

    def test_an_old_database_that_cannot_be_deleted_does_not_fail_a_finished_sync(self):
        def copy_database(command, **_kwargs):
            shutil.copytree(
                Path(str(command[-2]).rstrip("/")),
                Path(str(command[-1]).rstrip("/")),
                ignore=shutil.ignore_patterns("catchains"),
            )

        def remove(path):
            if path.name.endswith(".backup"):
                raise OSError("Directory not empty")

        with (
            patch("evernode.nodes.fetch_node_statistics", side_effect=self._statistics),
            patch("evernode.nodes.stop_node"),
            patch("evernode.nodes.start_node"),
            patch("evernode.nodes.process.execute_command", side_effect=copy_database),
            patch("evernode.nodes._remove_directory", side_effect=remove),
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            result = nodes.local_sync(self.store, "source", "target")
        self.assertEqual(result["target"], "target")
        self.assertIn(".backup was not removed", errors.getvalue())

    def test_an_interrupt_while_the_failed_copy_is_deleted_keeps_the_old_database(self):
        """Deleting a whole copy takes minutes, so it comes after the restore and the restarts."""

        def remove(path):
            if path.name.endswith(".failed"):
                raise KeyboardInterrupt

        self.assertEqual(
            self._target_fails_to_start(KeyboardInterrupt, remove), ["source", "target", "target"]
        )

    def test_an_interrupt_while_the_old_database_is_removed_keeps_the_copy(self):
        """The target already runs on the copy, so only the old database is left."""
        stopped = []

        def copy_database(command, **_kwargs):
            shutil.copytree(
                Path(str(command[-2]).rstrip("/")),
                Path(str(command[-1]).rstrip("/")),
                ignore=shutil.ignore_patterns("catchains"),
            )

        def remove(path):
            if path.name.endswith(".backup"):
                (path / "state").unlink()  # cut short part-way through
                raise KeyboardInterrupt

        with (
            patch("evernode.nodes.fetch_node_statistics", side_effect=self._statistics),
            patch(
                "evernode.nodes.stop_node",
                side_effect=lambda config, *_, **__: stopped.append(config["name"]),
            ),
            patch("evernode.nodes.start_node"),
            patch("evernode.nodes.process.execute_command", side_effect=copy_database),
            patch("evernode.nodes._remove_directory", side_effect=remove),
        ):
            with self.assertRaises(KeyboardInterrupt):
                nodes.local_sync(self.store, "source", "target")

        target_dir = self.store.node_directory("target")
        self.assertEqual((target_dir / "node_db/state").read_text(), "source-state")
        self.assertEqual(stopped, ["target", "source"])
        self.assertTrue((target_dir / "lsync.json").exists())

    def _interrupt(self, *, stop=None, copy=None):
        """Run local sync with these side effects; return the nodes started again."""
        started = []
        with (
            patch("evernode.nodes.fetch_node_statistics", side_effect=self._statistics),
            patch("evernode.nodes.stop_node", side_effect=stop),
            patch(
                "evernode.nodes.start_node",
                side_effect=lambda _store, config: started.append(config["name"]),
            ),
            patch("evernode.nodes.process.execute_command", side_effect=copy),
        ):
            with self.assertRaisesRegex(
                process.OperationError, "Local sync failed: interrupted"
            ):
                nodes.local_sync(self.store, "source", "target")
        return started

    def test_an_interrupted_copy_starts_both_nodes_on_their_own_databases(self):
        """Ctrl+C reaches only evernode, because rsync runs in its own session."""
        self.assertEqual(self._interrupt(copy=KeyboardInterrupt), ["target", "source"])
        target_dir = self.store.node_directory("target")
        self.assertEqual((target_dir / "node_db/state").read_text(), "target-state")
        self.assertEqual(list(target_dir.glob(".node_db-lsync-*")), [])

    def test_an_interrupt_while_the_target_stops_starts_it_again(self):
        """A stop cut short may still complete, so the target is started anyway."""
        self.assertEqual(self._interrupt(stop=KeyboardInterrupt), ["target"])

    def test_a_closed_session_or_a_kill_interrupts_the_copy_like_ctrl_c(self):
        """Ending the process outright would leave both nodes stopped."""
        stops = (signal.SIGHUP, signal.SIGTERM)
        for stop in stops:
            self.addCleanup(signal.signal, stop, signal.getsignal(stop))
        handlers = []

        def copy(*_):
            handlers.extend(signal.getsignal(stop) for stop in stops)
            return {}

        args = build_argument_parser().parse_args(
            ["node", "lsync", "--from", "source", "--to", "target", "--yes"]
        )
        with (
            patch("evernode.cli.nodes.inspect_local_sync", return_value={}),
            patch("evernode.cli.nodes.local_sync", side_effect=copy),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            handle_node_local_sync(self.store, args)
        self.assertEqual(handlers, [signal.default_int_handler] * 2)

    def test_a_running_election_keeps_both_nodes_from_being_stopped(self):
        """A run that began before election stop still signs through its node."""
        with (
            patch("evernode.nodes.fetch_node_statistics", side_effect=self._statistics),
            patch("evernode.nodes.stop_node") as stop,
            elections.hold_election_lock(self.store, "target"),
        ):
            for check in (nodes.inspect_local_sync, nodes.local_sync):
                with self.subTest(check=check.__name__):
                    with self.assertRaisesRegex(process.OperationError, "election lock"):
                        check(self.store, "source", "target")
        stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class ValidatorSetGuardTests(unittest.TestCase):
    """Taking a set member out costs the blocks it owes, so refuse by default."""

    def setUp(self):
        self.config = make_node_configuration("validator01")

    def refuse(self, **membership):
        reading = {
            "node_status": "synchronization_finished",
            "timediff": 2,
            "shards_timediff": 2,
            "in_current_vset_p34": False,
            "in_next_vset_p36": False,
        }
        reading.update(membership)
        with patch("evernode.nodes.fetch_node_statistics", return_value=reading):
            nodes._refuse_while_validating(self.config, "stop")

    def test_a_member_of_either_set_is_named_and_refused(self):
        cases = [
            ({"in_current_vset_p34": True}, "in the current validator set"),
            ({"in_next_vset_p36": True}, "in the next validator set"),
            (
                {"in_current_vset_p34": True, "in_next_vset_p36": True},
                "in the current and next validator set",
            ),
        ]
        for membership, expected in cases:
            with self.subTest(membership=membership):
                with self.assertRaises(process.OperationError) as caught:
                    self.refuse(**membership)
                message = str(caught.exception)
                self.assertIn(expected, message)
                self.assertIn("--force", message)

    def test_a_node_outside_both_sets_is_allowed(self):
        self.refuse()

    def test_an_unproven_membership_is_treated_as_membership(self):
        # Fail closed: a field that is not explicitly false proves nothing.
        with self.assertRaises(process.OperationError):
            self.refuse(in_current_vset_p34="unknown")

    def test_a_synchronized_node_that_does_not_report_membership_is_refused(self):
        """A node version that stops reporting these fields must not fail open."""
        reading = {"node_status": "synchronization_finished", "timediff": 2,
                   "shards_timediff": 2}
        with patch("evernode.nodes.fetch_node_statistics", return_value=reading):
            with self.assertRaises(process.OperationError) as caught:
                nodes._refuse_while_validating(self.config, "stop")
        message = str(caught.exception)
        self.assertIn("silent about its validator-set membership", message)
        self.assertIn("in_current_vset_p34", message)
        self.assertIn("--force", message)

    def test_an_unsynchronized_node_can_still_be_stopped(self):
        """It is not meeting its duties anyway, and refusing would make
        --force a habit, which is how the guard stops being read at all."""
        reading = {"node_status": "synchronization_by_archives", "timediff": 900,
                   "shards_timediff": 900}
        with patch("evernode.nodes.fetch_node_statistics", return_value=reading):
            nodes._refuse_while_validating(self.config, "stop")

    def test_an_unreachable_console_cannot_block_a_stop(self):
        # A node whose console is gone owes nothing that stopping can worsen.
        with patch(
            "evernode.nodes.fetch_node_statistics",
            side_effect=process.OperationError("container is not running"),
        ):
            nodes._refuse_while_validating(self.config, "stop")


class PendingBidGuardTests(unittest.TestCase):
    """A bid in an open election is not set membership yet, but can become it.

    The window between the bid and the published result lasts hours on
    mainnet. A node stopped in it can be elected and then miss its round.
    """

    NOW = 1_800_000_000

    def setUp(self):
        self.addCleanup(os.umask, os.umask(0o077))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        guard = patch.object(storage.NodeStateStore, "validate_state_root")
        guard.start()
        self.addCleanup(guard.stop)
        self.store = storage.NodeStateStore(temporary.name)
        self.config = make_node_configuration("validator01")
        self.keys = self.store.node_directory("validator01") / "node_cfg"
        self.keys.mkdir(parents=True)

    def write_keys(self, *election_ids):
        keys = [{"election_id": value, "validator_key_id": "k"} for value in election_ids]
        (self.keys / "config.json").write_text(json.dumps({"validator_keys": keys}))

    def pending(self, p36=None):
        reading = {"p36": p36} if p36 else {}
        with patch("evernode.nodes._run_console", return_value=reading) as console:
            value = nodes.pending_election(self.store, self.config, now=self.NOW)
        return value, console

    def test_a_key_for_a_round_not_started_is_a_pending_bid(self):
        self.write_keys(self.NOW - 900, self.NOW + 900)
        value, console = self.pending()
        self.assertEqual(value, self.NOW + 900)
        console.assert_called_once_with(self.config, "getconfig 36")

    def test_a_published_next_set_hands_over_to_set_membership(self):
        self.write_keys(self.NOW + 900)
        value, _ = self.pending(p36={"utime_since": self.NOW + 900})
        self.assertIsNone(value)

    def test_keys_of_started_rounds_are_not_a_bid(self):
        self.write_keys(self.NOW - 900, self.NOW)
        value, console = self.pending()
        self.assertIsNone(value)
        console.assert_not_called()

    def test_a_node_that_never_bid_has_no_key_file(self):
        value, console = self.pending()
        self.assertIsNone(value)
        console.assert_not_called()

    def test_stop_is_refused_with_the_round_and_the_override(self):
        with patch("evernode.nodes.pending_election", return_value=self.NOW + 900):
            with self.assertRaises(process.OperationError) as caught:
                nodes._refuse_with_pending_bid(self.store, self.config, "stop")
        message = str(caught.exception)
        self.assertIn("2027-01-15 08:15:00Z", message)
        self.assertIn("--force to stop", message)

    def test_an_unreachable_console_cannot_block_a_stop(self):
        with patch(
            "evernode.nodes.pending_election",
            side_effect=process.OperationError("container is not running"),
        ):
            nodes._refuse_with_pending_bid(self.store, self.config, "stop")


class UnknownNodeTests(unittest.TestCase):
    """A mistyped name must name the mistake, not report an internal errno."""

    def setUp(self):
        self.addCleanup(os.umask, os.umask(0o077))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        guard = patch.object(storage.NodeStateStore, "validate_state_root")
        guard.start()
        self.addCleanup(guard.stop)
        self.store = storage.NodeStateStore(temporary.name)

    def test_an_unknown_node_is_named_with_the_way_to_list_nodes(self):
        with self.assertRaises(process.OperationError) as caught:
            self.store.load_node_config("no-such-node")
        message = str(caught.exception)
        self.assertIn("No managed node named no-such-node", message)
        self.assertIn("evernode node list", message)
        self.assertNotIn(str(self.store.root), message)

    def test_the_election_lock_names_an_unknown_node_too(self):
        """Taken before the node is loaded, it must not report a missing folder."""
        with self.assertRaisesRegex(
            process.OperationError, "No managed node named no-such-node"
        ):
            with elections.hold_election_lock(self.store, "no-such-node"):
                pass


class ImageMaintenanceDryRunTests(unittest.TestCase):
    """A dry run must say what would stop the real run."""

    def setUp(self):
        self.addCleanup(os.umask, os.umask(0o077))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        guard = patch.object(storage.NodeStateStore, "validate_state_root")
        guard.start()
        self.addCleanup(guard.stop)
        self.store = storage.NodeStateStore(temporary.name)
        self.config = make_node_configuration("validator01")

    def blockers(self, *, schedule, running, **membership):
        reading = {"in_current_vset_p34": False, "in_next_vset_p36": False}
        reading.update(membership)
        status = {"container": "running" if running else "exited", "stats": reading}
        with patch("evernode.nodes.get_node_status", return_value=status), patch(
            "evernode.storage.NodeStateStore.load_node_config", return_value=self.config
        ), patch(
            "evernode.elections.node_election_cron_path"
        ) as cron:
            cron.return_value.exists.return_value = schedule
            return nodes.image_maintenance_blockers(self.store, "validator01")

    def test_an_installed_schedule_and_a_validating_node_are_both_reported(self):
        reported = self.blockers(schedule=True, running=True, in_current_vset_p34=True)
        self.assertEqual(len(reported), 2)
        self.assertIn("election schedule is installed", reported[0])
        self.assertIn("in the current validator set", reported[1])

    def test_a_stopped_node_without_a_schedule_is_not_blocked(self):
        self.assertEqual(self.blockers(schedule=False, running=False), [])

    def test_a_running_node_outside_both_sets_is_not_blocked(self):
        self.assertEqual(self.blockers(schedule=False, running=True), [])


class ElectionRunMaintenanceTests(unittest.TestCase):
    """A run that began before election stop still signs through the node, so
    maintenance that stops the node refuses until that run has finished."""

    def setUp(self):
        self.addCleanup(os.umask, os.umask(0o077))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        guard = patch.object(storage.NodeStateStore, "validate_state_root")
        guard.start()
        self.addCleanup(guard.stop)
        self.store = storage.NodeStateStore(temporary.name)
        (self.store.node_directory("validator01") / "elections").mkdir(parents=True)
        self.store.save_node_config(
            make_node_configuration("validator01", initialized=True, phase="console-ready")
        )
        # The lock the cron runner's flock holds for the whole run.
        running = elections.hold_election_lock(self.store, "validator01")
        running.__enter__()
        self.addCleanup(running.__exit__, None, None, None)

    def test_update_and_restart_refuse_before_stopping_the_node(self):
        record = {"image": "local/ever-node:new", "image_id": "sha256:" + "b" * 64}
        commands = {
            "update": lambda: nodes.update_node_image(self.store, "validator01", record),
            "restart": lambda: nodes.apply_node_lifecycle_operation(
                self.store, "validator01", "restart"
            ),
        }
        with (
            patch("evernode.nodes.stop_node") as stop,
            patch(
                "evernode.nodes.fetch_node_statistics",
                side_effect=process.OperationError("console unavailable"),
            ),
            patch("evernode.elections.node_election_cron_path") as cron,
        ):
            cron.return_value.exists.return_value = False
            for name, run in commands.items():
                with self.subTest(command=name):
                    with self.assertRaisesRegex(process.OperationError, "election lock"):
                        run()
        stop.assert_not_called()


class ImageUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        guard = patch.object(storage.NodeStateStore, "validate_state_root")
        guard.start()
        self.addCleanup(guard.stop)
        self.store = storage.NodeStateStore(temporary.name)
        (self.store.root / "nodes").mkdir(mode=0o700)
        self.store.node_directory("validator01").mkdir(mode=0o700)
        (self.store.node_directory("validator01") / "elections").mkdir(mode=0o700)
        self.store.save_node_config(
            make_node_configuration("validator01", initialized=True, phase="console-ready")
        )

    def test_an_image_remove_while_the_node_stops_cannot_take_the_replacement(self):
        """The review's case: removal used to succeed here, and the node was left
        stopped with a missing image configured."""
        replacement = {"image": "local/ever-node:new", "image_id": "sha256:" + "b" * 64, "key": "c" * 64}
        record = images.image_record_path(self.store, replacement["key"])
        record.parent.mkdir(parents=True)
        record.write_text("{}")
        outcome = []

        def remove_while_stopping(*_args, **_kwargs):
            try:
                images.remove_managed_image(self.store, replacement["image"])
                outcome.append("removed")
            except process.OperationError as exc:
                outcome.append(str(exc))

        with (
            patch("evernode.nodes.image_maintenance_blockers", return_value=[]),
            patch("evernode.images.find_image_record", return_value=replacement),
            patch("evernode.process.execute_command",
                  return_value=subprocess.CompletedProcess([], 0, "", "")),
            patch("evernode.nodes.stop_node", side_effect=remove_while_stopping),
            patch("evernode.nodes.start_node"),
            patch("evernode.nodes.fetch_node_statistics", return_value={}),
            patch("evernode.nodes.is_node_synchronized", return_value=True),
            patch("evernode.nodes.time.sleep"),
        ):
            nodes.update_node_image(self.store, "validator01", replacement)

        self.assertEqual(outcome, ["Image is referenced by: validator01"])
        self.assertTrue(record.exists())
        self.assertEqual(
            self.store.load_node_config("validator01")["image_id"], replacement["image_id"]
        )


class LogComponentTests(unittest.TestCase):
    """Every log component must name a file something actually writes."""

    def test_the_election_components_name_files_the_scripts_write(self):
        scripts = Path(__file__).resolve().parents[1] / "evernode/vendor/ever-validator/scripts"
        written = "".join(
            path.read_text()
            for path in scripts.glob("*.sh")
        ) + "".join(path.read_text() for path in scripts.glob("*.shinc"))
        source = (Path(__file__).resolve().parents[1] / "evernode/nodes.py").read_text()
        for component, filename in (
            ("elections", "transactions.log"),
            ("election-cron", "election-cron.log"),
        ):
            with self.subTest(component=component):
                self.assertIn(f'"{component}": "logs/validator/{filename}"', source)
        # The reference kit writes validator.log only from its own updater
        # cron, which this CLI never installs.
        self.assertNotIn('"logs/validator/validator.log"', source)
        self.assertIn("transactions.log", written)


class ElectedRoundTests(unittest.TestCase):
    """Which published validator sets hold one of this node's keys.

    ever-node keeps the node's keys in node_cfg/config.json with each key's
    ADNL address in base64; validator sets list that address in hex.
    """

    ADNL = ["a1" * 32, "b2" * 32, "c3" * 32]

    def setUp(self):
        self.addCleanup(os.umask, os.umask(0o077))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        guard = patch.object(storage.NodeStateStore, "validate_state_root")
        guard.start()
        self.addCleanup(guard.stop)
        self.store = storage.NodeStateStore(temporary.name)
        self.config = make_node_configuration("validator01")
        self.keys = self.store.node_directory("validator01") / "node_cfg"
        self.keys.mkdir(parents=True)

    def write_keys(self, *keys):
        entries = [
            {"election_id": election, "validator_key_id": "k",
             "validator_adnl_key_id": base64.b64encode(bytes.fromhex(adnl)).decode()}
            for election, adnl in keys
        ]
        (self.keys / "config.json").write_text(json.dumps({"validator_keys": entries}))

    @staticmethod
    def validator_set(since, *adnl):
        return {"utime_since": since, "utime_until": since + 600,
                "list": [{"public_key": "00", "adnl_addr": value} for value in adnl]}

    def test_keys_carry_their_adnl_address_in_hex(self):
        self.write_keys((100, self.ADNL[0]), (700, self.ADNL[1]))
        self.assertEqual(
            nodes.validator_keys(self.store, self.config),
            [{"election_id": 100, "adnl": self.ADNL[0]}, {"election_id": 700, "adnl": self.ADNL[1]}],
        )

    def test_a_key_without_a_readable_address_still_counts_for_its_election(self):
        (self.keys / "config.json").write_text(json.dumps({"validator_keys": [
            {"election_id": 100, "validator_adnl_key_id": "not base64!"},
            {"election_id": "100"},
        ]}))
        self.assertEqual(nodes.validator_keys(self.store, self.config), [{"election_id": 100, "adnl": None}])

    def test_a_node_that_never_bid_has_no_keys(self):
        self.assertIsNone(nodes.validator_keys(self.store, self.config))

    def test_each_set_holding_a_key_is_a_round(self):
        self.write_keys((1000, self.ADNL[0]), (1600, self.ADNL[1]))
        readings = {
            "getconfig 32": {"p32": self.validator_set(400, "ff" * 32)},
            "getconfig 34": {"p34": self.validator_set(1000, self.ADNL[0], "ee" * 32)},
            "getconfig 36": {"p36": self.validator_set(1600, self.ADNL[1].upper())},
        }
        with patch("evernode.nodes._run_console", side_effect=lambda config, command: readings[command]):
            rounds = nodes.elected_rounds(self.store, self.config)
        self.assertEqual(rounds, [
            {"round": "current", "utime_since": 1000, "utime_until": 1600},
            {"round": "next", "utime_since": 1600, "utime_until": 2200},
        ])

    def test_an_unpublished_next_set_is_skipped(self):
        self.write_keys((1000, self.ADNL[0]))
        readings = {"getconfig 32": {"p32": self.validator_set(400, self.ADNL[0])},
                    "getconfig 34": {"p34": self.validator_set(1000, "ee" * 32)},
                    "getconfig 36": {}}
        with patch("evernode.nodes._run_console", side_effect=lambda config, command: readings[command]):
            rounds = nodes.elected_rounds(self.store, self.config)
        self.assertEqual([item["round"] for item in rounds], ["previous"])

    def test_without_keys_no_set_is_read(self):
        with patch("evernode.nodes._run_console") as console:
            self.assertEqual(nodes.elected_rounds(self.store, self.config), [])
        console.assert_not_called()
